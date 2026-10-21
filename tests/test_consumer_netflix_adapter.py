import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import netflix_country
header = "country_name\tcountry_iso2\tweek\tcategory\tweekly_rank\tshow_title\tseason_title\n"
body = header + "".join(f"United States\tUS\t2026-10-18\tTV\t{i}\tTitle {i}\tSeason 1\n" for i in range(1, 11))
parsed = netflix_country(body.encode())
import json
assert json.loads(parsed["lists"]["netflix_us_tv"][0]["items"][0]) == ["Title 1", "Season 1"]
print("Netflix adapter contract passed")
