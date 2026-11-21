import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import mint

body = (b"Program Name,Item,Item Description,Adj. Net Demand,Date Sales Report is Valid\n"
        b"Annual Sets,26RH,Example Set,100,10/11/2026\n")
parsed = mint(body)
assert parsed["series"]["mint_26RH_cumulative"] == [{"date": "2026-10-11", "value": 100}]
print("Mint adapter contract passed")
