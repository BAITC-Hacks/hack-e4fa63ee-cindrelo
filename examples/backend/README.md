# Real offline demonstration

These are model artifacts and cached provider data, not synthetic fixtures.

- `curve.json`: per-turbine empirical wind/power bins and training means, fitted using observations whose hourly intervals end by 2025-12-31 19:00 UTC. Metadata identifies the validation bundle and configuration. The CatBoost binary is omitted because December selected the curve.
- `weather/`: four original Open-Meteo Single Runs response envelopes: both turbine coordinates, runs initialized 2026-01-09 at 00:00 and 12:00 UTC. Each includes request, returned coordinates/units, retrieval timestamp and SHA-256 content digest.
- `recovery-events.jsonl`: trace of a live OpenAI controller recovering from a deliberately injected latest-weather failure by selecting the preceding eligible cached run. This failure was simulated, not a provider outage.
- `evaluation.json`: measured December selection and January holdout summary from the full training command.

Run `python -m src demo` from the repository root to reproduce the two example forecast versions without network access. The first issuance is 2026-01-09 19:00 UTC; the second is 2026-01-10 07:00 UTC. The latter uses a newer eligible run under the assumed eight-hour publication delay. Forecast IDs and numerical predictions match `examples/dashboard/`.

Attribution: weather from **Open-Meteo and ECMWF**, https://open-meteo.com/, licensed under https://creativecommons.org/licenses/by/4.0/. Weather publication provenance is unverified; archive retrieval is not proof of as-issued availability. Turbine observations were provided by the hackathon organizers. Raw timezone, timestamp convention and wind-height mapping remain assumptions. See the root README for evaluation and limitations.
