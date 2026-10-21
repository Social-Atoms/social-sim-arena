import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import michigan_finals, michigan_current

final = b"Month,YYYY,PX_MD,PX5_MD\nSeptember,2026,4.6,3.4\n"
current = (b",,,EXPECTED CHANGE IN PRICES\nDATE OF SURVEY,,,NEXT YEAR,,NEXT 5 YEARS\n"
           b"October (P),2026,,4.7,,3.5\n")
assert michigan_finals(final)["series"]["umich_inflation_1y"][-1]["value"] == 4.6
assert michigan_current(current)["series"]["umich_inflation_1y"][-1]["value"] == 4.7
print("Michigan inflation adapter contract passed")
