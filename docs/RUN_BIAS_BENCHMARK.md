# Same-run recent wind-bias correction

Executed 23 September 2026. Seven fixed recipes were tested: the original empirical curve plus 12/18-hour median wind corrections at strengths 0.25, 0.5 and 1.0. **The December-selected correction worsens January and January 31. The 0.05 goal remains unmet; production forecasts are unchanged.**

| Method | December 1–29 selection MAE | Full January MAE | January 31 MAE |
|---|---:|---:|---:|
| Original curve | 0.219982 | **0.176782** | **0.308140** |
| December-selected: 12-hour bias, strength 1.0 | **0.204082** | 0.185808 | 0.325641 |
| 12-hour bias, strength 0.25; diagnostic only | 0.214344 | 0.176586 | 0.313469 |

The selected recipe reduces December selection error by 7.23%, then worsens full January by 5.11% and January 31 by 5.68%. The smallest strength produces a negligible diagnostic January gain while worsening January 31; it was not the frozen winner and is not promoted.

## Difference from the earlier live-bias experiment

`src/live_bias.py` estimates recent errors using the latest available historical forecast for each already-observed target hour. This experiment instead uses the pre-issuance values of the **exact same eligible ECMWF run** that supplies the next-day forecast. Associating past errors with the current forecast run could improve bias transfer. A bounded October–December pilot justified checking that distinction; the complete January benchmark rejects it as a solution to the current goal.

For each issuance and turbine:

1. Read the immutable local weather cache for the issued forecast's run. No network retrieval or later-run substitution occurs. Verify cached run identity, assumed availability and wind units.
2. Include only complete turbine observations after their measurement hour ends plus two reporting hours. A 12-hour window contains precisely the target starts from `issue-14h` through `issue-3h`; the 18-hour window extends to `issue-20h`.
3. Match those observations to past forecast values within that same run. Require at least six pairs and a latest observation age of at most five hours after interval end. Otherwise apply zero correction. There were no neutral fallbacks in either month's measured run.
4. Calculate the median measured-minus-predicted wind residual; clip to ±4 m/s, multiply by the fixed recipe strength, and damp by `exp(-(horizon_hours-1)/48)`. Pass nonnegative corrected wind through the frozen empirical curve.

This is an additive amplitude correction. It cannot reconstruct an incorrectly forecast intraday wind shape. It is also different from the phase-shift pilot documented in [NEXT_INFORMATION_AUDIT.md](NEXT_INFORMATION_AUDIT.md).

## Availability and evaluation

The 2-hour reporting delay applies to both live observations and curve-fitting labels. Models remain frozen before January. January 1 uses a separate curve fitted for the December 31 midnight issuance; January 2–31 uses the January 1 midnight fit. Selection uses December 1–29 targets, whose labels are available before the December 31 boundary issuance. `selection.json` is written before January evaluation.

All methods score **1,488 January pairs** (744 per turbine) and **48 January 31 pairs**. Targets are hourly interval starts 24–47 hours after issuance, labelled horizons 25–48. December full-month scores use 1,474 complete target pairs. January has been repeatedly inspected in earlier research, so this remains reused historical evaluation even though this batch froze its selection before evaluating January.

The selected model's full-January absolute MAE gain is **−0.009027**. Paired day-block bootstrap 95% intervals are `[-0.022991, 0.004300]` for one-day blocks, `[-0.019964, 0.001940]` for three-day blocks, and `[-0.016761, -0.001373]` for seven-day blocks. These exploratory intervals are not corrected for prior model searches. January 31 is only one target day; its 48 correlated turbine-hours do not support an independent day-level significance claim.

Weather availability still assumes initialization plus eight hours. The existing ECMWF archive may contain hindcasts, and exact historical publication provenance remains unverified. This experiment improves causal checks within that assumption; it does not resolve the archive limitation.

## Artifacts and reproduction

- [Compact results and hashes](../examples/run-bias-benchmark/) include the protocol, pre-January selection, scores, uncertainty and source checksums.
- Local `outputs/run-bias-benchmark/predictions.csv` retains every scored row; `bias_state.csv` retains the exact source run, weather content hash, paired-history counts and correction per issuance.
- Four focused tests verify future-observation invariance, exact reporting windows, stale fallback, rejection of wrong/future runs, bounded corrections and the full-month boundary fit.

After acquiring the existing research weather caches:

```powershell
python -m src.run_bias_benchmark --output outputs/run-bias-benchmark-repeat
python -m pytest -q tests/test_run_bias_benchmark.py
```

Use a new output directory. The runner never replaces production artifacts. No extra model search on January was performed after these results.
