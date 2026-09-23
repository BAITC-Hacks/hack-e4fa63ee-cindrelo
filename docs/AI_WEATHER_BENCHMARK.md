# AIFS/GEM additional-weather benchmark

Executed 23 September 2026. **The December-selected wind-calibration recipe improves full January, but the MAE ≤0.05 objective remains unmet.** Its January MAE is 0.160737 versus 0.176782 for the empirical-curve baseline (9.08% lower), and January 31 is 0.242786 versus 0.308140 (21.21% lower). No production forecasts or model bundles changed.

## Fixed experiment and additional information

The protocol was saved before acquisition, fitting or scoring. Seven methods were fixed: curve baseline; a matched no-extra-weather CatBoost control; four AIFS/GEM recipes (direct CatBoost, direct histogram, direct CatBoost with causal live telemetry, and CatBoost wind calibration followed by the curve); and a fixed 50/50 curve/direct-CatBoost blend. No recipe or hyperparameter changed after scores became available. All fitting was limited to two CPU threads.

The matched `no_extra_cat` and `add_cat` methods share 300 iterations, depth 5, MAE loss, learning rate .03, L2 10, seed 42, calendar features, the same rows, causal cutoffs and existing IFS/GFS/ICON/JMA features. Their difference is the new forecast feature set. The histogram uses 250 iterations, 15 leaves, a 40-row leaf minimum, absolute-error loss and no early stopping. The selected `add_wind_cat` uses 300 iterations, depth 4 and RMSE loss on matured historical wind labels, then the historical measured-wind power curve. It has **no matched no-extra-weather RMSE control** in this batch; differences from earlier wind recipes cannot be attributed solely to AIFS/GEM.

Additional variables were requested through [Open-Meteo Previous Runs](https://open-meteo.com/en/docs/previous-runs-api), using only day2/day3 forecast offsets and explicit models:

- `ecmwf_aifs025_single`: 10 m and 100 m wind speed/direction.
- `cmc_gem_gdps`: 10 m, 80 m and 120 m wind speed/direction; no fabricated 100 m field.
- Both models: 2 m temperature/humidity, surface pressure, cloud cover and precipitation.

[AIFS](https://open-meteo.com/en/docs/ecmwf-api) and [GEM](https://open-meteo.com/en/docs/gem-api) have native 6-hourly and 3-hourly model output respectively, interpolated by the API to hourly values. Thus returned hourly points are not independent hourly model predictions. AIFS model versions changed during the training period, as noted in the earlier feasibility report.

## Full archive and missingness audit

Eight sequential multi-site monthly requests cover June 2025 through January 2026 in UTC: **11,760 location-hour rows**, two offsets and **470,400 expected native field values**. All expected native values are populated. The archive has no duplicate or missing hourly keys in the requested UTC windows. The intentionally requested unsupported height fields are documented as unsupported/all-null and excluded from the feature specification. After joining to the existing issuance records and selecting eligible offsets, the additional features also have **zero missing cells** in the benchmark weather rows.

Each response is cached with the exact URL/request, retrieval time, payload hash and raw-response hash under `data/ai-weather/`. Existing IFS and provider archives were read unchanged. Their pre-existing August weather exclusions remain in training; this acquisition does not restore missing baseline forecast rows. Median imputation and removal of all-null fields are fitted using training data only. The existing JMA 100 m field and its derived trajectory columns are all-null and excluded consistently in every fit.

## Causal boundaries and weather timing

The raw `Asia/Almaty` timezone and interval-start timestamp convention remain project assumptions. Confirming timezone, hub-height/sensor-height, and curtailment metadata were not available; the benchmark does not establish them from January errors.

Every fitted target must have finished its one-hour interval and passed a **two-hour reporting delay** before the cutoff. January 1 uses a separate fit at its preceding-day issuance, **2025-12-31 00:00 Asia/Almaty**; January 2–31 uses the **2026-01-01 00:00** cutoff. The most recent usable training target begins three hours before each cutoff. All fit records and last-label availability times are persisted. **No January target power or wind labels enter fitting.**

Selection uses only December 1–29 local target days. All those labels mature before the first January issuance. Selection was written before January evaluation and remains `add_wind_cat`. Earlier January telemetry is permitted only as an input to later issuances after interval-end plus two hours; observations older than five hours trigger the curve fallback for the live recipe. No target-hour measured wind is used as a prediction input.

Previous Runs does not expose exact original initialization/publication timestamps. Offset selection retains the conditional rule `valid_time - offset_hours + 8h <= issued_at`, using the existing assumed eight-hour publication delay. Day2 is used for target-start leads 24–40 h and day3 for 41–47 h. Every trajectory feature is built **after** that selection and is grouped within one turbine and issuance. This is a conservative assumed timing rule, not verified historical as-issued provenance. No dayzero or reanalysis is requested.

## Full method comparison

| Method | December 1–29 MAE | Full January MAE | January 31 MAE |
|---|---:|---:|---:|
| curve | 0.219982 | 0.176782 | 0.308140 |
| no_extra_cat | 0.200189 | 0.177384 | 0.247328 |
| add_cat | 0.194609 | 0.157499 | 0.234811 |
| add_hist | 0.192931 | 0.166746 | 0.247481 |
| add_cat_live | 0.192690 | 0.160682 | 0.234735 |
| add_wind_cat **(December-selected)** | 0.192289 | 0.160737 | 0.242786 |
| blend_add_cat | 0.198133 | 0.159407 | 0.271287 |

Every method covers **1,488 January pairs** (744 hours × two turbines), including January 1, and **48 January 31 pairs**. December has 1,474 complete scored pairs; incomplete SCADA target hours are excluded equally for every method. All scores are normalized-power MAE, not MW.

The matched direct-CatBoost comparison improves full January by **11.21%** when the new weather features are added, providing evidence that the additional information is useful in this fixed historical experiment. `add_cat` is the lowest January MAE in this batch, but **it is not the December-selected model**. January has been repeatedly examined; its ranking is a reused historical diagnostic, not untouched validation or a reason to switch the frozen winner. None of the seven methods reaches 0.05 on either required period. A gain here does not establish future-month accuracy.

## Paired uncertainty on the reused January sample

The coordinating task computed paired day-block bootstrap intervals, saved as `uncertainty.json`. Positive gain means lower MAE. With three-day blocks, the selected model versus the curve has gain **0.016044**, 95% interval **[-0.021746, 0.051317]**; direct `add_cat` versus the curve has gain **0.019283**, interval **[-0.014501, 0.051268]**. Both intervals include zero, so the January gain over the curve remains uncertain.

For the matched `add_cat` versus `no_extra_cat` comparison, the gain is **0.019885**, with three-day 95% interval **[0.005898, 0.034997]**. Its one-, three- and seven-day intervals are all positive. This strengthens the within-experiment evidence that the added forecast information helps the direct CatBoost recipe. These are exploratory intervals on repeatedly inspected January data, without adjustment for multiple comparisons; they do not establish independent future performance or attainment of the 0.05 goal.

## Reproduction and artifacts

Authoritative full results are in `outputs/ai-weather-benchmark/`; compact results, fixed protocol, all method metrics, selection, full archive/selected-feature missingness, fit audit and hashes are in `examples/ai-weather-benchmark/`. `hashes.json` covers source, imported baseline sources, existing inputs and generated core outputs. Source still matches its pre-score protocol hash.

```powershell
$env:OMP_NUM_THREADS='2'
$env:OPENBLAS_NUM_THREADS='2'
$env:MKL_NUM_THREADS='2'
.\.venv\Scripts\python.exe -B -m src.ai_weather_benchmark --offline --output outputs/ai-weather-benchmark-repeat
.\.venv\Scripts\python.exe -B -m pytest tests/test_ai_weather_benchmark.py -q
```

A new output directory is required. Offline reproduction reuses checksummed caches; it preserves the existing compact export. The run completed successfully. Runtime assertions check hash integrity, archive keys, conditional forecast eligibility, label maturity, fit cutoffs, finite bounded predictions and full January coverage. **Nine targeted tests pass**: unavailable-offset poisoning (including trajectory features), target-label/future-wind feature invariance across four recipes, turbine/issuance separation, duplicate archive/no-eligible-offset rejection, the earlier January boundary fit and two-hour label maturity, and matched-control independence from added weather. Results and test/source hashes are recorded in `verification.json`.
