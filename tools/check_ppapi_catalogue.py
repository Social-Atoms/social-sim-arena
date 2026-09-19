"""Does the sponsor's gateway serve the models we score, under which ids?

    SA_BASELINE_HS=... python tools/check_ppapi_catalogue.py
    SA_BASELINE_HS=... python tools/check_ppapi_catalogue.py --json

`harness.PPAPI_MODELS` is empty in the repository and is meant to stay that way
until this has been run, because the failure it prevents is silent. An id that
is wrong by a version -- `grok-4.6` where the season scores `grok-4.5` -- is not
a routing mistake that shows up as an error. It is a different model answering
under an entrant's name, on a leaderboard row that still says the old one, and
nothing in the pipeline can tell the difference afterwards. `OPENROUTER_MODELS`
was filled by reading a catalogue entry by entry for the same reason, and one
model was deliberately left out of it because only a floating alias existed.

**This spends nothing.** `GET /v1/models` is a list, not an inference call. It
is also the only authoritative source: the vendor's own introduction page says
it lists "only some of the models", so absence from that page is not evidence,
and the console renders the full list in the browser where no script can read
it.

What it prints:

  exact      the gateway serves the same id the direct route scores. Safe to
             add: the entrant keeps its meaning.
  respelled  the same model under a different spelling (`kimi/kimi-k3` ->
             `kimi-k3`). Safe to add once a human agrees it is the same model.
  VERSION    the stem matches and the version does not. **Do not add.** This is
             the case this file exists to catch.
  absent     no candidate. The entrant stays on its direct route.

The `PPAPI_MODELS` block it prints at the end contains the exact matches only.
Respellings are printed separately, commented out, so that adding one is a
decision somebody made rather than a default.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ssa import harness

DEFAULT_BASE = "https://app.ppapi.ai/v1"
TIMEOUT = 30


def normalise(model_id):
    """Lower-case, vendor prefix dropped, separators removed.

    `kimi/kimi-k3`, `Kimi-K3` and `kimi_k3` are one model written three ways,
    and the gateway's own documentation shows ids in a fourth style
    (`claude-opus-4-8` where the vendor writes `claude-opus-4.8`). Comparing
    raw strings would call all of those absent.
    """
    tail = model_id.split("/")[-1].lower()
    return re.sub(r"[^a-z0-9]", "", tail)


def version_of(model_id):
    """The digit groups in an id, in order: ('4', '5') for `grok-4.5`.

    Two ids whose stems agree and whose versions do not are different models,
    which is the whole point of this check.
    """
    return tuple(re.findall(r"\d+", model_id.split("/")[-1]))


def stem_of(model_id):
    return re.sub(r"[0-9]", "", normalise(model_id))


def fetch_catalogue(base, key):
    url = base.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            body = json.load(response)
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"{url}: HTTP {err.code}: {detail}")
    except Exception as err:                              # noqa: BLE001
        raise SystemExit(f"{url}: {type(err).__name__}: {err}")
    rows = body.get("data") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        raise SystemExit(f"{url}: no model list in the reply: "
                         f"{json.dumps(body)[:300]}")
    ids = sorted({r.get("id") for r in rows if isinstance(r, dict) and r.get("id")}
                 | {r for r in rows if isinstance(r, str)})
    if not ids:
        raise SystemExit(f"{url}: the model list is empty")
    return ids


def classify(ours, catalogue):
    """(verdict, candidate) for one of our model ids."""
    if ours in catalogue:
        return "exact", ours
    by_norm = {normalise(c): c for c in catalogue}
    hit = by_norm.get(normalise(ours))
    if hit:
        return "respelled", hit
    stem, ver = stem_of(ours), version_of(ours)
    near = sorted(c for c in catalogue
                  if stem_of(c) == stem and version_of(c) != ver)
    if near:
        return "VERSION", ", ".join(near)
    return "absent", ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default=os.environ.get(harness.PPAPI_BASE_ENV)
                    or DEFAULT_BASE)
    ap.add_argument("--json", action="store_true",
                    help="machine-readable, for a workflow rather than a human")
    args = ap.parse_args(argv)

    key = os.environ.get(harness.PPAPI_ENV)
    if not key:
        raise SystemExit(
            f"{harness.PPAPI_ENV} is not set. It is the gateway's key; this "
            "reads a list and bills nothing, but the endpoint needs auth.")

    catalogue = fetch_catalogue(args.base, key)
    verdicts = {}
    for name in sorted(harness.MODELS):
        ours = harness.MODELS[name]["model"]
        verdict, candidate = classify(ours, catalogue)
        verdicts[name] = {"ours": ours, "verdict": verdict,
                          "candidate": candidate}

    if args.json:
        print(json.dumps({"base": args.base, "catalogue_size": len(catalogue),
                          "models": verdicts}, indent=2, sort_keys=True))
        return 0

    print(f"{args.base}/models: {len(catalogue)} models\n")
    width = max(len(n) for n in verdicts)
    for name, row in verdicts.items():
        note = f"  <- {row['candidate']}" if row["candidate"] else ""
        print(f"  {row['verdict']:<10} {name:<{width}}  {row['ours']}{note}")

    exact = {n: r["ours"] for n, r in verdicts.items()
             if r["verdict"] == "exact"}
    respelled = {n: r["candidate"] for n, r in verdicts.items()
                 if r["verdict"] == "respelled"}
    wrong = [n for n, r in verdicts.items() if r["verdict"] == "VERSION"]

    print("\nPPAPI_MODELS = {")
    for name, mid in sorted(exact.items()):
        print(f'    "{name}": "{mid}",')
    if respelled:
        print("    # Same model, different spelling. Add after agreeing it is")
        print("    # the same model -- the gateway writes ids its own way.")
        for name, mid in sorted(respelled.items()):
            print(f'    # "{name}": "{mid}",')
    print("}")

    if wrong:
        print(f"\nNot routable without changing what the season scores: "
              f"{', '.join(sorted(wrong))}. The gateway serves a different "
              "version of each, and an entrant id that changes model mid-season "
              "makes its scores incomparable with its own history.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
