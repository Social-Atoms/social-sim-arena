import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import sce_extensions

fixture = json.dumps({"not": "an xlsx"}).encode()
try:
    sce_extensions(fixture)
except Exception:
    pass
else:
    raise AssertionError("non-XLSX SCE input must fail loudly")

print("SCE consumer adapter contract passed")
