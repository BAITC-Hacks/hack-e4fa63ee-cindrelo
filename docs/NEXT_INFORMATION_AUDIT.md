# Information audit and bounded phase-correction pilot

23 September 2026. This audit used existing cached forecasts and observations. It did not inspect January outcomes to choose phase-correction settings, alter production forecasts, or change shared research code.

## What the organizer actually specifies

I re-read both pages of the original `docs/HackAlem AI_ Agentic AI для прогнозирования выработки ВЭС.pdf`. It requires hourly 24–48-hour generation forecasts, a February 1–28 replay, and archived weather available at each historical forecast origin. The supplied target is described as normalized line-side active power. The brief does **not** specify MAE, a 0.05 threshold, the normalization denominator, timestamp timezone/interval semantics, rated capacity, or a numerical accuracy-scoring formula. Its judging rubric allocates 25/25/25/15/10 points to working scope, technical implementation, reproducibility, applicability, and development/originality.

The 0.05 requirement remains the user's explicit research objective. The brief provides no basis to relabel hourly error as daily-energy error, alter the normalization, shorten the horizon, or count February predictions without actuals as validated success. January 31 and February 1 in the original replay wording are forecast-origin examples, whereas the separate requested CSVs in this repository have explicitly documented target-date semantics.

## Can observed phase error be carried into tomorrow?

The existing ECMWF cache contains values from the same eligible forecast run before issuance. Those values can be paired with already-reported turbine wind to estimate whether the forecast's wind evolution is early or late. This introduces causal trajectory adjustment without a new data source.

I ran a small, fixed pilot over October–December only:

- Fit the empirical curve using complete observations available at month start with a two-hour delay after interval end.
- At each existing daily issuance, use only measurements whose hour ended at least two hours earlier.
- Pair a 12- or 18-hour recent history with the **same eligible ECMWF run** under integer shifts bounded by three or six hours. Compare shifts on the intersection of available historical hours; require six pairs or use an unchanged forecast.
- Estimate the median wind bias for each shift. Choose the shift minimizing mean absolute centered residual plus `0.05 * abs(shift)` m/s.
- Apply the chosen shift to the issued forecast trajectory. Test either no amplitude correction or half the median bias, clipped to ±4 m/s and damped by `exp(-(horizon-1)/48)`.
- Select among eight recipes using combined October–November error; evaluate the fixed winner on December. Curves never see the evaluation month's labels; January is not used.

| Method | October MAE | November MAE | December MAE |
|---|---:|---:|---:|
| Original empirical curve, same rows | 0.171173 | 0.190132 | 0.228439 |
| Selected phase+amplitude correction: 12 h, ±3 h, half bias | 0.174758 | 0.182770 | 0.228369 |
| Bias-only control: 12 h, zero shift, same half bias | **0.166453** | **0.178398** | **0.215603** |

The selected phase recipe improves combined October–November from 0.180883 to 0.178861, but its December improvement is only 0.000070, about 0.03%. Pure phase variants worsen December to 0.2401–0.2453. The subsequently evaluated bias-only control outperforms the phase recipe in all three months; thus this pilot does not support carrying the recent phase estimate forward 24–48 hours. Recent bias adjustment is already covered by earlier research and does not constitute evidence of a route to 0.05.

Coverage is the existing origin-window convention, excluding each month's first target day: 1,326 October pairs, 1,392 November pairs and 1,426 December pairs after complete-hour target filtering. All compared causal recipes use identical rows. Some shift/history combinations collapse to the same usable pairs because a cached run does not extend far enough into the past; missing history produces the explicit neutral fallback. These are bounded exploratory diagnostic results, not a new full-month benchmark or operational release.

As a separate **hindsight-only family diagnostic**, allowing a different shift (±12 h) and constant wind bias (−3 to +3 m/s in 0.5 m/s steps) for each December turbine/day, chosen using that day's actual power, yields a mean of per-turbine-day MAEs of approximately 0.1243 over 60 turbine/days. This is unavailable future information and an unweighted day statistic; it is not a usable prediction or a lower bound on other models. Even this flexible phase-and-constant-bias family does not explain the entire weather error.

## Where useful information is still missing

The strongest actionable remaining experiment changes meteorological information: nearby grid-cell wind and spatial pressure gradients, or a materially different archived weather model. Root work is already evaluating these possibilities. They can encode local flow and approaching regime changes that the single-site forecast and scalar recent-wind summary miss. Prefer a matched control using exactly the same rows, model, training cutoffs and provider offsets; otherwise apparent gains cannot be attributed to the new information.

Useful metadata still absent from the supplied files include sensor/hub height, authoritative timezone and interval convention, and the power normalization denominator. Operational measurements such as wind direction, turbine status, yaw/pitch or curtailment flags are also absent. These could support site/operating-regime modeling, but there is no evidence that any one missing field guarantees the requested accuracy.

Do not spend an unlimited sequence of January-tuned parameter sweeps searching for an apparently successful number. The current evidence supports targeted new-input experiments with frozen recipes and matched controls. It neither meets the goal nor proves the goal mathematically impossible.
