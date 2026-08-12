"""LLM entrant harness.

Every frontier-model entrant runs through this module each refresh. Two modes:

1. REAL: when an API key for the provider is present in the environment, the
   model is asked for a forecast through a uniform prompt and the reply is
   parsed into {"mean", "sd"}. Keys are read from env so they can live in
   GitHub Actions secrets (Settings > Secrets > Actions) or any other deploy
   platform's secret store; nothing is ever committed.

2. MOCK: when no key is configured (or the call fails), a deterministic
   placeholder forecast is filed instead: persistence plus a small
   model-specific offset, clearly labeled MOCK in the notes. This keeps the
   entrant slots, pages, and scoring pipeline fully exercised.

Three wire protocols cover all thirteen entered models, so no vendor SDKs are
needed:
  openai    - /chat/completions (OpenAI, xAI, and the Qwen/Kimi/GLM/MiniMax
              gateway, which all speak it)
  anthropic - /v1/messages
  gemini    - /models/{m}:generateContent

Model id and endpoint are both overridable per entrant without touching code:
  SSA_MODEL_GROK=grok-4.3   SSA_BASE_QWEN=https://my-gateway/compatible-mode/v1

Cost control: a forecast is only re-requested when its inputs changed. Every
filed forecast carries in=<hash of the prompt> in its notes; if the hash still
matches, the existing file is kept and no API call is made. So a round costs
one call per model per new observation, not one per refresh.
"""
import concurrent.futures
import hashlib
import json
import os
import re
import threading

import requests

# Reasoning depth is set as high as each provider allows, and the parameter is
# not portable -- getting it wrong is a 400, not a silent downgrade:
#   OpenAI     reasoning_effort; GPT-5.6 takes none/low/medium/high/xhigh
#              and rejects "max", so xhigh is the ceiling there
#   Anthropic  thinking {type: adaptive} + output_config {effort: "max"}. The
#              older {type: "enabled", budget_tokens: N} is REJECTED on Opus 5,
#              Opus 4.8, Sonnet 5 and Fable 5.
#   xAI        reasoning_effort, only low/medium/high; defaults to high and
#              cannot be disabled, so "high" is already the ceiling.
#   gateway    Kimi, GLM, MiniMax and Qwen ride one OpenAI-compatible gateway
#              whose effort support is undocumented, so nothing is sent.
# "max" is rejected by the GPT-5.6 models with an explicit list of what they do
# take: none/low/medium/high/xhigh. xhigh is their ceiling, so that is maximum
# effort here despite the value differing from Anthropic's.
OPENAI_MAX_EFFORT = {"reasoning_effort": "xhigh"}
ANTHROPIC_MAX_EFFORT = {"thinking": {"type": "adaptive"},
                        "output_config": {"effort": "max"}}
XAI_MAX_EFFORT = {"reasoning_effort": "high"}

# Temperature is deliberately never set. Current frontier models on OpenAI and
# Anthropic reject it outright, and elsewhere the provider default (~1.0) is
# what we want: a rerun is not meant to reproduce, the committed record of raw
# replies is.
# Kimi, GLM, MiniMax and Qwen are served by one OpenAI-compatible gateway.
# The public DashScope endpoint below carries only the Qwen family, so a
# deployment that enters the other three must point SSA_BASE_GATEWAY (or the
# per-entrant SSA_BASE_<ENTRANT>) at a gateway that serves them. Nothing here
# hardcodes a private host.
GATEWAY = "https://dashscope.aliyuncs.com/compatible-mode/v1"
GATEWAY_ENTRANTS = ("qwen-3.7", "qwen-3.8", "kimi", "glm", "minimax")

MODELS = {
    # --- OpenAI: all three GPT-5.6 variants -------------------------------
    "gpt-5.6-luna": {
        "env": "OPENAI_API_KEY", "name": "GPT-5.6 Luna", "api": "openai",
        "base": "https://api.openai.com/v1", "model": "gpt-5.6-luna",
        "params": OPENAI_MAX_EFFORT,
    },
    "gpt-5.6-sol": {
        "env": "OPENAI_API_KEY", "name": "GPT-5.6 Sol", "api": "openai",
        "base": "https://api.openai.com/v1", "model": "gpt-5.6-sol",
        "params": OPENAI_MAX_EFFORT,
    },
    "gpt-5.6-terra": {
        "env": "OPENAI_API_KEY", "name": "GPT-5.6 Terra", "api": "openai",
        "base": "https://api.openai.com/v1", "model": "gpt-5.6-terra",
        "params": OPENAI_MAX_EFFORT,
    },
    # --- Anthropic --------------------------------------------------------
    # Opus 4.8 rather than Opus 5: Opus 5's May 2026 cutoff sits so close to
    # the right edge of the data that entering it collapses the common backtest
    # window for every other model. 4.8 is a January 2026 cutoff, which costs
    # little capability and buys the whole window back.
    "claude-opus": {
        "env": "ANTHROPIC_API_KEY", "name": "Claude Opus 4.8", "api": "anthropic",
        "base": "https://api.anthropic.com/v1", "model": "claude-opus-4-8",
        "params": ANTHROPIC_MAX_EFFORT,
    },
    # Opus 5 runs alongside 4.8 rather than instead of it. Its May 2026 cutoff
    # leaves it a much shorter backtest window than the rest, which is a reason
    # to score it on its own window, not a reason to leave it out.
    "claude-opus-5": {
        "env": "ANTHROPIC_API_KEY", "name": "Claude Opus 5", "api": "anthropic",
        "base": "https://api.anthropic.com/v1", "model": "claude-opus-5",
        "params": ANTHROPIC_MAX_EFFORT,
    },
    "claude-sonnet": {
        "env": "ANTHROPIC_API_KEY", "name": "Claude Sonnet 5", "api": "anthropic",
        "base": "https://api.anthropic.com/v1", "model": "claude-sonnet-5",
        "params": ANTHROPIC_MAX_EFFORT,
    },
    "claude-fable": {
        "env": "ANTHROPIC_API_KEY", "name": "Claude Fable 5", "api": "anthropic",
        "base": "https://api.anthropic.com/v1", "model": "claude-fable-5",
        "params": ANTHROPIC_MAX_EFFORT,
    },
    # --- Google -----------------------------------------------------------
    # Pinned, never the `-latest` aliases: an alias that rolls forward
    # mid-season silently swaps the entrant, and scores from before and after
    # the swap are not comparable. (gemini-2.5-pro now 404s as "no longer
    # available to new users".)
    "gemini-pro": {
        "env": "GOOGLE_API_KEY", "name": "Gemini 3.1 Pro", "api": "gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-3.1-pro-preview",
    },
    "gemini-flash": {
        "env": "GOOGLE_API_KEY", "name": "Gemini 3.6 Flash", "api": "gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "model": "gemini-3.6-flash",
    },
    # --- xAI --------------------------------------------------------------
    "grok": {
        "env": "XAI_API_KEY", "name": "Grok 4.5", "api": "openai",
        "base": "https://api.x.ai/v1", "model": "grok-4.5",
        "params": XAI_MAX_EFFORT,
    },
    # --- Gateway-hosted (one OpenAI-compatible endpoint, one key) ----------
    "qwen-3.7": {
        "env": "DASHSCOPE_API_KEY", "name": "Qwen3.7 Max", "api": "openai",
        # The dated snapshot matching the recorded cutoff, not the floating
        # qwen3.7-max alias.
        "base": GATEWAY, "model": "qwen3.7-max-2026-05-20",
    },
    "qwen-3.8": {
        "env": "DASHSCOPE_API_KEY", "name": "Qwen3.8 Max", "api": "openai",
        "base": GATEWAY, "model": "qwen3.8-max",
    },
    # --- DeepSeek ---------------------------------------------------------
    "deepseek-pro": {
        "env": "DEEPSEEK_API_KEY", "name": "DeepSeek V4 Pro", "api": "openai",
        "base": "https://api.deepseek.com", "model": "deepseek-v4-pro",
    },
    "deepseek-flash": {
        "env": "DEEPSEEK_API_KEY", "name": "DeepSeek V4 Flash", "api": "openai",
        "base": "https://api.deepseek.com", "model": "deepseek-v4-flash",
    },
    "kimi": {
        "env": "DASHSCOPE_API_KEY", "name": "Kimi K3", "api": "openai",
        "base": GATEWAY, "model": "kimi/kimi-k3",
    },
    "glm": {
        "env": "DASHSCOPE_API_KEY", "name": "GLM-5.2", "api": "openai",
        "base": GATEWAY, "model": "glm-5.2",
    },
    "minimax": {
        "env": "DASHSCOPE_API_KEY", "name": "MiniMax M3", "api": "openai",
        "base": GATEWAY, "model": "MiniMax/MiniMax-M3",
    },
}

# No output ceiling is imposed. Thinking tokens count against any cap, so at
# max reasoning effort a small one truncates the reply before the model reaches
# its JSON; that fails to parse and falls back to a labelled MOCK -- a silent
# downgrade under a green workflow. OpenAI and Gemini are simply not sent a
# limit, which leaves the model's own maximum in force.
#
# Anthropic is the exception: max_tokens is a *required* field on the Messages
# API, so the model's advertised maximum is sent instead. It is a cap, not a
# spend; only tokens actually produced are billed.
ANTHROPIC_MAX_TOKENS = 128000   # max_tokens reported by /v1/models for Opus 4.8,
                                # Sonnet 5 and Fable 5 (1M input, 128k output)
# (connect, read). Every entrant runs at its provider's maximum reasoning
# effort, so a reply can be minutes of thinking before the first byte -- a
# 120s read timeout was simply shorter than the work being asked for, and it
# failed the same three Anthropic rounds on every refresh while the other
# seven succeeded. Nothing here is interactive, so the read budget is generous;
# the connect budget stays short so an unreachable host still fails fast
# instead of holding a worker for ten minutes.
TIMEOUT = (15, 600)

# Two prompt variants, differing only in how much of the series the model sees.
# Everything that defines *what number is being asked for* -- the pollster, the
# population, the question wording, the release schedule -- appears in both,
# because the resolver uses all of it. A detail the grader relies on and the
# prompt omits is not a hard question, it is an unfair one: no amount of
# reasoning recovers whether "approval" here means adults or registered voters,
# and those differ by several points.
HEADER = (
    "You are forecasting the next scheduled release of a public opinion tracker.\n"
    "Question: {question}\n"
    "Unit: {unit}\n"
    "How the tracker is measured: {methodology}\n"
    "Release schedule: {cadence}\n"
    "Scheduled release date: {release}\n"
)

# `none` is the default baseline condition: the question and nothing else, so
# the forecast comes entirely from what the model already believes about the
# series. It doubles as a contamination probe -- accuracy on a post-cutoff
# release with no history to reason from is not forecasting.
NO_HISTORY = "No history of this series is provided.\n"

WITH_HISTORY = (
    "Recent published values of this series (oldest first, one point per release):\n"
    "{history}\n"
)

# Not passed through .format(), so the braces are literal single braces here.
FOOTER = (
    "Give your predictive distribution over the value that will be released. "
    "Reply with exactly one JSON object and no other text:\n"
    '{"mean": <number>, "sd": <number>}\n'
    "sd is your standard deviation in the same unit and must be greater than 0."
)

# The superforecaster protocol, transplanted from the human forecasting
# literature: outside view before inside view, decomposition, then a pre-mortem
# against your own answer. The point is to measure what the *process* is worth
# on top of the model, so the steps are named and ordered rather than left to
# "think step by step" -- an instruction that lets each model do whatever it
# already does and measures nothing.
SUPERFC = (
    "Work through the following before answering, in this order.\n"
    "1. Outside view. What is the base rate here? What has this series done "
    "historically, how much does it move between releases, and what would a "
    "naive extrapolation predict?\n"
    "2. Inside view. What is specific to this release -- events, timing, "
    "anything that would move this particular number away from the base rate? "
    "Say how much each is worth, in the unit of the series.\n"
    "3. Pre-mortem. Assume your answer turns out badly wrong. Write the most "
    "likely reason, then correct for it.\n"
    "4. Calibrate. Your sd should be wide enough that the true value falls "
    "inside one sd about two thirds of the time. Check it against how much "
    "this series actually moves between releases.\n"
)

# The fixed corpus, rendered into the prompt. Framed as a digest with an
# explicit as-of, so a model knows the horizon it is reasoning over and cannot
# mistake the absence of an event for evidence it did not happen.
NEWS_BLOCK = (
    "Recent world events, from the Wikipedia Current Events portal, as the "
    "pages stood at {asof}. This is the same digest given to every entrant; "
    "nothing after {asof} is included.\n{news}\n"
)

VARIANTS = {"none": 0, "recent10": 10}
DEFAULT_VARIANT = "recent10"

# --- the elicitation axis --------------------------------------------------
#
# The conditions above vary *what the model is shown*; every other live
# benchmark varies the same thing, under names like closed-book versus
# web-enabled. The conditions below vary *how it is asked*, holding the
# information fixed, and that axis is the one this arena is for: in a social
# simulation the question "does role-playing a population beat asking for a
# number" is not a prompt-engineering detail, it is the scientific claim the
# whole silicon-sampling literature rests on.
#
#   persona   the model is not asked to forecast at all. It answers the real
#             survey instrument as each of twenty-four weighted respondents in
#             turn, and the pollster's own arithmetic turns those answers into
#             the number. This is what the industry actually sells, so a result
#             either way is worth having: if it does not beat asking directly,
#             the premise of the method is in question.
#   superfc   asked directly, but through the human forecasting protocol --
#             outside view first, then decomposition, then a pre-mortem. Tests
#             what the *process* is worth, separately from the model.
#   news      asked directly, plus a fixed news digest: the same corpus for
#             every entrant, built from the Wikipedia Current Events pages as
#             they stood at the lock. This is the auditable version of "give it
#             real-world information" -- one corpus, archived, reproducible,
#             and safe in the backtest because it is fetched by revision
#             timestamp rather than as the pages read today.
#   web       asked directly, with live search. Isolated from the rest because
#             it is the only condition whose fairness cannot be audited after
#             the fact; see WEB_VARIANTS below for why it is live-only.
#
# All three see the same ten-release history as `recent10`, so any difference
# between them is elicitation and not information.
# `web` is written and deliberately NOT in the season. It stays out until the
# fairness question is settled: nine of fifteen models can run it at all, so a
# leaderboard containing it compares six models against an arm they were never
# offered. The code, the capability table and the backtest refusal all remain
# below, so enabling it later is adding one string to this tuple.
ELICITATION_VARIANTS = ("persona", "superfc", "news")
for _v in ELICITATION_VARIANTS:
    VARIANTS[_v] = 10

# `web` is a working condition that is not in the season (see
# ELICITATION_VARIANTS). It stays registered here so the code path, its
# capability table and its backtest refusal stay live and tested rather than
# rotting into something that has to be rediscovered; it simply produces no
# entrants, so nothing runs it.
VARIANTS["web"] = 10

# Web search is a *prospective-only* condition, and the guard is not a
# preference. In a live round the answer does not exist anywhere at lock time,
# so search cannot leak it. In the backtest the answer has been published for
# months: a model searching the open web for "Michigan sentiment July 2026"
# reads the outcome and scores perfectly, which measures retrieval, not
# forecasting. There is no prompt that prevents this and no way to verify
# after the fact what a model retrieved, so the backtest refuses the condition
# outright rather than publishing a number nobody can defend.
WEB_VARIANTS = ("web",)


def assert_prospective(variant, where="the backtest"):
    """Raise if `variant` may only be run on rounds whose answer is unknown."""
    if variant in WEB_VARIANTS:
        raise ValueError(
            f"variant {variant!r} cannot run in {where}: the outcome is "
            "already published, so live search reads the answer instead of "
            "forecasting it. It is a live-round condition only.")

# Season 0 runs both conditions, once per release, and scores them as separate
# entrants -- which is what they are. `recent10` shows the last ten releases,
# the same history the nulls read, so it is the like-for-like comparison
# against persistence. `none` shows the question and nothing else, which makes
# it two things at once: the ablation that isolates how much the series history
# is worth, and a contamination probe, because accuracy on a post-cutoff
# release with no history to reason from is not forecasting.
#
# Repeated sampling is deliberately absent. At the providers' default
# temperature a rerun does not reproduce, so the committed record of raw
# replies is the reproducibility mechanism, not a re-run.
SEASON_VARIANTS = ("recent10", "none")

# Entrant id suffix per condition. The default condition keeps the bare model
# name so existing forecasts, entrant records and leaderboard rows stay valid.
VARIANT_SUFFIX = {"recent10": "", "none": "-zeroshot",
                  "persona": "-persona", "superfc": "-superfc",
                  "news": "-news", "web": "-web"}

# Which models run the elicitation conditions. Every active model, because the
# whole matrix costs about $43 for a full season and a three-model subset would
# leave the axis unable to say whether an effect is real or one vendor's quirk.
#
# The reason to narrow it is cost, not correctness: `persona` is one call per
# simulated respondent per round, roughly two hundred times a normal entrant,
# so it dominates the bill. Narrow this tuple to trade coverage for money, and
# run tools/estimate_arms.py first -- it calls nothing and prints the total.
#
# The web condition self-restricts to WEB_CAPABLE regardless of what is listed
# here, so six models simply have no web arm.
# Defined as a function rather than a constant because PENDING_ACTIVATION is
# declared further down; a constant here read it before it existed.
def elicitation_models():
    return tuple(active_models())


def elicitation_entrants(variants=ELICITATION_VARIANTS, models=None):
    """(entrant_id, model_key, variant) for the how-it-is-asked conditions.

    The web condition is emitted only for models whose vendor hosts a search
    tool, so a roster never contains an entrant that is guaranteed to fail.
    Everything else runs anywhere.
    """
    models = elicitation_models() if models is None else models
    out = []
    for v in variants:
        for m in models:
            if m not in MODELS:
                continue
            if v in WEB_VARIANTS and m not in WEB_CAPABLE:
                continue
            out.append((m + VARIANT_SUFFIX[v], m, v))
    return out


# Entered in MODELS but not run: the gateway rejects the prefixed namespace
# these two live in ("The product is not activated"), and K3 and M3 exist only
# there -- the activated bare names top out at kimi-k2.6 and MiniMax-M2.5.
# Their config and cutoff rows are kept so re-enabling is deleting a line here.
PENDING_ACTIVATION = ("kimi", "minimax")


def active_models():
    return [m for m in MODELS if m not in PENDING_ACTIVATION]


def season_entrants():
    """(entrant_id, model_key, variant) for every condition the arena runs."""
    return [(m + VARIANT_SUFFIX[v], m, v)
            for v in SEASON_VARIANTS for m in active_models()]


def resolve(entrant_id):
    """Entrant id -> (model_key, variant). Raises on an unknown id."""
    for suffix, variant in sorted(
            ((s, v) for v, s in VARIANT_SUFFIX.items()),
            key=lambda x: -len(x[0])):          # longest suffix first
        if suffix and entrant_id.endswith(suffix):
            model = entrant_id[:-len(suffix)]
            if model in MODELS:
                return model, variant
    if entrant_id in MODELS:
        return entrant_id, DEFAULT_VARIANT
    raise KeyError(f"unknown entrant id: {entrant_id!r}")


def _env_suffix(entrant):
    return re.sub(r"[^A-Z0-9]", "_", entrant.upper())


def model_id(entrant):
    """Provider-side model name, overridable via SSA_MODEL_<MODEL>.

    Accepts either a model key or a full entrant id; the condition suffix does
    not change which model answers, so both resolve to the same name.
    """
    model, _ = resolve(entrant)
    return (os.environ.get("SSA_MODEL_" + _env_suffix(model))
            or MODELS[model]["model"])


def base_url(entrant):
    """API base, overridable via SSA_BASE_<ENTRANT>.

    Needed for self-hosted gateways and regional endpoints: a DashScope or
    Azure deployment speaks the same OpenAI-compatible protocol on a different
    host, so only the base differs.

    Resolution order is per-entrant override, then the shared gateway variable
    for the models that share one deployment, then the built-in default. The
    shared variable exists because those models are one host: setting several
    identical secrets invites all but one of them to drift.

    `SSA_BASE_QWEN` is accepted as that shared variable alongside the clearer
    `SSA_BASE_GATEWAY`. It is the name the secret was actually provisioned
    under, and renaming the lookup without renaming the secret is not a no-op:
    it drops the override, and every gateway model silently falls back to the
    public default host. That happened -- it routed three entrants away from
    the configured gateway and invalidated their whole backtest cache, since
    `call_identity` (and therefore the cache key) contains the base URL.
    """
    model, _ = resolve(entrant)
    shared = ((os.environ.get("SSA_BASE_GATEWAY")
               or os.environ.get("SSA_BASE_QWEN"))
              if model in GATEWAY_ENTRANTS else None)
    return (os.environ.get("SSA_BASE_" + _env_suffix(model))
            or shared or MODELS[model]["base"]).rstrip("/")


def has_key(entrant):
    model, _ = resolve(entrant)
    return bool(os.environ.get(MODELS[model]["env"]))


def build_prompt(r, history, variant=DEFAULT_VARIANT, news=None):
    """The exact text an entrant sees.

    `history` is the strictly pre-lock series the round's baselines were built
    from, so entrants and nulls read the same data. `variant` selects how much
    of it is shown; see VARIANTS.

    Methodology and cadence come from the round when present and fall back to
    the series registry, so a round definition never has to restate them.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown prompt variant {variant!r}; "
                         f"known: {sorted(VARIANTS)}")
    meta = {}
    if r.get("series"):
        try:
            from . import series as series_registry
            meta = series_registry.describe(r["series"])
        except (ImportError, KeyError):
            meta = {}

    head = HEADER.format(
        question=r.get("question") or meta.get("question", ""),
        unit=r.get("unit") or meta.get("unit", ""),
        methodology=r.get("methodology") or meta.get("methodology", "not stated"),
        cadence=r.get("cadence") or meta.get("cadence", "not stated"),
        release=r["release_at"][:10])

    n = VARIANTS[variant]
    if n == 0:
        body = NO_HISTORY
    else:
        pts = (history or [])[-n:]
        lines = "\n".join(f"  {p['date']}: {p['value']}" for p in pts) or "  (none)"
        body = WITH_HISTORY.format(history=lines)
    # `web` differs from recent10 in the request, not the text: the search tool
    # is attached per provider in call_provider. Keeping the prompt identical is
    # what makes the comparison an information comparison.
    protocol = SUPERFC if variant == "superfc" else ""
    digest = ""
    if variant == "news":
        if not news or not news.get("text"):
            raise ValueError(
                "the news condition needs a digest; refusing to file it as an "
                "ordinary forecast, which would silently make it a duplicate "
                "of recent10 under a different entrant name")
        digest = NEWS_BLOCK.format(asof=news["asof"], news=news["text"])
    return head + body + digest + protocol + FOOTER


# A respondent is being interviewed, not consulted. The framing says nothing
# about forecasts, releases, dates or aggregates, because a persona told it is
# feeding a prediction answers as an analyst wearing a costume -- which is the
# very thing this condition exists to be compared against. Everything the
# round knows and the respondent would not know is withheld here on purpose;
# that asymmetry is the experiment, not an oversight.
PERSONA_HEADER = (
    "You are answering a public opinion survey as the person described below. "
    "Answer the way that person would answer, not the way you would.\n\n"
    "{persona}\n\n"
    "Answer honestly and in character. Do not explain, hedge, or mention that "
    "you are playing a role.\n"
)

PERSONA_FOOTER = (
    "Reply with exactly one JSON object and no other text, using one of the "
    "listed options for each key:\n{shape}"
)


def build_persona_prompt(persona, spec):
    """What one simulated respondent is asked.

    `spec` is the series' `survey` block: the real instrument, item by item.
    """
    from . import personas
    lines, shape = [], []
    for item in spec["items"]:
        opts = " / ".join(item["options"])
        lines.append(f"- {item['key']}: {item['text']}\n  Options: {opts}")
        shape.append(f'"{item["key"]}": "<{opts}>"')
    return (PERSONA_HEADER.format(persona=personas.describe(persona))
            + "\nQuestions:\n" + "\n".join(lines) + "\n\n"
            + PERSONA_FOOTER.format(shape="{" + ", ".join(shape) + "}"))


def parse_survey_reply(text, spec):
    """One respondent's answers, or raise. Options are matched case-insensitively
    and unlisted answers are rejected rather than coerced -- a respondent who
    answered something else did not answer the question, and silently mapping
    it to the nearest option would put words in their mouth."""
    obj = _first_json_object(text)
    out = {}
    for item in spec["items"]:
        raw = obj.get(item["key"])
        if not isinstance(raw, str):
            raise ValueError(f"no answer for {item['key']!r} in reply")
        match = next((o for o in item["options"]
                      if o.lower() == raw.strip().lower()), None)
        if match is None:
            raise ValueError(
                f"{item['key']}: {raw!r} is not one of {item['options']}")
        out[item["key"]] = match
    return out


def call_identity(entrant):
    """What actually determines a reply: the model *and* the endpoint serving it.

    Both cache keys are built on this. Two gateways can serve different weights
    under the same model name, so a cache keyed on the name alone would reuse a
    forecast the current endpoint never produced.
    """
    return f"{model_id(entrant)} @ {base_url(entrant)}"


def prompt_hash(entrant, prompt):
    return hashlib.sha256(
        (call_identity(entrant) + "\n" + prompt).encode()).hexdigest()[:12]


# --- provider calls --------------------------------------------------------

# Concurrency is capped per provider, not just globally. A single pool lets one
# slow vendor hold every slot while fast ones idle, and it aims the whole burst
# at whichever provider happens to have the most entrants -- five of the fifteen
# models sit behind one gateway. Per-provider limits let the global worker count
# rise without any one vendor seeing a spike.
PROVIDER_LIMIT = int(os.environ.get("SSA_PROVIDER_LIMIT", "12"))
_provider_locks = {}
_locks_guard = threading.Lock()


def _provider_key(entrant):
    """What counts as one provider for rate-limiting: the endpoint host, so the
    four gateway-hosted models share a budget rather than getting one each."""
    model, _ = resolve(entrant)
    base = base_url(entrant)
    host = base.split("//", 1)[-1].split("/", 1)[0]
    return f"{MODELS[model]['env']}@{host}"


def _provider_slot(entrant):
    key = _provider_key(entrant)
    with _locks_guard:
        sem = _provider_locks.get(key)
        if sem is None:
            sem = _provider_locks[key] = threading.BoundedSemaphore(PROVIDER_LIMIT)
    return sem


# Server-side search, per wire protocol. Each vendor hosts the tool and runs
# the searches itself, so the harness stays three protocols wide and gains no
# scraper. Dated tool versions are pinned for the same reason model ids are: a
# tool that changes behaviour mid-season silently changes the condition.
WEB_TOOLS = {
    "anthropic": {"tools": [{"type": "web_search_20260209",
                             "name": "web_search"}]},
    "openai": {"tools": [{"type": "web_search"}]},
    "gemini": {"tools": [{"google_search": {}}]},
}

# Hosted search is a *vendor* feature, not a property of the wire protocol, and
# conflating the two is a trap this nearly fell into: Grok, both Qwens, both
# DeepSeeks and GLM all speak OpenAI-compatible chat completions, so dispatching
# on `api` alone would have sent OpenAI's hosted web_search tool to five hosts
# that do not serve it. The good outcome there is a 400. The bad one is a host
# that accepts unknown fields and ignores them, which yields a "web" entrant
# whose prompt and answer are identical to its closed-book twin -- a published
# comparison between two arms that were never different.
#
# So the capability is declared per model, and a model without it is refused by
# name rather than attempted.
WEB_CAPABLE = frozenset({
    "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra",       # OpenAI hosted tool
    "claude-opus", "claude-opus-5", "claude-sonnet", "claude-fable",
    "gemini-pro", "gemini-flash",                          # google_search
})


def call_provider(entrant, prompt, with_usage=False, variant=None):
    """One completion.

    Returns the reply text, or (text, usage) when with_usage is set. `usage` is
    the provider's own token report normalised to {input_tokens, output_tokens,
    thinking_tokens}, so a run's cost is measured rather than estimated. It is
    None for providers that report nothing.

    `variant` only matters where the condition changes the *request* rather
    than the prompt, which today means attaching the provider's search tool.
    """
    model, variant_of_id = resolve(entrant)
    variant = variant or variant_of_id
    cfg = MODELS[model]
    key = os.environ[cfg["env"]]
    mid = model_id(entrant)
    base = base_url(entrant)
    api = cfg["api"]
    fn = {"openai": _call_openai, "anthropic": _call_anthropic,
          "gemini": _call_gemini}.get(api)
    if fn is None:
        raise ValueError("unknown api: " + api)
    if variant in WEB_VARIANTS:
        extra = WEB_TOOLS.get(api) if model in WEB_CAPABLE else None
        if extra is None:
            raise ValueError(
                f"{entrant} ({model}) has no vendor-hosted search, so it "
                "cannot run the web condition. Speaking the OpenAI protocol is "
                "not the same as serving OpenAI's tools; add the model to "
                "WEB_CAPABLE only once its own endpoint is confirmed to run "
                "the search server-side.")
        cfg = dict(cfg)
        cfg["params"] = dict(cfg.get("params") or {}, **extra)
    with _provider_slot(entrant):
        text, usage = fn(cfg, base, key, mid, prompt)
    return (text, usage) if with_usage else text


def _usage(data):
    """Provider token reports, normalised. Each vendor names these its own way."""
    u = data.get("usage") or data.get("usageMetadata") or {}
    ino = u.get("input_tokens") or u.get("prompt_tokens") or u.get("promptTokenCount")
    out = (u.get("output_tokens") or u.get("completion_tokens")
           or u.get("candidatesTokenCount"))
    think = ((u.get("output_tokens_details") or {}).get("thinking_tokens")
             or (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
             or u.get("thoughtsTokenCount"))
    if ino is None and out is None:
        return None
    return {"input_tokens": ino, "output_tokens": out, "thinking_tokens": think}


def _check(r, what):
    """Fail with the provider's own explanation attached.

    `raise_for_status` discards the response body, which is exactly where the
    reason lives -- an unsupported parameter, a model name this endpoint does
    not serve, a quota. Without it every misconfiguration looks like a bare 400.
    """
    if r.status_code >= 400:
        detail = " ".join((r.text or "").split())[:400]
        raise RuntimeError(f"{what} HTTP {r.status_code}: {detail}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{what} returned non-JSON: "
                           f"{' '.join((r.text or '').split())[:200]}")


def _pluck(data, path, what):
    """Walk a response path, reporting the actual payload when it is not there."""
    cur = data
    for step in path:
        try:
            cur = cur[step]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(
                f"{what}: no {'.'.join(map(str, path))} in reply; got "
                f"{json.dumps(data)[:300]}")
    return cur


# Where "the text" lives, across endpoints that all claim OpenAI compatibility.
# Self-hosted and regional gateways are routinely compatible on the request side
# and not on the response side -- an Aliyun MaaS deployment answers a correct
# /chat/completions call with a flat {"finish_reason", "text"}. Reading a few
# known shapes beats making the caller run a different protocol per host.
_TEXT_PATHS = (
    ("choices", 0, "message", "content"),   # OpenAI chat completions
    ("choices", 0, "text"),                 # legacy completions
    ("output", "choices", 0, "message", "content"),  # DashScope, result_format=message
    ("output", "text"),                     # DashScope native default
    ("text",),                              # Aliyun MaaS compatible-mode
)


def _extract_text(data, what):
    for path in _TEXT_PATHS:
        cur = data
        for step in path:
            try:
                cur = cur[step]
            except (KeyError, IndexError, TypeError):
                cur = None
                break
        if isinstance(cur, str) and cur.strip():
            return cur
    raise RuntimeError(f"{what}: no text found in reply; got "
                       f"{json.dumps(data)[:300]}")


def _call_openai(cfg, base, key, mid, prompt):
    # No max_tokens: the model's own ceiling applies. A caller can still set one
    # through cfg["params"], and the rename retry below covers that case.
    body = {"model": mid, "messages": [{"role": "user", "content": prompt}]}
    body.update(cfg.get("params") or {})
    url = base + "/chat/completions"
    r = requests.post(url, headers={"Authorization": "Bearer " + key},
                      json=body, timeout=TIMEOUT)
    # Newer OpenAI models replaced max_tokens with max_completion_tokens and
    # reject the old name outright. Retry once on the rename rather than make
    # every caller know which vintage its model is.
    if (r.status_code == 400 and "max_completion_tokens" in (r.text or "")
            and "max_tokens" in body):
        body["max_completion_tokens"] = body.pop("max_tokens")
        r = requests.post(url, headers={"Authorization": "Bearer " + key},
                          json=body, timeout=TIMEOUT)
    data = _check(r, f"{mid} @ {base}")
    return _extract_text(data, mid), _usage(data)


def _call_anthropic(cfg, base, key, mid, prompt):
    """Streamed, because at maximum effort these replies outlast one request.

    Raising the read timeout to 600s fixed two of the three rounds that were
    failing every refresh; the third then began coming back as
    `RemoteDisconnected` and, once, as a genuine 600s timeout. A single
    buffered request that takes ten minutes to produce its first byte is
    exactly what Anthropic asks callers to stream instead: the connection has
    nothing on it for the whole thinking phase, and something between here and
    there closes it.

    Streaming keeps bytes moving, so the read timeout applies between events
    rather than to the whole reply, and a long think no longer looks like a
    dead connection. Nothing about the request the model sees changes, so the
    backtest cache -- keyed on (call identity, prompt) -- stays valid.
    """
    body = {"model": mid, "max_tokens": ANTHROPIC_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True}
    body.update(cfg.get("params") or {})
    what = f"{mid} @ {base}"
    r = requests.post(base + "/messages",
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                      json=body, timeout=TIMEOUT, stream=True)
    if r.status_code >= 400:
        # The body still carries the provider's explanation; read it before
        # raising, since a streamed error response is otherwise discarded.
        raise RuntimeError(f"{what} HTTP {r.status_code}: "
                           f"{' '.join((r.text or '').split())[:400]}")

    text, usage, stop = [], {}, None
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue                      # blank separators and `event:` lines
        payload = raw[5:].strip()
        if not payload:
            continue
        try:
            ev = json.loads(payload)
        except ValueError:
            continue
        kind = ev.get("type")
        if kind == "error":
            err = ev.get("error") or {}
            raise RuntimeError(f"{what} stream error: "
                               f"{err.get('type')}: {err.get('message')}")
        if kind == "message_start":
            msg = ev.get("message") or {}
            usage.update(msg.get("usage") or {})
        elif kind == "content_block_delta":
            d = ev.get("delta") or {}
            # thinking_delta carries the reasoning, which is not the answer
            if d.get("type") == "text_delta":
                text.append(d.get("text") or "")
        elif kind == "message_delta":
            usage.update(ev.get("usage") or {})
            stop = (ev.get("delta") or {}).get("stop_reason", stop)

    if stop == "refusal":
        raise RuntimeError("model declined the request")
    out = "".join(text)
    if not out.strip():
        raise RuntimeError(f"{what}: stream ended with no text "
                           f"(stop_reason={stop})")
    return out, _usage({"usage": usage})


def _call_gemini(cfg, base, key, mid, prompt):
    # No maxOutputTokens: leave the model's own ceiling in force.
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    params = dict(cfg.get("params") or {})
    # `tools` is a sibling of generationConfig, not a member of it. Nesting it
    # is accepted and silently ignored, which would have produced a "web"
    # condition that never searched and a comparison that measured nothing.
    for root_key in ("tools", "toolConfig"):
        if root_key in params:
            body[root_key] = params.pop(root_key)
    if params:
        body["generationConfig"] = params
    url = f"{base}/models/{mid}:generateContent"
    r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=TIMEOUT)
    data = _check(r, f"{mid} @ {base}")
    parts = _pluck(data, ("candidates", 0, "content", "parts"), mid)
    return "".join(p.get("text", "") for p in parts), _usage(data)


# --- parsing ---------------------------------------------------------------

def _first_json_object(text):
    m = re.search(r"\{[^{}]*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    return json.loads(m.group(0))


def parse_forecast(text):
    """Pull {"mean": .., "sd": ..} out of a model reply. Raises on anything
    that would not survive the submission schema."""
    obj = _first_json_object(text)
    mean, sd = float(obj["mean"]), float(obj["sd"])
    if not (mean == mean and sd == sd):  # NaN
        raise ValueError("non-finite forecast")
    if not (0 < sd <= 50):
        raise ValueError(f"sd out of schema range: {sd}")
    if abs(mean) > 1000:
        raise ValueError(f"implausible mean: {mean}")
    return {"mean": round(mean, 2), "sd": round(sd, 2)}


# A failed provider call used to become a labelled placeholder, which kept the
# pages populated at the cost of hiding the failure: a wrong model name or a
# rejected parameter produced a green workflow and an arena quietly full of
# fabricated forecasts. Failures now raise. Set SSA_ALLOW_MOCK=1 to restore the
# old behaviour for local pipeline work where no keys are configured.
ALLOW_MOCK = os.environ.get("SSA_ALLOW_MOCK") == "1"


# --- mock ------------------------------------------------------------------

def mock_forecast(entrant, round_id, persistence_mean, persistence_sd):
    """Deterministic placeholder: persistence + a stable per-(model, round)
    offset in [-1.5, +1.5] points, slightly wider uncertainty."""
    h = hashlib.sha256(f"{entrant}:{round_id}".encode()).digest()
    offset = (h[0] / 255.0) * 3.0 - 1.5
    widen = 1.0 + (h[1] / 255.0) * 0.8
    return {
        "mean": round(persistence_mean + offset, 2),
        "sd": round(max(persistence_sd * widen, 1.0), 2),
    }


# --- the persona condition -------------------------------------------------

# How many of the panel may fail to answer before the aggregate is refused. A
# poll that lost a third of its respondents is not a poll, and quietly
# publishing a share of whoever happened to reply would hide exactly the
# failure that matters -- a model that refuses to role-play certain personas
# does not produce a representative panel, it produces a biased one.
PERSONA_MIN_RESPONSE = 0.75


def forecast_persona(entrant, r, history=None, previous=None):
    """A poll, simulated: ask every persona the real instrument, then aggregate.

    One call per respondent, run concurrently under the same per-provider limit
    as everything else. The model never sees the series, the release date or
    the fact that a forecast is wanted -- only a person and a question. The
    number comes out of the pollster\'s arithmetic in `personas`, not out of
    the model, which is the whole point of the condition.

    The input hash covers the persona prompts *and* the panel, so a change to
    either correctly misses the cache and re-runs.
    """
    from . import personas, series as series_registry

    spec = series_registry.survey(r["series"])
    if spec is None:
        raise RuntimeError(
            f"{entrant}: series {r['series']!r} has no survey instrument, so "
            "there is no honest question to put to a respondent. Register one "
            "in ssa/series.py or leave this series out of the persona arm.")

    panel = personas.panel()
    weights = personas.weights_for(spec.get("population"))
    prompts = {p["id"]: build_persona_prompt(p, spec) for p in panel}
    # One hash over the whole instrument, so adding a persona or reordering the
    # panel is a different question set and re-runs rather than reusing.
    ih = prompt_hash(entrant, "\n\n".join(prompts[p["id"]] for p in panel))

    if previous and f"in={ih}" in (previous.get("notes") or "") \
            and not (previous.get("notes") or "").startswith("MOCK"):
        return previous

    if not has_key(entrant):
        raise RuntimeError(
            f"{entrant}: no {MODELS[resolve(entrant)[0]]['env']} in the "
            "environment; the persona condition never files a placeholder.")

    answers, failures = {}, []
    lock = threading.Lock()

    def ask(p):
        pid = p["id"]
        try:
            reply = call_provider(entrant, prompts[pid], variant="persona")
            parsed = parse_survey_reply(reply, spec)
        except Exception as e:                 # noqa: BLE001 - collected below
            with lock:
                failures.append(f"{pid}: {type(e).__name__}: {e}")
            return
        with lock:
            answers[pid] = parsed

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(panel), PROVIDER_LIMIT)) as ex:
        list(ex.map(ask, panel))

    responded = sum(weights[pid] for pid in answers)
    if responded < PERSONA_MIN_RESPONSE:
        raise RuntimeError(
            f"{entrant} ({model_id(entrant)}) on {r['round_id']}: only "
            f"{responded:.0%} of the weighted panel answered, below the "
            f"{PERSONA_MIN_RESPONSE:.0%} floor. A panel this incomplete is "
            f"biased, not merely small. First failures: {failures[:3]}")

    mean = personas.aggregate(spec["aggregate"], answers, weights)
    sd = personas.sd_for(weights, history, scale=spec.get("se_scale", 1.0))
    note = (f"{model_id(entrant)}, harness v1, variant=persona, "
            f"{len(answers)}/{len(panel)} respondents, "
            f"{responded:.0%} of panel weight, "
            f"aggregate={spec['aggregate']}; in={ih}")
    return {
        "round_id": r["round_id"],
        "entrant": entrant,
        "topline": {"mean": round(mean, 2), "sd": round(sd, 2)},
        "notes": note[:500],
    }


# --- entry point -----------------------------------------------------------

def forecast(entrant, r, history=None, previous=None, variant=None,
             news=None):
    """One forecast dict for a round definition with baselines attached.

    `previous` is the forecast already on disk for this (round, entrant), if
    any. When its recorded input hash matches the prompt we would send now,
    it is returned unchanged and no API call is made. The variant is part of
    the prompt, so changing it correctly misses the cache.
    """
    per = r["baselines"]["persistence"]
    # The condition is carried by the entrant id, so a caller cannot file a
    # forecast under one entrant while prompting for another.
    variant = variant or resolve(entrant)[1]
    if variant == "persona":
        return forecast_persona(entrant, r, history, previous)
    prompt = build_prompt(r, history, variant, news=news)
    ih = prompt_hash(entrant, prompt)

    if previous and f"in={ih}" in (previous.get("notes") or "") \
            and not (previous.get("notes") or "").startswith("MOCK"):
        return previous

    if not has_key(entrant):
        if not ALLOW_MOCK:
            raise RuntimeError(
                f"{entrant}: no {MODELS[resolve(entrant)[0]]['env']} in the "
                "environment. Set the key, or set SSA_ALLOW_MOCK=1 to file a "
                "labelled placeholder instead.")
        top = mock_forecast(entrant, r["round_id"], per["mean"], per["sd"])
        note = ("MOCK: no API key configured; deterministic placeholder, "
                f"replaced by real output once keys are added; in={ih}")
    else:
        try:
            top = parse_forecast(call_provider(entrant, prompt))
            note = (f"{model_id(entrant)}, harness v1, variant={variant}, "
                    f"1 sample; in={ih}")
        except Exception as e:
            if not ALLOW_MOCK:
                raise RuntimeError(
                    f"{entrant} ({model_id(entrant)}) failed on "
                    f"{r['round_id']}: {type(e).__name__}: {e}") from e
            top = mock_forecast(entrant, r["round_id"], per["mean"], per["sd"])
            note = f"MOCK: {model_id(entrant)} call failed ({type(e).__name__}); in={ih}"
    return {
        "round_id": r["round_id"],
        "entrant": entrant,
        "topline": top,
        "notes": note[:500],
    }
