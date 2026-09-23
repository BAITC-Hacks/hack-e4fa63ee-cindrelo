# Portable dashboard candidate

The dashboard loads the December-selected `add_wind_cat` recipe as `aifs_gem`.
It predicts wind with CatBoost (300 trees, depth 4, RMSE), then applies the
empirical measured-wind power curve. Hours 25–48 use that model. Hours 1–24
explicitly use the empirical curve; they were not scored as a learned head.

`manifest.json` selects a checksummed native `.cbm` model and JSON preprocessing
metadata. The JSON includes feature order, training medians, missing indicators,
curves, configuration hash and label-maturity cutoff. No pickle or scikit-learn
object is needed for inference. `boundary` serves the first January issuance;
`january` serves subsequent issuances. Training labels mature two hours after
their hourly interval ends, and no January labels fit either artifact.

The portable model reproduces all 1,488 recorded January day-ahead predictions
within 1.12e-16. January MAE is 0.160737 and January 31 MAE is 0.242786. These are
reused research diagnostics; neither reaches 0.05. The full hybrid horizon and
12-hour update issuances have not been independently accuracy-validated.
`demo_reference.json` also tests that the serving weather adapter reproduces
the initial demo's 48 day-ahead predictions for both turbines.

The four immutable `weather/` envelopes contain extracted archive slices for
the initial `2026-01-09T19:00Z` and update `2026-01-10T07:00Z` demo issuances.
They retain checksums and original acquisition provenance. Base IFS inputs are
in `examples/backend/weather/`. Runtime refreshes write to a separate ignored
output cache. Conditional offset eligibility uses an assumed eight-hour delay;
original publication timestamps are unverified. Timezone and turbine metadata
assumptions remain unchanged.

To regenerate from the existing local research caches, run
`python -B -m src.candidate`; export-only preparation uses optional research
dependencies. To verify the portable artifacts, run
`python -B -m pytest tests/test_candidate.py -q`.
