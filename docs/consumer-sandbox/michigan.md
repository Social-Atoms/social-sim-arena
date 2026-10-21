# Michigan One-Year Inflation Sandbox

Target: the preliminary one-year-ahead median expected inflation rate for the
named survey month, in percent. An actual round pins the publisher's release
calendar and an immutable preliminary file, locks at least 48 hours before the
release, and never substitutes the later final CSV for the preliminary result.

Example forecast: Normal(mean=4.5%, sd=1.0). Example preliminary value: 4.7%.
The offline test parses a preliminary fixture and computes the existing CRPS.
Its calendar and value are illustrative, not a published result. No production
round is registered by this sandbox PR.
