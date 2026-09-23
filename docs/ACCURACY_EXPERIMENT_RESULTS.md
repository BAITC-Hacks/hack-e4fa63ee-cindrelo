# Accuracy experiments: retain the existing curve

Executed on native Windows / Python 3.12.13 on 23 September 2026. Eleven recipes were compared on chronological October–December folds. The chosen direct forecast-wind median curve improved every development fold, but regressed on the single January confirmation. **Promotion rejected.** No production model, demo directory or February submission was replaced.

## Comparison with previous results

The original evaluation was reproduced using the original January cutoff, issuance filter, CatBoost recipe and freshly acquired archived weather. Pooled MAE matched the committed results exactly to the displayed precision:

| Original January protocol, horizons 1–48 | Previous | Reproduced |
|---|---:|---:|
| Empirical curve | 0.166891 | 0.166891 |
| CatBoost | 0.208734 | 0.208734 |
| Persistence | 0.333983 | 0.333983 |

This establishes that the experiment starts from the same published baseline. These numbers are not comparable directly with a day-ahead-only score.

## Corrected day-ahead comparison

Evaluation now selects targets in the calendar month and retains the preceding month's issuance. A common training cutoff at the earliest evaluated origin ensures every model was trained before every forecast. January's cutoff is December 31 at local midnight; this is earlier than the old January 1 cutoff. It is a controlled fixed-cutoff comparison, not a replay of production's bundle fallback policy. This matters particularly for the sensitive CatBoost fit.

| Target month | Existing curve MAE | Selected direct median MAE | Relative MAE change |
|---|---:|---:|---:|
| October | 0.173249 | 0.159429 | 7.98% lower |
| November | 0.192623 | 0.162820 | 15.47% lower |
| December | 0.227012 | 0.206820 | 8.89% lower |
| October–December, sample weighted | 0.198236 | 0.176859 | 10.78% lower |
| January confirmation | 0.176779 | 0.179969 | **1.80% higher** |

The previous January day-ahead curve score was 0.176235 on 1,440 pairs. The corrected score is 0.176779 on **1,488 pairs / 744 per turbine**, including January 1 and the earlier safe training cutoff. The difference is a protocol change, not an accuracy enhancement.

January corrected comparisons: original CatBoost recipe 0.266688; persistence 0.365754. CatBoost's old-protocol 0.215655 day-ahead score was also reproduced. Do not attribute the change between these CatBoost results solely to model quality: both training cutoff and scored coverage changed.

Complete October/November/December day-ahead scored pairs are 1,374 / 1,440 / 1,474. Missing/incomplete observations are excluded identically for all candidates; October and December do not have full target coverage. Both turbines contribute to every paired comparison.

## Candidate ranking before January

| Recipe | October–December day-ahead MAE |
|---|---:|
| Direct forecast-wind median curve | **0.176859** |
| Affine wind calibration + existing curve | 0.183274 |
| Cross-fitted curve residual / MAE CatBoost | 0.190429 |
| Direct forecast-wind mean curve | 0.192046 |
| Measured-wind median curve | 0.196104 |
| Recent-history measured-wind curve | 0.196619 |
| Existing full-history mean curve | 0.198236 |
| MAE CatBoost, depth 6 | 0.213081 |
| MAE CatBoost, depth 4 | 0.220264 |
| RMSE CatBoost, depth 4 | 0.225889 |
| Original RMSE CatBoost, depth 6 | 0.250780 |

The direct median model learns 0.5 m/s forecast-wind bins per turbine with at least six training pairs, interpolates their median power and clips to [0,1]. Calibration and curves are fitted separately in each fold. Boosted challengers use an inner chronological seven-day validation interval for early stopping, then refit using all permitted training pairs. Original CatBoost retains its fixed 400 rounds. Residual targets use curves fitted on observations preceding each weekly training block. No future target power/wind enters features.

Candidate selection was written to `selection.json` before January predictions were evaluated. No runner-up was tried on January after the selected candidate failed.

## Promotion decision and diagnosis

The candidate passed the pre-January 3% gain, at least two winning folds and maximum 0.005 per-turbine regression gates. Paired block-bootstrap 95% intervals for absolute MAE improvement were positive for 1/3/7-day blocks: [0.00796, 0.03395], [0.00718, 0.03378], [0.01035, 0.03165]. Blocks keep turbines together and use sample-weighted error sums/counts, including incomplete days. These development intervals are exploratory and do not correct for selecting the best of multiple recipes.

January failed the no-regression gate for both turbines:

| Turbine | Existing MAE | Candidate MAE |
|---|---:|---:|
| 1 | 0.176083 | 0.179381 |
| 2 | 0.177476 | 0.180556 |

January RMSE also increased, from 0.256760 to 0.263650. Signed bias moved from -0.077094 to +0.034844. The candidate reduced development underprediction, but overpredicted in January; in the forecast-wind 4–8 m/s regime its January MAE rose from 0.212637 to 0.231801. This describes the failure; it does not establish its physical cause. Do not retune against January until it passes.

The bootstrap implementation was corrected after the initial run to weight incomplete days by their scored counts; only uncertainty summaries were recomputed from saved predictions. Selection, fitted models and all January predictions remained unchanged. The final runner contains that correction.

## Completed and conditional plan items

- Acquired 306 checksummed turbine/run weather envelopes covering 153 daily issuances; 14,688 weather rows. Existing portable fixtures remain unchanged.
- Added an isolated target-window evaluator, mature-target training filters, duplicate and future-weather rejection, and boundary regression tests.
- Executed all low-cost curve, calibration, MAE/RMSE boosting and cross-fitted residual recipes on identical outer folds; recorded per-turbine/horizon MAE, RMSE, bias, coverage, wind and actual-ramp diagnostics.
- Recorded candidate selection before the single reused-January confirmation, applied gates and retained the baseline.
- Recent-state features remain conditional on unconfirmed telemetry latency/availability. They were not granted assumed operational availability just to improve a score.
- Earlier weather expansion is deferred to a separately preregistered study. No additional model search was launched after January failed; that would turn confirmation into tuning.
- Candidate dashboard publication, replacement February export and challenger rehearsal were conditional on promotion and therefore are not performed. The existing Horizon demo and submission remain the rollback/current version.
- Organizer confirmation of timezone, interval convention and historical weather publication remains external. All results are retrospective under existing assumptions. January was previously examined; no untouched or February accuracy is claimed.

## Reproduce and inspect

```powershell
python -m src.accuracy --offline --output outputs/accuracy-repeat
python -m pytest -q
```

Use a new output directory: the runner refuses to overwrite a completed report. On a fresh clone omit `--offline` to acquire the required weather. The runner writes all experiment outputs separately and never calls production model persistence. Archive re-fetches may differ; compare content hashes before treating a run as identical.

Full row-level predictions and acquired cache remain in ignored local `outputs/accuracy/` and `data/weather/`. Compact reproducible evidence is committed under [examples/accuracy](../examples/accuracy/): metrics, frozen selection, promotion report, training recipes, diagnostics, legacy reproduction and SHA-256 hashes. Forecast rows are paired by fold/origin/target/turbine. Historical dependency and configuration versions should accompany any external reproduction.

Validation: the full suite passed **29 tests and 10 subtests** on Windows without `-X utf8`; the final small diagnostics/uncertainty changes are included in the final verification recorded with this report.
