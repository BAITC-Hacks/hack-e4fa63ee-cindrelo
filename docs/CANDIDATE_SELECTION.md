# Selected forecasting candidate

The selected research candidate is **`add_wind_cat`**, the AIFS/GEM CatBoost wind-calibration model followed by the empirical power curve. This decision uses the recorded **December 1–29** selection, not the smallest reused January error.

| Comparable candidate | December selection MAE | Full January MAE | January 31 MAE |
|---|---:|---:|---:|
| **AIFS/GEM wind model** | **0.192289** | **0.160737** | 0.242786 |
| Spatial wind model | 0.193234 | 0.175032 | 0.249772 |
| AIFS/GEM direct-power challenger | 0.194609 | 0.157499 | 0.234811 |
| Prior site-wind model | 0.197417 | 0.174079 | **0.215940** |

All rows use the same full-month boundary handling and reporting delay. The direct-power challenger's better January score does not override December selection. The prior site-wind model remains better on January 31. No candidate satisfies the requested 0.05 MAE on both periods; the goal remains unmet.

The machine-readable decision is [selection.json](../examples/selected-candidate/selection.json). It links the original protocol, selection and report without changing those experiment records. Selection labels were available before the December 31 issuance used for January 1. January consists of 1,488 target pairs and is a repeatedly examined diagnostic; January 31 contains 48 pairs.

## Serving compatibility

The live dashboard now defaults to this selected candidate. Portable CatBoost models and explicit imputation/curve metadata live in `examples/candidate/`; the backend runtime does not import research dependencies. The December 31 boundary bundle and January 1 bundle contain no January fitting labels. Checksums and configuration checks reject mismatched artifacts.

The full 48-hour serving policy is explicit: **hours 1–24 use the empirical curve; hours 25–48 use AIFS/GEM wind calibration followed by that curve**. Within-forecast features for the learned model are computed only over hours 25–48, preserving the research recipe. Full January research predictions match the portable implementation to numerical precision. A 12-hour update uses the same fixed recipe but is not separately accuracy-validated. The dashboard metrics cover the historical day-ahead evaluation, not a measured full-48-hour or live-update score.

The provider retrieves IFS plus GFS, ICON, JMA, AIFS and GEM weather. Only eligible 48/72-hour offsets enter features and input identity. Validated refreshes use isolated runtime caches; bundled data and research archives remain immutable. Failed retrievals can use validated cached responses with an explicit trace; prediction recovery is labelled as empirical-curve fallback.

Use `python -m src cycle --offline` for the cached candidate demonstration or **Run forecast cycle** in Horizon. `python -m src cycle --agent` uses live OpenAI orchestration and weather retrieval. See [the integration review](PR13_PR14_INTEGRATION.md) for verification. The original February export and baseline CLI replay remain reproducible separately.

Historical publication remains conditional on the assumed eight-hour delay. Both labels and live observations require two reporting hours after interval end. The user has no further hub-height, timestamp or curtailment metadata, so the existing disclosed assumptions are retained.
