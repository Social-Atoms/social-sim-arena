# Steam Weekly Adapter

The parser reads the inert official weekly SSR payload, requires `US`, preserves
typed app/package/bundle IDs, and rejects a page that returns a different week.
The round design locks before the measured Tuesday-to-Tuesday week and fixes
candidate/new-item rules in advance. Steam automation and public chart-license
terms remain permission-needed; this PR does not activate retrieval or rounds.
