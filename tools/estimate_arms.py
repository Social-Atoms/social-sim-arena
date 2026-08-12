"""What the elicitation conditions cost, before anything is spent.

Calls nothing. The persona condition is one call per simulated respondent per
round, so its price is linear in the panel and the panel is the only thing that
buys accuracy -- and accuracy only as one over the square root. This prints
both halves of that trade so the panel size is chosen with the number in view
rather than after the invoice.

    python tools/estimate_arms.py
    python tools/estimate_arms.py --replicates 4 --models claude-opus,gemini-pro
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import harness, personas, series as series_registry  # noqa: E402

# Measured on the completed backtest: 378 input / 454 output tokens per call at
# maximum effort. A persona call is shorter -- no series history, one question
# -- so its input is roughly half, and its output is a single word of JSON
# rather than a reasoned distribution.
FORECAST_TOKENS = (378, 454)
PERSONA_TOKENS = (190, 60)
# The news condition is the same forecast call with a 14-day digest prepended;
# measured at ~13,000 tokens of corpus on a real August window, which dwarfs
# everything else in the prompt and is the whole cost of the arm.
NEWS_TOKENS = (378 + 13000, 454)

# $ per 1M tokens, (input, output).
PRICING = {
    "claude-opus": (5.0, 25.0), "claude-opus-5": (5.0, 25.0),
    "claude-sonnet": (3.0, 15.0), "claude-fable": (10.0, 50.0),
    "gpt-5.6-luna": (2.0, 10.0), "gpt-5.6-sol": (2.0, 10.0),
    "gpt-5.6-terra": (2.0, 10.0),
    "gemini-pro": (2.0, 10.0), "gemini-flash": (0.3, 2.5),
    "grok": (3.0, 15.0),
    "qwen-3.7": (1.2, 6.0), "qwen-3.8": (1.2, 6.0), "glm": (0.6, 2.2),
    "deepseek-pro": (0.6, 1.7), "deepseek-flash": (0.3, 1.1),
}


def price(model, tokens, n):
    cin, cout = PRICING.get(model, (2.0, 10.0))
    return n * (tokens[0] / 1e6 * cin + tokens[1] / 1e6 * cout)


def open_rounds():
    """Rounds that would actually be filed, and which have an instrument."""
    with open(os.path.join(ROOT, "questions", "season0.json")) as f:
        season = json.load(f)
    out = []
    for r in season["rounds"]:
        try:
            has_survey = series_registry.survey(r["series"]) is not None
        except KeyError:
            has_survey = False
        out.append((r["round_id"], r["series"], has_survey))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replicates", type=int, default=personas.REPLICATES)
    ap.add_argument("--models", default=",".join(harness.elicitation_models()))
    args = ap.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in models if m not in harness.MODELS]
    if unknown:
        sys.exit(f"unknown model(s): {', '.join(unknown)}")

    rounds = open_rounds()
    n_rounds = len(rounds)
    n_survey = sum(1 for _, _, s in rounds if s)
    panel_n = personas.CELLS * args.replicates

    print("panel size vs resolution (discretisation error, points)")
    print(f"  {'per cell':>9s} {'panel':>6s} {'error':>7s}")
    for k, n, se in personas.resolution():
        mark = "  <- selected" if k == args.replicates else ""
        print(f"  {k:>9d} {n:>6d} {se:>7.2f}{mark}")

    print(f"\n{n_rounds} rounds in the season, {n_survey} with a survey "
          f"instrument (the persona arm can only run on those)")
    print(f"panel: {personas.CELLS} cells x {args.replicates} = {panel_n} "
          f"respondents per round\n")

    print(f"{'model':16s} {'condition':10s} {'calls':>7s} {'$/full season':>14s}")
    total = 0.0
    for m in models:
        for cond in ("superfc", "news", "web"):
            if cond == "web" and m not in harness.WEB_CAPABLE:
                continue          # no vendor-hosted search; no such entrant
            n = n_rounds
            usd = price(m, NEWS_TOKENS if cond == "news" else FORECAST_TOKENS, n)
            total += usd
            print(f"{m:16s} {cond:10s} {n:7d} {usd:14.2f}")
        n = n_survey * panel_n
        usd = price(m, PERSONA_TOKENS, n)
        total += usd
        print(f"{m:16s} {'persona':10s} {n:7d} {usd:14.2f}")
    print(f"{'':16s} {'TOTAL':10s} {'':7s} {total:14.2f}")

    print("\nNotes:")
    print("  - one filing per round, not per refresh: the input hash means a")
    print("    round is paid for once unless its pre-lock history changes.")
    print("  - the web arm is live-only; the backtest refuses it, because there")
    print("    the outcome is already published and search would read it.")
    print("  - a backtest of persona/superfc over the 22 matched releases would")
    print(f"    cost roughly {price(models[0], PERSONA_TOKENS, 22 * panel_n):.2f} "
          f"per model for persona, {price(models[0], FORECAST_TOKENS, 22):.2f} "
          "for superfc.")


if __name__ == "__main__":
    main()
