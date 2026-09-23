# Goal progress: MAE at most 0.05 on January and January 31

The goal remains **unachieved and active**. This step acquired alternative forecast inputs and completed a new benchmark. It is progress, not a change to the target or a claim that the target is attainable with current data.

## New inputs

Retrieved 16 checksummed monthly responses for the two turbine locations, June 2025–January 2026: 11,760 location-hour rows. Data use Open-Meteo Previous Runs with **72-hour offsets** for GFS, ICON and JMA, including wind speed/direction, temperature, pressure, humidity, cloud and precipitation. JMA 100 m wind is unsupported/all-null and is excluded based on training availability; its 10 m wind remains usable. Other requested columns are complete.

According to the [provider documentation](https://open-meteo.com/en/docs/previous-runs-api), these offsets represent predictions made at fixed lead times. For targets 24–47 hours after issuance, a 72-hour offset plus the existing assumed eight-hour publication delay leaves at least 17 hours before issuance. This is a conservative **conditional eligibility calculation**, not proof of actual historical publication: the response does not expose exact initialization/publication timestamps. Day-zero stitched forecasts and reanalysis were not used. [Single-run archives](https://open-meteo.com/en/docs/single-runs-api) for most alternative models do not cover January 2026, which is why fixed-offset data were investigated.

## Experiment and result

Tested nine challengers plus the original curve: multi-provider histogram boosting, CatBoost, extra trees, live-input variants, provider-specific enhancements and wind postprocessing. Provider-specific variants add the named provider to existing ECMWF features. Models train on June–November for December selection and June–December for January. January is a reused historical diagnostic; it does not fit models or change the frozen December winner. Live observations use the previously authorized two-hour reporting delay.

| Method | December MAE | January MAE | January 31 MAE |
|---|---:|---:|---:|
| Existing curve | 0.228440 | 0.176235 | 0.308140 |
| December-selected multi-provider wind postprocessing | **0.200097** | 0.190675 | 0.330511 |
| ICON-enhanced CatBoost | 0.213525 | **0.174088** | 0.305395 |
| Multi-provider CatBoost with live inputs | 0.205760 | 0.174150 | 0.312404 |
| JMA-enhanced CatBoost | 0.221383 | 0.175688 | **0.283120** |

None meet 0.05 for either required period. The December-selected model fails January. Small diagnostic monthly improvements do not justify replacing the official forecast. These scores use the original issuance-window convention: January day-ahead targets January 2–31, 1,440 pairs; January 31 has 48 pairs. Final completion will require explicitly accounting for the January 1 boundary too, without backdating training/selection.

Earlier-history power autocorrelation is approximately 0.90 at one hour, 0.41 at six hours and 0.10 at 24 hours. This helps explain weak persistence but is not an impossibility proof or a formal accuracy bound.

## Remaining path toward the full objective

1. Test fresher fixed-offset forecast inputs where their assumed availability still precedes issuance: 48-hour offsets can satisfy the eight-hour-delay rule for target leads up to 40 hours, while later targets need older offsets. Keep exact publication provenance uncertainty explicit.
2. Evaluate temporal forecasting models that use longer pre-issuance observation sequences, rather than only recent summary features; keep reporting delay and training cutoffs enforced.
3. Audit time/height/spatial alignment against authoritative metadata; do not choose a convenient timezone from January error alone.
4. Continue to require MAE <=0.05 on both the full January scope and January 31. Preserve the original predictions and do not count measured-future-wind diagnostics as forecasts.

## Reproduction

```powershell
python -m src.provider_weather
python -m src.provider_benchmark --output outputs/provider-benchmark-repeat
```

Requires the existing research dependencies and earlier weather exports. Responses persist in ignored `data/provider-weather/`; combined input and row predictions remain under `outputs/provider-weather/` and `outputs/provider-benchmark/`. Compact protocol, selection, metrics, support audit, source links and hashes are in `examples/provider-benchmark/`. No production artifacts changed.
