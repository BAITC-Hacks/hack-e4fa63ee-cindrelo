# February export and PR #6 verification

- `february_day_ahead.csv`: genuine normalized-power predictions, 672 February hours per turbine (1,344 rows), selected from horizons 25–48 of the preceding local midnight's forecast. It contains the existing forecast lineage columns and preserves degraded status.
- `verification.json`: repeated replay coverage checks, UTC period bounds, environment, source integration commit, SHA-256 of the export and unresolved assumptions.
- `live-agent-events.jsonl`: a fresh successful OpenAI tool-controller execution after the encoding fix. `timestamp` is simulated issuance; `executed_at` is actual execution time. The corresponding forecast ID matches the first January example in `examples/dashboard/`.

This is an export/evidence directory, not a dashboard snapshot: day-ahead rows intentionally contain only 24 targets per issuance. To view all February forecasts, regenerate the complete five-file dashboard directory with `python -m src train` then `python -m src replay`, and set `CINDRELO_OUTPUT_DIR=outputs/dashboard`. Existing full local caches permit `python -m src replay --offline`.

The calendar is assumed `Asia/Almaty`, so February spans 2026-01-31 19:00 UTC inclusive to 2026-02-28 19:00 UTC exclusive. January 31 issuance uses the December-trained validation bundle; later issuances use the final bundle. No training data later than issuance are allowed. Power is normalized, not MW/MWh; February observations are unavailable.

Weather attribution: **Open-Meteo and ECMWF**, https://open-meteo.com/, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). An eight-hour publication delay is assumed. Archived-run historical as-issued provenance is unverified. Neither the export nor these verification checks establish operational forecast skill or February accuracy. See [the backend verification notes](../../docs/BACKEND_VERIFICATION.md).
