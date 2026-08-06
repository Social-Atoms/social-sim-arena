"""LLM entrant harness.

Every frontier-model entrant runs through this module each refresh. Two modes:

1. REAL: when an API key for the provider is present in the environment,
   the model is asked for a forecast (uniform prompt, temperature 0).
   Keys are read from env so they can live in GitHub Actions secrets
   (Settings > Secrets > Actions) or any other deploy platform's secret
   store; nothing is ever committed.

2. MOCK: when no key is configured, a deterministic placeholder forecast is
   filed instead: persistence plus a small model-specific offset, clearly
   labeled MOCK in the notes. This keeps the entrant slots, pages, and
   scoring pipeline fully exercised until keys are added.

Adding a real provider = fill in `call_provider` for it and set the env var.
"""
import hashlib
import os

MODELS = {
    "gpt-5.5":        {"env": "OPENAI_API_KEY",    "name": "GPT-5.5"},
    "claude-opus":    {"env": "ANTHROPIC_API_KEY", "name": "Claude Opus"},
    "gemini-pro":     {"env": "GOOGLE_API_KEY",    "name": "Gemini Pro"},
    "grok":           {"env": "XAI_API_KEY",       "name": "Grok"},
    "deepseek":       {"env": "DEEPSEEK_API_KEY",  "name": "DeepSeek"},
    "qwen":           {"env": "DASHSCOPE_API_KEY", "name": "Qwen"},
}

PROMPT = (
    "You are forecasting a public-opinion release. Question: {question} "
    "Recent history: {history}. Reply with JSON {{\"mean\": <number>, \"sd\": <number>}} "
    "for your predictive distribution of the released value. No other text."
)


def has_key(model_id):
    return bool(os.environ.get(MODELS[model_id]["env"]))


def call_provider(model_id, question, history):
    """Real API call. Implemented per provider as keys come online."""
    raise NotImplementedError(
        f"{model_id}: key present but provider call not wired yet")


def mock_forecast(model_id, round_id, persistence_mean, persistence_sd):
    """Deterministic placeholder: persistence + a stable per-(model, round)
    offset in [-1.5, +1.5] points, slightly wider uncertainty."""
    h = hashlib.sha256(f"{model_id}:{round_id}".encode()).digest()
    offset = (h[0] / 255.0) * 3.0 - 1.5
    widen = 1.0 + (h[1] / 255.0) * 0.8
    return {
        "mean": round(persistence_mean + offset, 2),
        "sd": round(max(persistence_sd * widen, 1.0), 2),
    }


def forecast(model_id, r):
    """One forecast dict for a round definition with baselines attached."""
    per = r["baselines"]["persistence"]
    if has_key(model_id):
        try:
            top = call_provider(model_id, r["question"], None)
            note = "live model output, harness v0"
        except NotImplementedError:
            top = mock_forecast(model_id, r["round_id"], per["mean"], per["sd"])
            note = "MOCK: key present but provider not wired; deterministic placeholder"
    else:
        top = mock_forecast(model_id, r["round_id"], per["mean"], per["sd"])
        note = "MOCK: no API key configured; deterministic placeholder, replaced by real output once keys are added"
    return {
        "round_id": r["round_id"],
        "entrant": model_id,
        "topline": top,
        "notes": note,
    }
