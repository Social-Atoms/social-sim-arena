# NYT Books Adapter

The adapter preserves list publication date, bestseller date, ISBN13 edition,
dagger and asterisk markers, and the exact list name. A proposed question locks
before the sales window and resolves against a pinned publisher list. Candidate
and new-book rules must be fixed before lock. The API terms and attribution
record remain permission-needed, so this PR does not activate a round.
