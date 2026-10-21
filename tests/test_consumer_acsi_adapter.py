import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import acsi_transcription
body=(b"industry_id,company_id,company_name,study_year,published_date,score,evidence_url,evidence_quote\n"
      b"retail,example,Example,2026,2026-10-06,82,https://theacsi.com/example/,Example scores 82.\n")
parsed=acsi_transcription(body)
assert parsed["series"]["acsi_retail_example"] == [{"date":"2026-10-06","value":82}]
print("ACSI adapter contract passed")
