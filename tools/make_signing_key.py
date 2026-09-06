"""Generate the arena's Route A signing key. Run once by the maintainer.

    python tools/make_signing_key.py [--key-id ssa-live]

Prints two things and stores neither:

  1. the private key, to paste into the repository's Actions secret
     SSA_SIGNING_KEY (Settings -> Secrets and variables -> Actions);
  2. the entry for site/keys.json, to commit.

The private key must not be written anywhere else: not .env, not Vercel, not a
chat. Rotation is this command again with a new --key-id, both entries kept in
keys.json for a week or two, then the old one removed.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ssa import signing  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key-id", default=signing.DEFAULT_LIVE_KEY_ID)
    args = ap.parse_args(argv)
    private, public = signing.generate()
    print("1. Actions secret  SSA_SIGNING_KEY  (paste this, then close the terminal):")
    print(f"   {private}")
    print()
    print("2. site/keys.json entry (commit this):")
    print(json.dumps({"key_id": args.key_id, "purpose": "live",
                      "public_key": public,
                      "since": time.strftime("%Y-%m-%d", time.gmtime())},
                     indent=2))
    if args.key_id != signing.DEFAULT_LIVE_KEY_ID:
        print()
        print(f"3. Actions variable  SSA_SIGNING_KEY_ID = {args.key_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
