# Independent audit of the 0.05 MAE goal

Audited 23 September 2026 against commit `0cb7f8ddfabed9de6dff6c0a40bd9c69eb6b23bb` and the saved local experiment predictions. No production model, source observations or forecasts were changed by this audit. The requested goal is MAE at most 0.05 on **both full January and January 31**, at the existing day-ahead issuance convention.

## Findings that affect the next experiment

1. **Reweighting the existing predictions cannot achieve the January 31 target.** I joined 57 saved recipe outputs on `(issued_at, valid_time, turbine_id)` from `outputs/{model-families,provider-benchmark,live-bias-final,weather-calibration,accuracy-round2-final,accuracy}/predictions.csv`. All 57 have the same 1,440 January 2–31 day-ahead rows. For each row, let `lo` and `hi` be the smallest/largest prediction across those recipes. The error `max(lo - actual, actual - hi, 0)` is a hindsight lower bound for **any nonnegative convex blend of these particular predictions**, even one allowed to choose weights separately for every target using the truth. It is not a bound on other models, extrapolating corrections or new data.

   | Hindsight diagnostic | January 2–31 | January 31 |
   |---|---:|---:|
   | Best pointwise convex blend within the saved prediction envelope | 0.034164 | **0.127671** |
   | Best saved individual prediction, separately for each target | 0.046880 | 0.133982 |

   January 31 local hours 00–06 are the decisive failure: the lowest prediction across all recipes averages approximately 0.51–0.58 by hour, while actual power averages approximately 0.075–0.173. Those seven hours alone exhaust more than the entire day's 0.05 error budget. New methods must substantially change the forecast trajectory, especially the overnight low-wind interval. An ensemble-weight sweep of these files cannot do that.

2. **Full-January verification is still missing from newer model benchmarks.** `src/weather_calibration.py:98`, `src/live_bias.py:90`, and `src/provider_benchmark.py`'s `evaluate` filter out origins before January 1. Their January day-ahead figures cover January 2–31 (1,440 pairs), not January 1–31 (1,488 pairs). This is documented honestly in the reports, but cannot be the final goal-completion metric. A full January run needs the December 31 local-midnight issuance and an eligible model at that origin. Use an earlier frozen selection/training cutoff for the whole run, or explicitly version the boundary model; do not backdate a December-31-trained model to the start of December 31.

3. **The live-input reporting delay and training-label availability are inconsistent at the cutoff.** `src/live_bias.py` correctly requires an observation interval to finish plus two reporting hours before it can become a feature. In contrast, `src/accuracy.py:20–26` includes training labels as soon as the interval ends. Therefore, a model notionally fitted for midnight can include the preceding two hours before those observations would arrive under the conservative delay assumption. This is a small boundary issue, not an explanation for the large errors, but the next operationally valid benchmark should apply the same availability delay to training labels. Existing historical scores are not altered by this read-only audit.

4. **Calendar features were intentionally removed in several recent families.** `src/weather_calibration.py:33–34` drops hourly/seasonal sine and cosine features when `physical=True`; `src/provider_benchmark.py:21` and `src/live_bias.py:50` always request that setting. A local hour/season ablation is a reasonable small experiment for recurring site effects. It is not evidence that restoring these features will fix January 31, and parameters must still be selected before examining the next held-out period.

## Additional diagnostic evidence

Applying the pre-January empirical curve to **measured future turbine wind** gives full-January MAE **0.035667** across 1,488 complete hourly turbine pairs, with turbine results 0.033908 and 0.037426. January 31 is 0.03083. This is an unavailable-future-input diagnostic, not a usable forecast, accuracy guarantee or formal information-theoretic floor. It supports prioritizing weather evolution and site wind estimation over another small power-curve adjustment. January 24 still has 0.1668 error even in that diagnostic, so the power relationship also has exceptional operating intervals.

Simple repeated-pattern explanations are weak. Pre-January power autocorrelation is about 0.90 at one hour, 0.41 at six hours, 0.11 at 24 hours, 0.07 at 48 hours and 0.07 at one week. January power shifted by 48 hours has MAE 0.3570/0.3468 for the two turbines; one-week persistence is 0.3435/0.3405. These are descriptive diagnostics, not lower bounds for a sequence model.

A December-only timestamp sensitivity check using day-ahead ECMWF inputs found wind correlation highest at the configured alignment (0.6907); a one-hour shift changed empirical-curve MAE only from about 0.2270 to 0.2249. Five-hour shifts were substantially worse. The strongest temperature correlation was two hours away from the configured alignment, which can reflect forecast phase error or timestamp conventions. This is insufficient evidence to change the raw timezone. Organizer metadata remains the proper authority.

## Suggested next experiment

Prioritize **new weather information and trajectory correction**, not another ensemble-weight search. Compare fresher eligible provider forecasts against the current older offsets; then consider multiple nearby grid cells and spatial pressure/wind gradients, using a small, preregistered model family. A temporal model should combine the already-available observation sequence with the *whole eligible forecast trajectory*, learn a bounded phase/amplitude correction on pre-January data, and score all required horizons. Test local-hour features as an explicit ablation. Keep the full-January boundary and two-hour reporting rules in the benchmark itself.

The [Open-Meteo Previous Runs documentation](https://open-meteo.com/en/docs/previous-runs-api), checked during this audit, describes fixed lead-time offsets and distinguishes them from individual initialized runs. A 48-hour offset is conditionally eligible with an assumed eight-hour publication delay only where `valid_time - 48h + 8h <= issued_at`; later targets need an older offset. The endpoint does not independently establish exact historical publication timestamps. Preserve that limitation for any freshness experiment.

The goal is not proved unattainable, and it has not been met. Reused January results must continue to be described as research diagnostics, even if a numerical threshold is eventually crossed. A newly frozen validation period is required for an independent generalization claim.
