"""Local/Actions entrypoint; only encrypted branch data is read, never executed."""
import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.forecast_reveal import reveal

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True)
    args = parser.parse_args()
    try:
        count = reveal(Path(__file__).resolve().parents[1], args.snapshot,
                       json.loads(os.environ['SSA_AGE_IDENTITIES']))
        print(f'Revealed {count} signed forecasts')
    except Exception:
        # Don't echo private keys or decrypted input in logs.
        print('Signed forecast reveal failed; no publication authorized.', file=sys.stderr)
        raise SystemExit(1)
