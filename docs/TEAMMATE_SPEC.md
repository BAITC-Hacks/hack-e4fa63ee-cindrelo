# Dashboard handoff — Cindrelo

## Mission and ownership

Build a simple Streamlit dashboard that makes the forecasting pipeline understandable to judges. Start with the supplied synthetic fixture; the same interface must load real pipeline artifacts later. No ML, weather API, LLM integration, database, authentication, or deployment work is required from you.

You own `app.py`, `ui/`, `assets/`, `docs/DEMO.md`, and `requirements-ui.txt`. Use Streamlit, pandas and Plotly. Put UI dependencies in `requirements-ui.txt`; the backend owner will integrate installation instructions and pin dependencies before submission.

The backend owner owns `src/`, training, weather acquisition, the agent, real `outputs/`, dependency integration and the root README. Treat `docs/fixtures/dashboard/` and the contracts below as shared read-only inputs. Propose contract changes in your PR before implementing them. This prevents conflicting edits.

OpenAI credits are available for the backend agent ($50 activated; the teammate's additional credits are not yet activated). The dashboard must require neither an API key nor a network connection. Never put keys in source, fixture files, logs, screenshots or PRs.

## Start here

After this specification is merged into `origin/main`:

```sh
git fetch origin
git switch -c feat/dashboard origin/main
python3 -m venv .venv
source .venv/bin/activate
python -m pip install streamlit pandas plotly
```

Create `app.py`, then run `streamlit run app.py`. Default to the fixture directory. Support `CINDRELO_OUTPUT_DIR` as the directory override for integration:

```sh
CINDRELO_OUTPUT_DIR=outputs/dashboard streamlit run app.py
```

Use paths relative to the repository/app location, not the caller's current working directory. Show the loaded directory and a Refresh button. Refresh only reloads files; it does not request weather or retrain models. If files are absent or malformed, display an actionable error with the filename, without a traceback or silent fallback to synthetic data.

## First PR: forecast screen

Deliver this within roughly 60–90 minutes:

1. Title: “Cindrelo — Wind power forecast”. Brief caption: “Hourly normalized power for two wind turbines”.
2. A persistent, prominent “SYNTHETIC DEMO DATA — not model results” banner when `manifest.json` has `data_kind: synthetic`. The fixture contains January example dates, not actual January results.
3. Turbine selector (Turbine 1 / Turbine 2) and forecast issuance selector, defaulting to the latest issuance. Display UTC explicitly.
4. Line chart of hourly normalized power versus target time for the selected issuance and turbine. Axis range 0–1. Use “Normalized power” as the unit; do not label values MW or MWh.
5. Weather run time, forecast issue time, model version and forecast status in a compact details panel.
6. Download the selected forecast's original CSV columns for both turbines, with a filename containing the forecast ID. Caption explains that export scope.

Keep the layout readable on a laptop. Use two or three consistent colors, clear legends, useful hover labels, and sufficient contrast. Skip maps, custom illustrations, login screens, chat and elaborate animations.

## Second PR: evidence and revision views

After the first screen works:

- Overlay the previous issuance for the selected turbine on overlapping target timestamps only; use a dashed line. Align by `valid_time`, never row position or horizon number. Show mean absolute revision on those overlapping hours, labelled “Forecast revision”, not “Forecast error”. Display “No previous overlapping forecast” when needed.
- Overlay actual power where a matching `(turbine_id, valid_time)` exists in `actuals.csv`. Missing observations remain gaps; never turn missing values into zero. Real February actuals are unavailable, so show “Actuals unavailable” for that period.
- Show `metrics.csv` as a compact model/baseline comparison, filtered by turbine. Label its evaluation window and horizon bucket. Do not calculate a new score on the currently displayed forecast or describe fixture metrics as measured performance.
- Show `events.jsonl` as a chronological, expandable tool trace filtered by forecast ID: tool, state, timestamp, message. Do not render messages as executable HTML.
- Surface manifest warnings, degraded status and errors visibly. Empty metrics or event files should produce helpful empty states, not crash the app.
- Add `docs/DEMO.md`: startup instructions, a 2–3 minute walkthrough and screenshots. Screenshots of fixture data must retain the synthetic banner.

Actual generation/rerun controls are a later integration task. Until the backend callable exists, browsing two saved forecasts is a revision viewer, not evidence that an agent just ran. Do not add a fake “Run agent” button or fake live progress.

## Data contract v1

All files live in one configured directory. CSVs use UTF-8, comma delimiters and a header. JSON uses standard JSON, not NaN. Unknown extra fields may be ignored; missing required fields should be explained to the user. Backend publishes a complete directory before asking the user to refresh; atomic snapshot handling can be added during integration.

All timestamps are ISO 8601 UTC with a `Z` suffix. These are application/output timestamps; converting the raw CSV timezone is the backend's responsibility. `valid_time` labels the **start** of an hourly target interval. `horizon_hours = (valid_time - issued_at) / 1 hour + 1`; issuance is on the hour, so horizon 1 is the first interval beginning at issuance. This convention resolves the ambiguous initial proposal and must be used consistently by both owners.

### manifest.json

Required fields:

| Field | Type / meaning |
|---|---|
| `schema_version` | Integer, currently `1` |
| `data_kind` | `synthetic` or `model_output` |
| `timezone` | `UTC` |
| `target_unit` | `normalized_power` |
| `description` | Human-readable dataset description |
| `warnings` | Array of strings; may be empty |

### forecasts.csv

One row per `(forecast_id, turbine_id, valid_time)`. A full run has 48 hours × 2 turbines = 96 rows. An issue may have multiple versions: select by forecast ID and display issuance time alongside it.

| Column | Type / meaning |
|---|---|
| `forecast_id` | Stable string identifying one published version |
| `issued_at` | Simulated forecast issuance time |
| `valid_time` | Target interval start |
| `turbine_id` | `turbine_1` or `turbine_2` |
| `horizon_hours` | Integer 1–48 |
| `power_normalized` | Number in [0,1] |
| `weather_run_time` | Weather model initialization time |
| `weather_available_at` | Verified or explicitly assumed historical availability time |
| `model_version` | Model/version identifier |
| `input_hash` | Input identity for reproducibility |
| `status` | `ok` or `degraded` |

Sort records for plotting. Do not modify values or infer physical capacities. Failed runs with no valid forecast belong in the events file, not fabricated numeric rows. Actual versus assumed weather availability must be disclosed in manifest warnings or run events.

### actuals.csv

Columns: `valid_time,turbine_id,power_normalized`. The key `(turbine_id, valid_time)` is unique. Header-only is valid. These are quality-filtered hourly observations supplied by the backend; the UI must not resample raw turbine files.

### metrics.csv

Columns: `model,turbine_id,horizon_bucket,mae,rmse,n_samples,period_start,period_end`.

`horizon_bucket` is `1-24` or `25-48`. Errors are in normalized-power units; `n_samples` counts scored forecast/target pairs, and the period is start-inclusive/end-exclusive. Header-only is valid. Compare models within the same turbine, window and horizon. Lower MAE/RMSE is better. Fixture values are invented solely for layout.

### events.jsonl

One JSON object per line with `forecast_id`, `timestamp`, `tool`, `status`, `message`. `status` is `started`, `completed`, `warning`, `failed` or `skipped`. Empty file is valid. Render plain messages in time order; timestamp denotes execution time in the displayed replay scenario. Detailed backend logging may include additional fields.

## Acceptance checklist

- Fresh setup starts with documented dependencies and fixture data, without keys/network calls during app use.
- Changing turbine/issuance updates the chart, metadata and download correctly.
- The fixture has two issuances and 192 forecast rows; each selected issuance exports 96 rows.
- The two fixture issuances share 24 target hours per turbine; comparison uses exactly that overlap.
- Actuals are joined by turbine and timestamp. Missing actuals remain missing.
- Every synthetic view is clearly marked, including screenshots and score tables.
- A header-only actuals/metrics file and empty events file render gracefully.
- Degraded forecasts, warnings and invalid input paths are understandable.
- No UI action claims to execute an unimplemented backend tool.
- Swapping the configured directory to real contract-compatible files requires no code changes.

Manual checks and one screenshot are sufficient for the first PR. Do not spend the deadline adding tests that only duplicate display code.

## Branch and PR workflow

- `origin/main` is the integration branch. Both owners work on feature branches, never directly on main.
- Open your first draft PR early: `feat/dashboard` → `main`. Include what works, the startup command, a screenshot with the synthetic banner and remaining items.
- Commit only your owned files. Do not commit `.venv`, `.env`, model binaries, downloaded weather or generated real outputs.
- Push with `git push -u origin feat/dashboard`, then open the PR against `main` on GitHub.
- After the first PR is merged, create `feat/dashboard-evidence` from updated `origin/main` for the second PR. Keep dependencies between PRs explicit if the first is still open.
- Backend owner will use `feat/forecast-pipeline`; ownership of shared files/contracts is coordinated before editing.
- Before integration, fetch `origin` and incorporate the latest `origin/main`; avoid rewriting a shared branch or force-pushing main. The backend owner reviews/merges UI PRs and handles final integration.

## Cut order if time runs short

Keep the forecast chart, correct units, synthetic disclosure, provenance details and CSV download. Then add revisions and score table. Cut decoration and extra charts first. Reserve the last hour for a clean-start run, screenshots and rehearsal.
