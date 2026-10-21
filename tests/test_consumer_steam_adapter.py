import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssa.adapters.consumer import steam_official
payload={"route":"/charts/topsellers/:country?/:week?","loaderData":[json.dumps({"weekly":{"strCountryCode":"US","rtWeekStart":1791849600,"rgRanks":[{"nRank":i,"itemKey":{"appid":i}} for i in range(1,21)]}})]}
html=('<html><script id="valve-ssr-data" type="application/json">'+json.dumps(payload)+'</script></html>').encode()
parsed=steam_official(html)
assert len(parsed["lists"]["steam_us_top_sellers"][0]["items"]) == 10
print("Steam adapter contract passed")
