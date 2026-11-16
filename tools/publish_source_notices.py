"""Write site/sources.json from ssa/source_notices.py. Offline, deterministic.

  python tools/publish_source_notices.py           # write the file
  python tools/publish_source_notices.py --check   # exit 1 if it is stale

The site reads this file to print one standardized data-source notice under
every question and forecast page, and the full list under "Data sources &
terms" in the docs. The notices themselves are edited in
ssa/source_notices.py, never here and never in the JSON.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from ssa import source_notices  # noqa: E402

OUT = os.path.join(ROOT, "site", "sources.json")


def render():
    return json.dumps(source_notices.site_payload(), indent=1,
                      ensure_ascii=False, sort_keys=True) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    body = render()
    if args.check:
        current = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        if current != body:
            print("site/sources.json is stale: run tools/publish_source_notices.py")
            return 1
        print("site/sources.json is current")
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"wrote {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
