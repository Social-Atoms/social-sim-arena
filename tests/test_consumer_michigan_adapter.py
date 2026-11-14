import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import michigan_finals, michigan_current

final = b"Month,YYYY,PX_MD,PX5_MD\nSeptember,2026,4.6,3.4\n"
# Head and tail of tbcpx1px5.csv as served on 2026-10-21: CRLF, 15 columns,
# blank padding rows, values in fixed columns 3 and 5.
pad = b"," * 14 + b"\r\n"
current = (b",,,EXPECTED CHANGE IN PRICES,,,,,,,,,,,\r\n" + pad + pad +
           b"DATE OF SURVEY,,,NEXT YEAR,,NEXT 5 YEARS,,,,,,,,,\r\n" + pad +
           b"October,2025,,4.6,,3.9,,,,,,,,,\r\n"
           b"September,2026,,4.6,,3.4,,,,,,,,,\r\n"
           b"October (P),2026,,4.7,,3.5,,,,,,,,,\r\n" + pad)
assert michigan_finals(final)["series"]["umich_inflation_1y"][-1]["value"] == 4.6
parsed = michigan_current(current)
assert parsed["series"]["umich_inflation_1y"] == [
    {"date": "2025-10-01", "value": 4.6}, {"date": "2026-09-01", "value": 4.6},
    {"date": "2026-10-01", "value": 4.7}]
assert parsed["series"]["umich_inflation_5_10y"][-1] == {"date": "2026-10-01", "value": 3.5}
assert parsed["metadata"]["release_types"] == {
    "2025-10-01": "final", "2026-09-01": "final", "2026-10-01": "preliminary"}
# A row whose values moved off their columns fails rather than misreading.
try:
    michigan_current(current.replace(b"October (P),2026,,4.7,,3.5", b"October (P),2026,4.7,,3.5,"))
    raise AssertionError("shifted row was accepted")
except ValueError:
    pass
print("Michigan inflation adapter contract passed")
