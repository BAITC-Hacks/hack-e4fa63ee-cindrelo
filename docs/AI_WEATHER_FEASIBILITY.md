# Additional weather-source feasibility pilot

Checked 23 September 2026. **AIFS and GEM provide additional archived wind inputs, but this small pilot does not establish a forecasting improvement.** The frozen-curve AIFS diagnostic worsens January 31. No model was fitted or selected and no production forecast was changed.

## Verified requests

The pilot uses the [Open-Meteo Previous Runs API](https://open-meteo.com/en/docs/previous-runs-api) with explicit models, UTC timestamps, wind in m/s, and only `_previous_day2` and `_previous_day3`. Three requests cover 2025-06-15–16, 2025-12-15–16 and 2026-01-30–31, at both configured turbine coordinates. Each sample contains 48 hours per site (288 location-hours overall). This checks sample support, not complete June–January coverage. Exact accepted model identifiers also appear in the [official API schema](https://github.com/open-meteo/open-meteo/blob/main/openapi/forecast.yml).

| Accepted model | Wind speed/direction populated in every sampled hour at both offsets/sites | Unsupported/all-null tested heights |
|---|---|---|
| `ecmwf_aifs025_single` | `wind_speed_10m`, `wind_direction_10m`, `wind_speed_100m`, `wind_direction_100m` | 80 m, 120 m |
| `cmc_gem_gdps` | speed/direction at 10 m, 80 m, 120 m | 100 m |
| `ukmo_global_deterministic_10km` | 10 m speed; 10 m direction except June day3 | 80 m, 100 m, 120 m |

Append `_previous_day2` or `_previous_day3` to each base variable when requesting. Multiple-model response column names additionally end in the model identifier. For example: `wind_speed_100m_previous_day3_ecmwf_aifs025_single`.

`temperature_2m`, `surface_pressure`, `relative_humidity_2m`, `cloud_cover` and `precipitation` are complete for AIFS and GEM in all samples. UKMO has June-specific gaps: day3 temperature, pressure and wind direction are all-null; day2 cloud cover is all-null; precipitation is all-null at both offsets. Its December and January samples contain all five scalar variables. These are observed archive gaps, not claims that the provider never supports the fields.

The [ECMWF API documentation](https://open-meteo.com/en/docs/ecmwf-api) describes AIFS as 0.25-degree output with **native 6-hourly timesteps**; returned hourly values therefore do not represent independent hourly model information. The [GEM documentation](https://open-meteo.com/en/docs/gem-api) lists native 80/120 m winds, while [UKMO documentation](https://open-meteo.com/en/docs/ukmo-api) limits height-level fields to its UKV regional model, which does not cover this Kazakhstan site. Do not substitute 10 m wind for hub-height wind or invent missing 100 m measurements.

The [historical-forecast model table](https://open-meteo.com/en/docs/historical-forecast-api) lists AIFS Single from 20 February 2025, but that is not a guarantee of Previous Runs completeness. ECMWF's [version history](https://confluence.ecmwf.int/spaces/FCST/pages/496875126/Implementation%2Bof%2BAIFS%2BSingle%2Bv1) states that AIFS became operational on 25 February 2025 and v1.1 was implemented on 27 August 2025 after an earlier reverted attempt. The June and December samples thus span model-version changes. No pre-2025 AIFS training coverage is claimed.

## Conditional timing

Previous Runs documents day2/day3 as fixed 48/72-hour lead offsets, but responses do not expose exact historical initialization or publication timestamps. The pilot retains the project's **assumed eight-hour publication delay** and requires:

`valid_time - offset_days * 24h + 8h <= issued_at`

For target starts 24–47 hours after issue, day2 passes through lead 40 h; later targets require day3. The mixed diagnostic therefore uses 17 day2 and 7 day3 values per turbine. Actual historical publication compliance remains unverified. These fixed-offset series are also not a single named weather-model run. No dayzero data or reanalysis was requested.

## January 31 reused diagnostic

The existing December-trained empirical curves were loaded unchanged from `examples/backend/curve.json`. The January 31 comparison file supplied target power only for scoring; no target-hour measured wind was loaded or used. Both recipes below were specified before scoring. Their 48 pairs span the complete local January 31 day across two turbines.

| Forecast-to-frozen-curve recipe | MAE | Mean predicted power | Mean actual power |
|---|---:|---:|---:|
| Original saved forecast | 0.308140 | 0.4692 | 0.500625 |
| AIFS 100 m wind, day3 throughout | 0.458600 | 0.042025 | 0.500625 |
| AIFS 100 m wind, eligible day2 otherwise day3 | 0.333485 | 0.221634 | 0.500625 |

January 31 has already been repeatedly inspected. These scores are **reused historical diagnostics, not untouched validation or selection evidence**. They do not show that AIFS will help this site. A broader December-selection/January-diagnostic benchmark could evaluate AIFS and GEM after checking full training-window missingness, but this pilot neither selects them nor promises improvement. GEM and UKMO were checked for support only; no height conversion or power diagnostic was fitted for them.

## Local artifacts and verification

All pilot data are under `outputs/ai-weather-pilot/`: three immutable response JSON records containing exact request URLs, retrieval timestamps and response hashes; `pilot_weather.csv`; `support.csv`; `provenance.json`; and the two `jan31_reused_diagnostic*.csv` exports. Provenance includes the frozen-curve and comparison-file hashes.

Checked: response hashes match stored payloads; only offsets 2/3 were requested; both sites have identical support patterns; each diagnostic row satisfies the conditional timing inequality; both recipes score all 48 expected pairs; predictions remain within [0,1]. No training or core-code changes were performed.
