# Five-hour implementation recommendation

Reviewed the complete two-page brief and all records in both CSV files. This is a proposal, not a claim that a forecasting system or measured model improvement already exists.

## Decision

Build a Python application that replays archived weather forecasts, predicts hourly normalized power for each turbine, and exposes a bounded agent that fetches, validates, forecasts, analyses, and reruns when inputs change. Use CatBoost for the main candidate, an empirical wind-to-power curve as the fallback, and Streamlit for the demonstration. Keep training and numerical predictions deterministic; give the agent tools for operational decisions.

The strongest submission is a reproducible, historically valid forecasting loop with visible evidence of its decisions. Reserve the final hour for documentation, a clean-start check, and rehearsal. Do not spend the deadline on deep-learning experiments or a separate frontend/backend stack.

## What the brief actually requires

- Train using the supplied historical turbine data through January 31, 2026.
- Produce hourly forecasts covering the following 24–48 hours.
- Replay successive forecast origins through February 1–28, 2026, starting on January 31.
- Automatically obtain weather forecasts using the turbine coordinates.
- Use weather forecasts available at each historical forecast origin, never subsequently observed weather.
- Complete the autonomous loop: fetch → prepare → predict → analyse → rerun on updated inputs.

The brief does not specify an error metric, file schema, daily issuance hour, timezone, rated capacities, hub heights, or how overlapping forecasts should be scored. Make these configurable and document assumptions.

| Criterion | Points | Evidence to show |
|---|---:|---|
| Task fit and working scenario | 25 | February replay, hourly forecasts, both turbines, archived weather |
| Technical implementation | 25 | Working tool-using agent, temporal guards, model and fallback, rerun trace |
| README and reproducibility | 25 | Setup/run commands, dependency versions, data provenance, cached example, evaluation procedure |
| Value and applicability | 15 | Baseline comparison, forecast revisions, operational alerts, downloadable predictions |
| Development potential and originality | 10 | Auditable forecast lineage and graceful recovery; explain extensions |

There is no separate numerical-accuracy scoring formula in the PDF. Accuracy is still useful evidence of applicability, but is not a reason to sacrifice the working loop or documentation.

## Findings from the supplied data

| Property | Turbine 1 | Turbine 2 |
|---|---:|---:|
| Records | 142,360 | 149,499 |
| First timestamp | 2023-03-11 00:00 | 2023-03-11 00:00 |
| Last timestamp | 2026-01-31 23:50 | 2026-01-31 23:50 |
| Complete six-sample hours | 23,667 | 24,785 |
| Duplicate timestamps | 0 | 0 |
| Missing/nonfinite numeric cells | 0 | 0 |
| Normalized power range | 0–1 | 0–1 |
| June 2024 records | 27 | 4,263 |

Both files contain ID, timestamp, wind speed in m/s, normalized active power, and ambient temperature. Nominal sampling is ten minutes. Missing timestamps exist despite complete cells. There are 141,357 timestamps shared by both turbines; do not align them by row number. Turbine 1 has substantial missing coverage in May–July 2024. Treat gaps as unknown, not zero generation or confirmed outages.

Despite the filenames mentioning February 28, neither CSV contains February 2026 observations. Therefore:

1. February is an unlabelled prediction/export period; do not claim February accuracy.
2. Use an earlier chronological holdout for measured performance.
3. During February replay, January measurements become stale. Do not invent fresh power lags or wind observations.

Coordinates resolved from the PDF's Google Maps links:

- Turbine 1: `43.645150, 78.535604`.
- Turbine 2: `43.643198, 78.538828`.

These close locations may map to the same weather grid cell; retain separate turbine calibration and report the returned grid coordinates.

## Approaches considered

| Approach | Strength | Limitation | Decision |
|---|---|---|---|
| Persistence and historical mean | Immediate reference scores | Weak weather response; stale during February | Evaluation baselines |
| Empirical wind-to-power curve | Fast, interpretable, uses turbine history | Forecast wind differs from measured turbine wind | First working forecast and fallback |
| Weather-driven CatBoost | Learns local weather-to-power bias and nonlinear effects | Needs correctly aligned forecast/target pairs | Recommended main candidate |
| LSTM/Transformer or large ensemble | Possible later research | Extra tuning, integration and validation work | Defer |
| LLM-generated numeric power forecasts | Easy narrative demo | Poor numerical control and reproducibility | Do not use |

## Weather acquisition: first technical gate

Start with [Open-Meteo Single Runs](https://open-meteo.com/en/docs/single-runs-api), explicitly selecting `models=ecmwf_ifs`. Its documented date coverage includes the test period. Most other models in this endpoint begin in April 2026, too late for this case. The documentation also labels early IFS coverage as hindcasts: establish whether the selected January/February records are operational runs, and disclose unresolved provenance rather than claiming proof of historical availability.

The following request was tested successfully during this review (HTTP 200, 72 hourly values, no nulls in the four variables):

```text
https://single-runs-api.open-meteo.com/v1/forecast?latitude=43.645150&longitude=78.535604&run=2026-01-31T00:00&models=ecmwf_ifs&hourly=wind_speed_10m,wind_speed_100m,wind_direction_100m,temperature_2m&wind_speed_unit=ms&forecast_days=3
```

The returned weather grid point was `43.620384, 78.47891`. A successful response confirms retrieval, not historical publication provenance or whole-month coverage. Test late February and a validation-period date next, then cache the full required set.

The ordinary [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api) stitches the early hours of successive runs. It must not be passed off as a complete day-ahead forecast issued at one fixed origin. [Previous Runs](https://open-meteo.com/en/docs/previous-runs-api) provides fixed lead-time series, but also needs careful availability alignment; it is not an automatic substitute for a single issuance.

Weather run initialization is earlier than public availability. The provider documents this distinction in its [Single Runs explanation](https://openmeteo.substack.com/p/single-runs-api). Select runs using a documented publication delay plus a conservative margin, not merely `run_time <= issue_time`. Keep UTC internally; resolve the CSV timezone separately. Do not infer timezone from the developer laptop or apply an unverified fixed offset to three years of data.

Cache raw responses plus request parameters, requested coordinates, returned coordinates, run time, assumed/verified availability time, retrieval time, units and content hash. Retrieval time today is different from historical availability time. Use bounded retries and respect provider limits.

If exact historical provenance cannot be established quickly, raise that specific issue with organizers. Continue the engineering demo with the limitation visible. Reanalysis, modern hindcasts and actual future turbine wind are not silent substitutes for the brief's as-issued requirement.

## Model and evaluation recipe

1. Parse each file separately, sort timestamps, validate ranges and build a ten-minute time grid. Preserve coverage flags. Aggregate normalized power with an hourly mean, not a sum; start by requiring all six samples for training/evaluation targets. Confirm whether timestamps denote interval starts or ends.
2. Build a per-turbine empirical curve from pre-cutoff wind and power, using wind bins and interpolation with bounded outputs. This is a fallback, not evidence of 48-hour skill when evaluated against observed future wind.
3. Download daily archived forecast runs for a manageable recent training period, initially September–December 2025, and for January validation and February inference. Expand history only after the loop works. Use older local observations for the empirical curve. A smaller properly aligned forecast dataset is preferable to a large leaked one.
4. Train one pooled CatBoost regressor with turbine ID as a categorical feature. Inputs: forecast wind at 10/100 m, wind-direction sine/cosine, forecast temperature, hour/season and forecast lead time. Include both application lead time and weather-run lead time if issuance and initialization differ. Clip predictions to the observed normalized range [0,1].
5. Omit recent-power features from the first model so February replay works without fresh telemetry. If later added, calculate them strictly before issuance and carry age/missingness flags; validate the same stale-data behavior that the demo will use.
6. Choose candidate settings on December 2025 using earlier training data. Freeze settings, refit through December 31 and assess daily 24/48-hour replay over January 2026. Fit preprocessing only on training data; split by time and purge training examples whose targets cross the cutoff. Do not use random row splits.
7. Compare MAE and RMSE against last-known-power persistence, a training-only per-turbine mean, and the weather-driven empirical curve. Report each turbine and horizons 1–24 and 25–48 separately, alongside coverage. These are proposed diagnostics, not organizer-defined metrics. Avoid MAPE because power can be zero.
8. Select the final candidate using pre-February evidence and refit through January 31. Forecast February without target values. If honest validation does not beat the fallback, ship the fallback and report the result.

Normalized active power is not MW or MWh. Export per-turbine normalized predictions. If rated capacities and normalization semantics are confirmed, hourly energy is `normalized_power × rated_MW × 1 hour`, and station energy is the sum. Without capacities, a mean across turbines must be labelled an equal-weight normalized index, not actual station production.

Optional, only after validation: construct an empirical error band from pre-test residuals, report its held-out coverage, and label it empirical. Do not fabricate confidence levels or financial savings.

## Agent and demonstration

Use one orchestrator with a small set of callable tools rather than several independent agents. Suggested tools: `fetch_weather`, `validate_inputs`, `predict_power`, `compare_forecasts`, `publish_forecast`. If an LLM key is available, let the LLM choose permitted recovery actions and explain tool-produced results using structured responses. Enforce timestamps, allowed models, retry budgets and output bounds in code. A narrative generated after a fixed script is not, by itself, convincing agentic behavior.

```text
New issue time / updated input hash
  → choose an eligible archived weather run
  → fetch and validate coverage, units and availability
  → run selected model or explicit fallback
  → calculate forecast changes and data-quality alerts
  → publish versioned hourly output and an execution trace
  → rerun when a newer eligible input appears
```

If a call fails, retry once and use a cached eligible run if it still covers the target horizon. Otherwise mark the run degraded/failed; a persistence fallback can preserve the UI but must not be presented as meeting the weather-driven requirement. Never use a later run to repair an earlier forecast origin.

For the update demo, advance the simulated clock to when a newer archived run becomes eligible, then show the revised prediction on overlapping target hours. Keep the previous version visible. In live mode, a timer can detect new input hashes. Deduplicate unchanged inputs and keep training outside the online loop.

Streamlit screen: issue time, turbine selector, next-48-hour chart, earlier-versus-revised forecast, archive/run metadata, agent tool trace, validation score table, and CSV download. Show actuals only for labelled validation periods. Avoid a chat-only interface.

Minimum export schema:

The concrete dashboard contract, including UTC interval-start timestamps and horizon numbering, is defined in [TEAMMATE_SPEC.md](TEAMMATE_SPEC.md). Use that contract for implementation.

```text
forecast_id,issued_at,valid_time,turbine_id,horizon_hours,
power_normalized,weather_run_time,weather_available_at,
model_version,input_hash,status
```

Generate a 48-hour forecast for each daily origin; retain overlapping forecasts with their issuance metadata. Export a separate day-ahead table containing one prediction per turbine per February hour (672 per turbine, 1,344 total under the agreed local-calendar convention). Choose the forecast-origin rule before running; do not select whichever prediction later looks best. Include late-January origins needed to cover the start of February, and distinguish any March targets from the scored February window.

## Five-hour schedule from implementation start

| Time | Work | Exit condition |
|---|---|---|
| 0:00–0:30 | Confirm assumptions, test archive dates/provenance, parse/resample CSVs, lock export schema | One valid weather run joined to hourly turbine targets |
| 0:30–1:15 | Cache weather, fit empirical curve, implement issuance-aware replay and export | First genuine 48-hour forecast for both turbines |
| 1:15–2:15 | Train CatBoost candidate, chronological evaluation and baseline table | Honest January scores and selected model |
| 2:15–3:00 | Agent tools, validation/recovery branches, updated-input rerun | Visible autonomous run and versioned revision |
| 3:00–4:00 | Dashboard, full February export, provenance display | Demo works end to end; outputs saved |
| 4:00–5:00 | README, cached offline example, focused checks, clean-start run and pitch | Another person can reproduce and explain the submission |

Team confirmed: two people, one experienced developer and one teammate comfortable with design and graphs. The experienced developer owns data/weather, model/evaluation, replay and the agent. The teammate owns the Streamlit layout/charts, screenshots, README presentation and rehearsal. Agree a sample CSV schema immediately so interface work can proceed using clearly labelled fixture data; replace it with real predictions as soon as the first export exists.

Teammate deliverables by hour: (1) a simple layout and chart using the agreed fixture; (2) forecast-versus-actual validation chart plus baseline score table; (3) earlier-versus-revised forecast chart and run metadata panel; (4) integrated dashboard and screenshots; (5) follow README instructions independently and rehearse the pitch. Keep the teammate's work inside `app.py` and presentation assets to minimize integration conflicts. The experienced developer provides the underlying CSV/JSON artifacts and a callable agent entry point.

Time limits: archive problems get an early escalation, not hours of silent investigation. If CatBoost cannot be validated by 2:15, retain the empirical curve and finish the required loop. After 4:00, freeze features. Cut extra weather providers, model ensembles, elaborate styling, deployment and chat before cutting replay, export, provenance or README.

## Reproducibility and final checks

Proposed layout: `src/data.py`, `weather.py`, `features.py`, `model.py`, `replay.py`, `agent.py`; `app.py`; `config.yaml`; dependency lock; `artifacts/`; `outputs/`; and a small attributed cached demonstration dataset. Do not commit secrets. Suggested CLI contract: `prepare`, `train`, `evaluate`, `replay`, followed by `streamlit run app.py`.

README must explain the target/units, timezone assumptions, historical availability rule, training cutoffs, actual measured scores, setup commands, online/offline modes, update demo, and known limitations. Include provenance and required weather attribution. Preserve configuration, model metadata and input hashes with each result.

Focused checks: reject future weather availability and future measured features; reject incomplete target horizons or duplicate output keys; verify power bounds and units; ensure no fabricated February actuals; prove an unchanged input does not rerun; prove an eligible updated input creates a new version; run one cached scenario from a fresh environment.

Three-minute pitch: (1) why day-ahead wind uncertainty matters, (2) replay one historical origin with visible weather provenance, (3) show normalized hourly predictions and January baseline comparison, (4) advance time and demonstrate autonomous revision, (5) show reproducible exports and describe capacity-aware scheduling and uncertainty estimation as next steps.

## Questions that affect implementation

- OpenAI credits are confirmed: $50 activated, with an additional $50 available after the teammate activates their promotion. API key configuration remains a local setup step; never commit keys. Team size/skills are confirmed above.
- CSV timezone and timestamp interval convention.
- Required daily issuance time, overlap/scoring policy and submission format.
- Rated capacities, hub heights and normalization definition.
- Whether actual February telemetry will be provided during replay.
- Whether the proposed weather archive's historical provenance satisfies the organizers' interpretation.

Until answered: use a configurable daily schedule, preserve per-turbine normalized units, omit fresh-telemetry-dependent features, show assumptions explicitly, and prioritize a verifiable 48-hour replay.
