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
import hashlib
import json
import os
import re

import requests

# Reasoning depth is set as high as each provider allows, and the parameter is
# not portable -- getting it wrong is a 400, not a silent downgrade:
#   OpenAI     reasoning_effort, ladder none/low/medium/high/xhigh/max
#   Anthropic  thinking {type: adaptive} + output_config {effort: "max"}. The
#              older {type: "enabled", budget_tokens: N} is REJECTED on Opus 5,
#              Opus 4.8, Sonnet 5 and Fable 5.
#   xAI        reasoning_effort, only low/medium/high; defaults to high and
#              cannot be disabled, so "high" is already the ceiling.
#   gateway    Kimi, GLM, MiniMax and Qwen ride one OpenAI-compatible gateway
#              whose effort support is undocumented, so nothing is sent.
OPENAI_MAX_EFFORT = {"reasoning_effort": "max"}
ANTHROPIC_MAX_EFFORT = {"thinking": {"type": "adaptive"},
                        "output_config": {"effort": "max"}}
XAI_MAX_EFFORT = {"reasoning_effort": "high"}

# Temperature is deliberately never set. Current frontier models on OpenAI and
# Anthropic reject it outright, and elsewhere the provider default (~1.0) is
# what we want: a rerun is not meant to reproduce, the committed record of raw
# replies is.
GATEWAY = "https://dashscope.aliyuncs.com/compatible-mode/v1"

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
    "qwen": {
        "env": "DASHSCOPE_API_KEY", "name": "Qwen3.7 Max", "api": "openai",
        "base": GATEWAY, "model": "qwen3.7-max",
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
TIMEOUT = 120

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

VARIANTS = {"none": 0, "recent10": 10}
DEFAULT_VARIANT = "recent10"

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
VARIANT_SUFFIX = {"recent10": "", "none": "-zeroshot"}


def season_entrants():
    """(entrant_id, model_key, variant) for every condition the arena runs."""
    return [(m + VARIANT_SUFFIX[v], m, v)
            for v in SEASON_VARIANTS for m in MODELS]


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
    host, so only the base differs. Point an entrant at any endpoint that
    speaks its `api` protocol without touching code.
    """
    model, _ = resolve(entrant)
    return (os.environ.get("SSA_BASE_" + _env_suffix(model))
            or MODELS[model]["base"]).rstrip("/")


def has_key(entrant):
    model, _ = resolve(entrant)
    return bool(os.environ.get(MODELS[model]["env"]))


def build_prompt(r, history, variant=DEFAULT_VARIANT):
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
    return head + body + FOOTER


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

def call_provider(entrant, prompt):
    """One completion. Returns the model's raw reply text."""
    model, _ = resolve(entrant)
    cfg = MODELS[model]
    key = os.environ[cfg["env"]]
    mid = model_id(entrant)
    base = base_url(entrant)
    api = cfg["api"]
    if api == "openai":
        return _call_openai(cfg, base, key, mid, prompt)
    if api == "anthropic":
        return _call_anthropic(cfg, base, key, mid, prompt)
    if api == "gemini":
        return _call_gemini(cfg, base, key, mid, prompt)
    raise ValueError("unknown api: " + api)


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
    return _extract_text(data, mid)


def _call_anthropic(cfg, base, key, mid, prompt):
    body = {"model": mid, "max_tokens": ANTHROPIC_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}]}
    body.update(cfg.get("params") or {})
    r = requests.post(base + "/messages",
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                      json=body, timeout=TIMEOUT)
    data = _check(r, f"{mid} @ {base}")
    if data.get("stop_reason") == "refusal":
        raise RuntimeError("model declined the request")
    # content is a list of blocks; thinking blocks come first and carry no text
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")


def _call_gemini(cfg, base, key, mid, prompt):
    # No maxOutputTokens: leave the model's own ceiling in force.
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if cfg.get("params"):
        body["generationConfig"] = dict(cfg["params"])
    url = f"{base}/models/{mid}:generateContent"
    r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=TIMEOUT)
    data = _check(r, f"{mid} @ {base}")
    parts = _pluck(data, ("candidates", 0, "content", "parts"), mid)
    return "".join(p.get("text", "") for p in parts)


# --- parsing ---------------------------------------------------------------

def parse_forecast(text):
    """Pull {"mean": .., "sd": ..} out of a model reply. Raises on anything
    that would not survive the submission schema."""
    m = re.search(r"\{[^{}]*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    obj = json.loads(m.group(0))
    mean, sd = float(obj["mean"]), float(obj["sd"])
    if not (mean == mean and sd == sd):  # NaN
        raise ValueError("non-finite forecast")
    if not (0 < sd <= 50):
        raise ValueError(f"sd out of schema range: {sd}")
    if abs(mean) > 1000:
        raise ValueError(f"implausible mean: {mean}")
    return {"mean": round(mean, 2), "sd": round(sd, 2)}


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


# --- entry point -----------------------------------------------------------

def forecast(entrant, r, history=None, previous=None, variant=None):
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
    prompt = build_prompt(r, history, variant)
    ih = prompt_hash(entrant, prompt)

    if previous and f"in={ih}" in (previous.get("notes") or "") \
            and not (previous.get("notes") or "").startswith("MOCK"):
        return previous

    if has_key(entrant):
        try:
            top = parse_forecast(call_provider(entrant, prompt))
            note = (f"{model_id(entrant)}, harness v1, variant={variant}, "
                    f"1 sample; in={ih}")
        except Exception as e:
            top = mock_forecast(entrant, r["round_id"], per["mean"], per["sd"])
            note = f"MOCK: {model_id(entrant)} call failed ({type(e).__name__}); in={ih}"
    else:
        top = mock_forecast(entrant, r["round_id"], per["mean"], per["sd"])
        note = ("MOCK: no API key configured; deterministic placeholder, "
                f"replaced by real output once keys are added; in={ih}")
    return {
        "round_id": r["round_id"],
        "entrant": entrant,
        "topline": top,
        "notes": note[:500],
    }
