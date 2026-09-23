# Nearby-grid weather experiment

Executed 23 September 2026. Adding nearby forecast fields gives a new exploratory full-January blend result of **0.166375 MAE**, versus the matching original curve's **0.176782**. However, the December-selected spatial model is worse than the preceding site-only wind model on January and January 31. **No recipe reaches 0.05; production is unchanged.**

| Recipe | December 1–29 selection | Full January | January 31 |
|---|---:|---:|---:|
| Empirical curve | 0.219982 | 0.176782 | 0.308140 |
| Previous site-only wind CatBoost, matched control | 0.197417 | 0.174079 | **0.215940** |
| **December-selected spatial wind CatBoost** | **0.193234** | 0.175032 | 0.249772 |
| Compact gradient wind CatBoost | 0.194996 | 0.176185 | 0.235746 |
| Spatial direct-power CatBoost | 0.196668 | 0.171458 | 0.247005 |
| Spatial histogram MAE boosting | 0.196548 | 0.183839 | 0.277887 |
| Fixed 50/50 curve / spatial direct-power blend | 0.198470 | **0.166375** | 0.277384 |

Scores use the same **1,488 January hourly turbine targets**, including an issuance-safe January 1 forecast, and 48 January 31 targets. December has 1,474 complete targets. Both fitting and live-observation rules retain the two-hour delay after interval end, and no January observations fit a model. Selection on December 1–29 is frozen before January evaluation.

The fixed blend improves full January by **5.89%** against the curve and approximately **1.72%** against the previous exploratory full-January blend's 0.169284. Its weight was fixed before scoring, but highlighting this recipe after seeing January is post-hoc selection. It gives worse January 31 accuracy than the preceding selected wind model. There is still no recipe that dominates every period.

The paired three-day bootstrap 95% interval for the blend's absolute monthly MAE gain is **[-0.008604, 0.027027]**, including zero. The December-selected spatial model's interval is **[-0.034437, 0.034421]**. One- and seven-day intervals also include zero. These are exploratory diagnostics, not adjusted for repeated research selection.

## New information and mathematical features

The design was fixed without target scoring: a **3×3 grid with 0.25-degree spacing** around the two turbines' midpoint, latitude 43.644174 and longitude 78.537216. GFS and ICON supply wind at 10/100 m, 100 m direction, sea-level pressure and temperature. June 2025 through January 2026 yields **52,920 location-hour rows**. All 20 requested provider/variable/offset columns are populated. Eight monthly responses are cached with content hashes; requested and returned coordinates are retained.

For each target, a 48-hour forecast offset is used only where `valid_time - 48h + 8h <= issued_at`; otherwise the 72-hour offset is used. Every raw offset column is removed before feature construction. This follows the same conditional availability assumption as the [previous experiment](FRESH_PROVIDER_BENCHMARK.md); exact historical publication is not verified. The [Previous Runs documentation](https://open-meteo.com/en/docs/previous-runs-api) describes fixed lead offsets. These are not reanalysis observations or a single initialized model run.

Features include each selected grid value, sine/cosine direction encoding, neighborhood mean/spread, the center value, and central east/north differences in pressure, wind speed and temperature. The compact gradient recipe omits individual peripheral values and direction encoding. Distances use the requested point spacing and a latitude-adjusted longitude distance. Returned cells are snapped to model grids, so these are **approximate gradient features**, not precise physical derivatives. Provider units and the nine distinct returned locations were independently checked.

The spatial wind model holds the previous site-only wind model's fitting rows, base features, CatBoost parameters, loss and curve mapping fixed, adding only these spatial features. The matched control reproduces all **2,962 December-plus-January predictions exactly**, maximum difference zero. Thus the spatial wind versus site wind comparison isolates the additional feature set; its December benefit fails to carry into January. Other recipe comparisons also change target/loss or calendar features and are not single-factor ablations.

CatBoost uses 350 iterations, depth 5, MAE loss, learning rate 0.03 and L2 penalty 10. Histogram boosting uses absolute-error loss, 300 iterations, 15 leaves, minimum leaf size 40 and L2 penalty 10. Both use seed 42, training-only imputation and two CPU threads. Wind predictions are mapped through the pre-cutoff empirical curve. The fixed blend uses equal weights.

## Reproduction

```powershell
python -m src.spatial_weather_benchmark --acquire
python -m src.spatial_weather_benchmark --output outputs/spatial-weather-benchmark-repeat
python -m pytest tests/test_spatial_weather_benchmark.py -q
```

Requires the prepared turbine data, optional research dependencies and preceding site-weather exports. Acquisition resumes from checksummed cached requests. Use a new benchmark output directory. Raw weather and full predictions remain ignored under `data/spatial-weather/` and `outputs/`. Compact reports, coverage, coordinates, control verification, uncertainty, January 31 predictions and hashes are in `examples/spatial-weather-benchmark/`.

Tests verify offset-boundary selection, invariance to poisoned unavailable forecast values and actual target power, gradient orientation, and exclusion of spatial information from the control. An independent code/data review found no critical leakage issue.

Weather attribution: Open-Meteo, NOAA GFS and DWD ICON, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); baseline inputs also use ECMWF IFS and JMA. No new February accuracy claim is possible without February actuals.
