import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import nyt_books
books = [{"rank": i, "primary_isbn13": f"97800000000{i:02}",
          "title": f"Book {i}", "author": "Author"} for i in range(1, 11)]
payload = {"status": "OK", "results": {"published_date": "2026-10-25",
    "bestsellers_date": "2026-10-10", "list_name_encoded": "hardcover-fiction",
    "books": books}}
parsed = nyt_books(json.dumps(payload).encode())
assert parsed["lists"]["nyt_hardcover_fiction"][0]["week_end"] == "2026-10-25"
print("NYT adapter contract passed")
