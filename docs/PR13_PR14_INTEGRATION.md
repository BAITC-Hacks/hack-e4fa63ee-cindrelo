# Dashboard candidate integration

PR #14 was already merged into `main` as `be4b3cb`. PR #13 integrates its one-button forecast service with the December-selected AIFS/GEM candidate. The later PR #15 Horizon styling is also included before the final merge.

## Selected model and serving behavior

`add_wind_cat` won the recorded December selection: MAE **0.192289**, versus **0.194609** for the direct-power AIFS/GEM challenger. Its full January day-ahead diagnostic MAE is **0.160737** and January 31 MAE is **0.242786**. January was repeatedly examined during research, so these are diagnostic results. The 0.05 target remains unmet; February actuals are unavailable.

The dashboard's default method is `aifs_gem`. Hours 1–24 use the empirical curve; hours 25–48 use the selected CatBoost wind calibration followed by that curve. Learned trajectory features use only the day-ahead window. Both turbine outputs retain the complete 48-hour dashboard contract. Twelve-hour revisions use the same recipe but have not been independently accuracy-validated.

The portable model reproduces all **1,488** January research predictions with a maximum absolute difference of **1.11e-16**. Two temporal bundles handle the January 1 boundary without January training labels. Training labels require two reporting hours after interval end. Inference imports no research modules or scikit-learn. See [artifact verification](../examples/candidate/verification.json), [candidate selection](CANDIDATE_SELECTION.md) and [the serving manifest](../examples/candidate/manifest.json).

## Compatibility fixes

- The live service prefers the checksummed native candidate bundle. Model/configuration or metadata-integrity failures stop the cycle rather than silently selecting an old model.
- Weather retrieval adds GFS/ICON/JMA/AIFS/GEM Previous Runs inputs to IFS. Eligible offsets use the disclosed assumed eight-hour publication delay. Original publication times remain unverified, so outputs retain degraded status.
- Forecast identity covers all selected numerical weather inputs, provider grids, eligibility assumptions and the model version. Retrieval timestamps and unselected future offsets do not change identity.
- Runtime caches live under `outputs/`. A refreshed response must pass units, finite-value and full-horizon validation before replacing a cache entry. Failed refreshes use a validated cache with an explicit trace. Research archives and committed examples are preserved.
- A failed native CatBoost prediction can recover through the empirical curve, with an explicit model version and fallback warning.
- Each forecast stores its own provenance. When the initial forecast changes but its revision deduplicates, the returned revision retains its original metadata. Failed cycles keep the previous completed snapshot.
- Dashboard metrics describe only the selected model's measured January day-ahead diagnostic, with two turbine rows and 1,488 pairs. They never inherit old curve metrics or imply measured full-horizon/revision accuracy.
- JSON checksums tolerate Git's LF/CRLF conversion; native model bytes remain checked exactly. This preserves portability across Windows and Unix checkouts.

## Rehearsal

The default offline cycle and the online deterministic cycle both published **192** predictions across two issuances. The online run logged **eight external weather responses**. Their numerical predictions matched exactly. A clean Windows/Python 3.12 environment installed only `requirements-backend.txt` and ran the offline candidate cycle with scikit-learn absent.

The Horizon browser rehearsal completed the candidate cycle, selected its 12-hour revision, displayed the previous forecast and actuals, and exposed the candidate's day-ahead evaluation window. The missing-API-key path preserved the prior view. The final combined branch passed **95 tests and 12 subtests** with `python -m pytest -q` on Windows/Python 3.12. A separate checkout exported from Git with Unix LF line endings also completed the offline cycle in the backend-only environment, preserving both forecast IDs and all predictions. The final browser run used the live OpenAI controller for both issuances: **two completed agent runs, eight external weather responses, 192 predictions and zero failed tool events**. The predictions and IDs exactly matched the deterministic online cycle. See [the verification record](../examples/candidate/integration-verification.json) and [sanitized live trace](../examples/candidate/live-events.jsonl).

```sh
python -m src cycle --offline
python -m src cycle --agent --output outputs/candidate-live
python -m pytest -q
```

Use the command's returned snapshot directory for `CINDRELO_OUTPUT_DIR`. The original `demo`, `forecast`, `replay` and committed February submission retain their baseline behavior; the new `cycle` command calls the same candidate service as the dashboard.
