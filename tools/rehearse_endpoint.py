"""Call an endpoint the way the cron will, and file nothing.

    python tools/rehearse_endpoint.py --url https://host/forecast
    python tools/rehearse_endpoint.py --entrant ssa-starter          # url from entrants/<id>.json

This is not the contract probe (`tools/probe_agent_api.py`, which sends
fixtures). It runs `harness.forecast` itself -- the same function the
six-hourly refresh calls -- for one real open round of each shape (topline,
profile, ranking), signed with the published test key unless
`--signing-key-env` names a variable holding the live one. What comes back is
parsed by the same parsers, and the record that would have been written to
`forecasts/<round>/<id>.json` is validated against the forecast schema and
printed. Nothing is written into the repository: the registration is a
temporary file, and the reply log goes to a temporary directory.

If this passes for an endpoint, the cron will file for it. If it fails, the
message is the cron's own message.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ssa import harness, participants, profile_round, ranking_round, signing  # noqa: E402

SHAPES = ("continuous_normal", "profile_energy", "ranking_list")
KEY_OF = {"continuous_normal": "topline", "profile_energy": "profile",
          "ranking_list": "ranking"}


def open_rounds(data, shapes):
    """One open, scoreable round per requested shape, earliest release first."""
    picked = {}
    for r in sorted(data["rounds"], key=lambda x: x["release_at"]):
        tt = r.get("target_type", "continuous_normal")
        if r.get("status") == "open" and tt in shapes and tt not in picked:
            picked[tt] = r
    return picked


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", help="the endpoint; or use --entrant")
    ap.add_argument("--entrant", default="rehearsal",
                    help="entrant id to call as; with no --url, its "
                         "registration in entrants/ supplies the url")
    ap.add_argument("--signing-key-env",
                    help="NAME of the variable holding the live signing key. "
                         "Default: the published test key (ssa-test).")
    ap.add_argument("--key-id", default=signing.TEST_KEY_ID)
    ap.add_argument("--shape", action="append", choices=SHAPES,
                    help="only these shapes (default: all three)")
    args = ap.parse_args(argv)

    if args.signing_key_env:
        private = os.environ.get(args.signing_key_env)
        if not private:
            ap.error(f"{args.signing_key_env} is not set")
        os.environ[signing.LIVE_KEY_ENV] = private
        os.environ[signing.LIVE_KEY_ID_ENV] = args.key_id
    else:
        os.environ[signing.LIVE_KEY_ENV] = signing.TEST_PRIVATE_KEY
        os.environ[signing.LIVE_KEY_ID_ENV] = signing.TEST_KEY_ID

    url = args.url
    if not url:
        reg = participants.registration(args.entrant)
        if not reg or not reg.get("route"):
            ap.error(f"no --url and entrants/{args.entrant}.json has no route")
        url = reg["route"]["url"]

    tmp_entrants = tempfile.mkdtemp(prefix="ssa-rehearse-entrants-")
    tmp_replies = tempfile.mkdtemp(prefix="ssa-rehearse-replies-")
    with open(os.path.join(tmp_entrants, args.entrant + ".json"), "w") as fh:
        json.dump({"entrant_id": args.entrant, "name": "rehearsal",
                   "type": "participant", "route": {"kind": "agent_api", "url": url}}, fh)
    participants.ENTRANTS = tmp_entrants
    os.environ["SSA_REPLIES_DIR"] = tmp_replies

    try:
        import jsonschema
        with open(os.path.join(ROOT, "schema", "forecast.schema.json")) as fh:
            schema = json.load(fh)
        with open(os.path.join(ROOT, "site", "data.json")) as fh:
            data = json.load(fh)
        shapes = tuple(args.shape) if args.shape else SHAPES
        picked = open_rounds(data, shapes)
        print(f"endpoint {url}\nsigned as {os.environ[signing.LIVE_KEY_ID_ENV]}; "
              f"calling as entrant '{args.entrant}'; nothing is written to the repository\n")
        failures = 0
        for tt in shapes:
            r = picked.get(tt)
            if r is None:
                print(f"--   {tt}: no open round on the site right now"); continue
            kw = {}
            if profile_round.is_profile(r):
                kw["profile_history"] = {c: [] for c in profile_round.cells_for(r)}
            if ranking_round.is_ranking(r):
                kw["ranking_history"] = []
            history = (data.get("series_tail") or {}).get(r.get("series")) or None
            try:
                body = harness.forecast(args.entrant, r, history=history, previous=None, **kw)
                jsonschema.validate(body, schema)
            except Exception as e:                       # noqa: BLE001 - reported
                failures += 1
                print(f"FAIL {r['round_id']} ({tt}): {type(e).__name__}: {str(e)[:300]}")
                continue
            answer = json.dumps(body[KEY_OF[tt]])
            print(f"ok   {r['round_id']} ({tt}): {answer[:100]}{'…' if len(answer) > 100 else ''}")
            print(f"     would file forecasts/{r['round_id']}/{args.entrant}.json; "
                  f"notes: {body['notes'][:90]}")
        print(f"\n{len(shapes) - failures} of {len(shapes)} shapes filed and validated")
        return 1 if failures else 0
    finally:
        shutil.rmtree(tmp_entrants, ignore_errors=True)
        shutil.rmtree(tmp_replies, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
