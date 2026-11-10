# Singapore passenger volume: Jurong East MRT and Boon Lay bus interchange

Owner update, 2026-11-10: prepare production immediately, with no unscored pilots. All proposed rounds count toward scoring from the first eligible target month. Changes are local until reviewed and pushed; the proposed rounds are not yet in questions/season0.json.

## First formal rounds

December 2026 is the first prospective target: lock November 30 at 23:59 Singapore time (15:59 UTC), expected release January 10, 2027. November is already underway and cannot be admitted under the existing pre-month lock. December's October reference is already archived. January–March 2027 are also proposed as scored rounds.

The exact specifications are cards [train v3](../data-cards/sg-lta-pv-train-jurong-east.json) and [bus v3](../data-cards/sg-lta-pv-bus-boon-lay-int.json). Their rounds files carry the corresponding byte hashes.

## Capture and deployment

AccountKey access was tested successfully; the original August–October 2026 train and bus files are archived, preserving fetch timestamps and hashes. DataMall serves only three months, so do not defer collection until a round opens.

refresh.yml now configures independent, non-blocking capture steps for each kind. Set the repository secret LTA_API_KEY before deploying; missing credentials fail visibly, and a train failure does not skip the bus step. Rotate the key previously exposed in the Claude session. Keys never enter archives or URLs.

The commands `python -m ssa.adapters.sg_lta_pv capture train` and `python -m ssa.adapters.sg_lta_pv capture bus` archive only missing months. No network request is made for a month already held. A manual fallback is `python -m ssa.adapters.sg_lta_pv ingest <train|bus> <YYYY-MM> <file.zip>`. Different bytes for an archived month are refused.

## Targets, references and resolution

Predict weekday average daily tap-ins at Jurong East MRT (EW24/NS1) or Boon Lay bus interchange (22009). Divide WEEKDAY tap-in totals by Monday–Friday dates excluding the public holidays frozen in the round. Codes remain strings and the station alias table handles slash/hyphen spellings.

Lock remains 23:59 SGT before the target month starts. The reference is mean_k(k=1, lag=1), normally the month two before the target. Interim standard deviations use measured system-wide two-month changes: 5.43% train and 6.66% bus, switching to the rule's own RMS relative errors after 12 resolved months. No missing reference is substituted.

Resolve from the first target-month zip archived on or after 00:00 SGT on the 10th of the following month. The release hour is estimated; continuous capture records its actual arrival. No file by 23:59 on the 17th, a missing target code, or zero weekday tap-ins during any hour 06–22 cancels the round.

## Licence and attribution

The [API terms](https://datamall.lta.gov.sg/content/datamall/en/api-terms-of-service.html) specify that dataset use is governed by the [Singapore Open Data Licence](https://datamall.lta.gov.sg/content/datamall/en/SingaporeOpenDataLicence.html), which permits redistribution and derived analyses with attribution. We interpret this specific dataset grant as applying to the API files; the website-content restriction and this interpretation are recorded in the cards. Do not imply LTA endorsement.

Attribution: Contains information from LTA DataMall Passenger Volume by Train Stations / Bus Stops, accessed on the dates recorded in sources/lta-pv/*/fetches.json, made available under the [Singapore Open Data Licence version 1.0](https://datamall.lta.gov.sg/content/datamall/en/SingaporeOpenDataLicence.html).
