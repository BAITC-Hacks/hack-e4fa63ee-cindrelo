# Long-history sequence benchmark

Executed 23 September 2026. This isolated experiment tests whether a full week of past wind and power, together with the issued weather trajectory, can repair the difficult January forecast. **The requested maximum 0.05 MAE is not reached.** It does not change production models or published forecasts.

## Measured results

The December-selected method is the fixed 50/50 blend of the empirical curve and weather-conditioned sequence histogram MAE model. All values below are normalized-power MAE; lower is better.

| Method | December 1–29 selection | Full January | January 2–31 | January 31 |
|---|---:|---:|---:|---:|
| Empirical curve | 0.219982 | 0.176782 | 0.176235 | 0.308140 |
| **Selected curve / sequence histogram blend** | **0.219177** | **0.176223** | **0.175556** | 0.253074 |
| Sequence histogram, unblended | 0.232198 | 0.192038 | 0.190821 | **0.227847** |
| Long-history extra trees, no forecast weather | 0.330280 | 0.308912 | 0.305579 | 0.335114 |
| Long-history ridge, penalty 1,000 | 0.350959 | 0.312539 | 0.305701 | 0.352283 |

The selected blend improves full January by **0.32%** and January 31 by **17.87%**. The full-month absolute gain is only 0.000559. Its paired three-day block-bootstrap 95% interval is **[-0.01080, 0.01127]**, spanning no improvement. One- and seven-day intervals also span zero; none account for the wider history of repeated January research. There is no established significant monthly improvement.

The unblended model is better on January 31 but worse over January and was not the December winner. The earlier exploratory hourly histogram blend had January 2–31 MAE 0.172369, better than this sequence blend's 0.175556, while the sequence blend is somewhat better on January 31 (0.253074 versus 0.258783). This is a tradeoff, not a new model dominating the earlier best on both metrics.

Longer telemetry history alone performs substantially worse than issued-weather methods. These results do not establish a mathematical lower bound; they show that this bounded set of direct sequence models cannot reconstruct the missing future weather evolution from a week of past telemetry.

The authoritative completed run is `outputs/sequence-benchmark-v3/`. Two earlier launches stopped at a coverage assertion before any fitting because December's incomplete observations had not yet been allowed and reported. No scores from those incomplete launches are used. Source and full-prediction hashes are retained in the compact artifacts.

## Mathematical setup

At an issuance time `t`, the newest permitted observation is the interval starting at `t - 3 hours`: one hour for the interval to end and two hours for reporting. The input vector contains the preceding 168 hourly values of both turbine power and measured wind, summaries over 6/24/48/72/168 hours, turbine identity and annual seasonality. Missing past values remain missing until a training-only median imputer processes them; there is no backward filling from a later observation.

Each example predicts an entire 24-element vector: target intervals starting at `t + 24` through `t + 47 hours`, corresponding to horizons 25–48. Direct ridge minimizes the squared vector error plus an L2 coefficient penalty. It learns the temporal response jointly without recursively inserting its own predictions. Extra trees shares each tree's partition across the 24 outputs. Histogram boosting fits a separate absolute-error model for each horizon.

Seven recipes are fixed before evaluation:

- Long-history direct ridge, penalties 100 and 1,000.
- Long-history direct extra trees: 180 trees, maximum depth 20, minimum leaf size 8, feature fraction 0.65.
- Weather-conditioned direct ridge, penalties 100 and 1,000.
- Weather-conditioned extra trees with the same parameters.
- Weather-conditioned histogram MAE boosting: 120 iterations per horizon, seven leaves, minimum leaf 20, learning rate 0.04, L2 penalty 20, no random early-stopping split.

Each recipe also has a predefined 50/50 blend with the original empirical curve. This gives 15 methods including the curve. Both ridge and trees use at most two CPU threads. All preprocessing is fitted on training rows only.

The weather-conditioned input includes all 48 forecast hours of wind at 10/100 m, temperature and sine/cosine wind direction from the same eligible issued weather path. It never reads target-hour measurements. Pure autoregressive models use the longer supplied history back to March 2023; weather-conditioned models use only the available archived pairs beginning in June 2025, retaining previously documented archive gaps.

This experiment inherits the repository's raw-timezone and archived-weather publication assumptions. Passing timestamp guards does not establish historical as-issued provenance for the weather archive.

## Causality and coverage

Training labels obey the same reporting delay as live inputs. A complete 24-target training vector is usable only when its final interval has ended plus two hours. The empirical curve baseline also excludes those last unavailable hours.

Requiring a whole output vector excludes a partially matured final target day from sequence-model training. The curve can still use individually matured observations from that day. The fitting artifact records sample counts and last label-availability timestamps for every recipe and cutoff.

The first target day of each month needs an issuance on the preceding day. It therefore gets a separate model fitted at that earlier issuance; the remaining days use the first-of-month model cutoff. The January 1 model never uses December 31 measurements. January observations may enter later January input histories after their reporting delay, but never enter fitting.

Recipe selection uses December 1–29, so all selection labels would be available before the December 31 issuance for January 1. The selection record is written before computing January results. Full December is diagnostic. January was inspected during earlier research and remains a reused historical test, not independent validation.

December has 1,474 available observation pairs out of 1,488 expected: 14 hourly turbine records lack six ten-minute samples. All methods use the same available rows; missing targets are not imputed. January must have all 1,488 pairs (744 per turbine), and January 31 must have all 48 pairs. A separate January 2–31 score supports comparison with prior experiments that omitted the first target day.

## Reproduction

```powershell
python -m src.sequence_benchmark --output outputs/sequence-benchmark-repeat
python -m pytest tests/test_sequence_benchmark.py -q
```

Use a new output directory. This requires the prepared turbine data and earlier research weather exports. Full predictions are local research outputs; compact protocol, frozen selection, fitting cutoffs, metrics and report are under `examples/sequence-benchmark/` after completion. The tests cover exact reporting boundaries, missing-observation handling, future-observation invariance and exclusion of unavailable weather.
