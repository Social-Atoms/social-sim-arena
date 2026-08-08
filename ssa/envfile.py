"""Load provider credentials from a .env file for local runs.

Python does not read .env on its own and this repo has no dependency that does,
so without this a local run silently files MOCK forecasts while the keys sit
unread on disk two directories up.

Two deliberate choices:

- **Already-set variables win.** In CI the keys arrive as GitHub Actions secrets
  and there is no .env at all; if one ever appeared in the tree it must not be
  able to shadow them. `override=True` exists for the local case where you want
  the file to be authoritative.
- **Only the library's own callers load it.** `ssa/harness.py` reads os.environ
  and nothing else, so the code path CI exercises is the same one tested here.
  Loading happens in the entry points (tools/*, refresh.main), not on import.

Returns the names it set, never the values -- these get printed.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(ROOT, ".env")


def parse(text):
    """KEY=VALUE lines into a dict. Tolerates `export`, quotes, and comments."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def load(path=None, override=False):
    """Set os.environ from `path`. Returns the sorted names actually set."""
    path = path or DEFAULT
    if not os.path.exists(path):
        return []
    with open(path) as f:
        pairs = parse(f.read())
    set_now = []
    for key, val in pairs.items():
        if not val:
            continue
        if key in os.environ and not override:
            continue
        os.environ[key] = val
        set_now.append(key)
    return sorted(set_now)
