# Accuracy investigation and one-week plan

Investigated 23 September 2026 against code `dcc434936143c737af16183d755b01719d6c0f4d`. This is a research plan, not a claim of improved accuracy. The Horizon demo and published forecasts are unchanged.

## Findings from the repository

Sources: `src/model.py`, `src/training.py`, `src/weather.py`, `src/data.py`, `config.json`, `examples/backend/evaluation.json`, and `examples/dashboard/metrics.csv`.

| January metric | Empirical curve | CatBoost | Persistence |
|---|---:|---:|---:|
| Existing pooled MAE, horizons 1–48 | 0.16689 | 0.20873 | 0.33398 |
| Hours 1–24 MAE | 0.15785 | 0.20204 | 0.31080 |
| Hours 25–48 MAE | 0.17624 | 0.21566 | 0.35794 |

Bucket scores above are sample-weighted across turbines from the committed metrics, not new model runs. Each turbine contributes 744 pairs to hours 1–24 and 720 to hours 25–48. These are normalized-power errors, not percentages of forecasting accuracy.

1. **Evaluation does not exactly match submission.** `evaluate()` filters issuance and target timestamps into January. This excludes December 31's origin and therefore January 1's day-ahead targets. February export instead selects hours 25–48 from the preceding local midnight, including the preceding month's origin. Use target-window scoring with eligible preceding origins and an origin-safe model for each. A complete January day-ahead comparison should contain 744 hours per turbine, subject to actual completeness. Recompute the curve baseline before comparing any candidates; do not silently replace historical reported scores.
2. **Measured-wind versus forecast-wind mismatch.** The curve uses 0.5 m/s bins of observed turbine wind and bin mean power, then receives forecast `wind_speed_100m` at inference. Hub height and the measured sensor height are unspecified. Calibrating forecast wind or fitting a direct forecast-wind power relationship is a higher-priority hypothesis than increasing model complexity. It is not yet a measured improvement.
3. **Loss mismatch.** CatBoost uses fixed RMSE loss, 400 iterations, depth 6; selection uses December MAE. Compare MAE training and simpler settings. The existing mean-bin curve also need not minimize MAE; a median-bin curve is a cheap challenger.
4. **Unequal history.** Weather collection starts September 2025. CatBoost's validation fit therefore sees September–December weather/target pairs, whereas the curve uses available observations back to March 2023. This is a legitimate existing recipe, but does not establish that CatBoost is inherently worse. Compare both under explicit matched and extended training windows.
5. **No recent operating-state inputs.** CatBoost has weather, turbine, time and lead features, but no power/wind history available at issuance. Test recent bias correction and lag features only after confirming observation availability and latency. Future target wind/power must never enter production features.
6. **Data regimes need inspection.** Prepared complete hours total 23,666 and 24,784. A descriptive full-history query found 80 and 54 hours respectively with measured wind at least 8 m/s and normalized power below 0.05. This is a diagnostic flag, not evidence of faults, icing or curtailment. Investigate training-period examples, source quality and gaps; retain valid low-output periods in evaluation.
7. **Provenance remains a gate.** Raw timezone, interval convention and eight-hour weather availability are assumptions. Run timestamps alone do not establish actual historical publication. Confirm with the organizer/provider; do not choose timezone or latency by whichever gives the best January score.

The local checkout has hourly observations and small portable weather examples, but no full training model bundle or full weather archive. The committed evaluation establishes the baseline; a complete residual attribution or candidate backtest needs the backend machine's full cache or a reproducible archive acquisition. The two demo issuances are insufficient to rank models.

## Experiment order

| Priority | Experiment | Method and safeguards | Output |
|---|---|---|---|
| P0 | Correct evaluation and audit lineage | Match submission hours 25–48, include preceding origins, require training targets and weather availability before issuance, identical target masks for every model | Frozen baseline and per-origin predictions |
| P1 | Calibrated curve | Compare existing mean curve, median curve, and per-turbine forecast-wind calibration followed by the curve; also a direct forecast-wind-to-power curve | Cheapest deployable challenger |
| P1 | MAE CatBoost | Compare RMSE/MAE; depth 4/6 with chronological early stopping; fit calibrators and all preprocessing inside each training fold | Small reproducible model comparison |
| P2 | Residual correction | Predict `actual - curve_prediction` from issued weather, wind direction, lead and season; cross-fit curve predictions within training to avoid in-sample residual leakage | Hybrid challenger |
| P2 | Recent state | Last available power/wind, rolling summaries and recent matured forecast residuals, with explicit latency and stale-data fallback | Availability-safe correction or negative result |
| P3 | More historical weather | Extend eligible runs into earlier seasons if coverage/provenance permit; compare recent versus longer history | Learning-curve evidence |

Use capped search budgets rather than a large hyperparameter sweep. Compare per-turbine versus shared models only after the simple candidates. Smooth or constrain curves carefully: enforcing monotonicity through a real cut-out region can be wrong. Keep all clipping and missing-data behavior identical across candidates.

Defer neural sequence models, more LLM calls and new weather-provider ensembles this week. The LLM orchestrates tools; it does not supply the numerical power prediction. More calls are not an accuracy intervention. Multi-provider historical ensembles require compatible as-issued coverage, which is not established here.

## Validation rules and promotion gate

- Primary objective: sample-weighted MAE on submission-aligned hours 25–48. Also report RMSE, signed bias, both turbines, horizons 1–24, wind regimes, ramps, and coverage.
- Use chronological expanding folds before January: for example train through September/test October, train through October/test November, train through November/test December. Early stopping and calibration use an earlier inner time split, not the fold being scored. All overlapping forecasts for a target must respect target-end/availability cutoffs; never random-split rows.
- January has already been inspected and is no longer a pristine holdout for this new investigation. Freeze the recipe using pre-January folds, then run one January confirmation and label it reused historical evaluation. Truly untouched validation needs later observations; none exist for February in the supplied files.
- Use paired errors on the same origins/targets. Estimate uncertainty using day blocks (both turbines together), and check sensitivity to multi-day blocks because weather errors are correlated. Do not treat 2,928 correlated forecast pairs as independent samples.
- Proposed promotion gate, fixed before experiments: at least 3% relative day-ahead MAE reduction versus the corrected curve baseline over pre-January outer folds, improvement in at least two of three folds, and no turbine's aggregate MAE worse by more than 0.005 normalized units. A confidence interval including no improvement warrants retaining the curve or extending evaluation. These are engineering acceptance thresholds, not promised gains.
- January confirmation must show no aggregate day-ahead regression. If the candidate fails, retain the existing model; do not use repeated January feedback to keep tuning. Operational provenance problems remain disclosed even when retrospective scores improve.
- Preserve raw observations, immutable caches, baseline forecasts and model identities. Do not overwrite the demo during research. Give every candidate its configuration, training cutoff, feature list, hashes, fold scores and inference fallback.

## One-week delivery plan

| Day | Work | Owner and deliverable |
|---|---|---|
| 1 | Confirm target/availability assumptions; inventory backend cache; implement target-window evaluation and boundary tests | Backend: corrected baseline, artifact inventory and written assumptions |
| 2 | Build chronological folds; inspect training-only wind/power alignment, gaps, bias and flagged low-output periods | Backend: residual diagnostics and frozen experiment protocol |
| 3 | Run curve calibration and MAE CatBoost ablations on identical folds | Backend: per-origin results and candidate shortlist |
| 4 | Test curve-residual correction and latency-safe recent state if data permit; otherwise extend weather training coverage | Backend: incremental ablation results |
| 5 | Freeze candidate; one January confirmation; compute paired block uncertainty and apply promotion gate | Backend: accept/reject report, no forced model replacement |
| 6 | Publish accepted candidate to a separate contract-compatible output directory; verify 96-row export, revisions, provenance and stale-data fallback | Backend + dashboard: integrated challenger preview and regression checks |
| 7 | Rehearse on Windows, validate February export coverage, retain rollback model and disclose evaluation limitations | Both: reproducible demo and final comparison report |

Dashboard work stays focused: present baseline/challenger metrics for the same window, explicitly label 25–48-hour day-ahead results, retain normalized units and source warnings, and distinguish revisions from errors. Consume supplied metrics rather than calculating a new score from whichever forecast happens to be displayed. Only add uncertainty displays if calibrated interval artifacts are actually supplied.

If archive acquisition or provenance cannot be resolved in the week, deliver the corrected evaluation and measured low-cost experiments, keep the existing curve, and label archive-based findings retrospective. No February accuracy claim is possible without February observations.

## External references checked

- [CatBoost regression objectives](https://catboost.ai/docs/en/concepts/loss-functions-regression): MAE and Quantile are supported objectives; changing the loss is an available experiment, not a guaranteed gain.
- [Open-Meteo Single Runs API](https://open-meteo.com/en/docs/single-runs-api): individual initialization-time runs; current documentation lists IFS history from March 2024 and most other models from April 2026. Verify the exact model/version and archive semantics before extending the September–January experiment or adding providers.
- [Open-Meteo features](https://open-meteo.com/en/features): describes IFS historical availability using hindcasts. Keep the existing as-issued caveat until the actual chosen data source is confirmed acceptable.
