# Finalization and submission checklist

Use this after PRs #7 (dashboard designs) and #8 (backend portability) are merged. The next priority is a dependable demonstration and complete submission. Reserve the final **45–60 minutes** for rehearsal, submission checks and fixes uncovered by those checks. Freeze new features and model tuning during that window.

The combined PR snapshot merged without conflicts and passed **26 tests and 13 subtests** in an isolated macOS checkout. That result included the dashboard design tests and simulated Windows encoding checks. Native Windows verification and browser save-to-disk were the remaining dashboard checks at that point; the update below records their current status.

**Dashboard update, 23 September:** native Windows verification now passes without `-X utf8` (26 tests, 13 subtests). Horizon is selected and real-data screenshots are captured. Browser save-to-disk remains pending the saved path from the user. See [tested revision, environment and evidence](DASHBOARD_FINALIZATION.md). PR #7 was still open during this check; verification used it integrated with the latest main in a feature branch.

## Ownership and completion evidence

| Owner | Next task | Done when |
|---|---|---|
| Dashboard teammate | Native Windows setup and offline demo | Tests pass without `-X utf8`; real artifacts load and repeat execution deduplicates |
| Dashboard teammate | Browser CSV download | Saved file opens and contains 96 rows, both turbines and original columns |
| Dashboard teammate | Presentation design and screenshots | One design chosen; readable real-data screenshots retain warnings and units |
| Backend owner | Live agent, revision and deduplication | Two genuine executions publish versions; a repeat skips unchanged inputs |
| Backend owner | Submission package and local backup | February CSV, instructions, evidence and offline example are available; working cache/models backed up |
| Backend owner | Organizer clarification | Answers recorded, or unresolved assumptions remain disclosed |
| Both | Three-minute rehearsal | Demo runs within time and both teammates can explain results and limitations |

Work from the integrated main revision in your own checkout. Keep any fixes on feature branches and integrate through PRs. Record the tested commit, environment, results and remaining failures; screenshots alone do not establish backend execution.

## 1. Dashboard teammate: Windows and browser checks

From the repository root, with the project virtual environment active, run these commands **without `-X utf8`**:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m src demo --output outputs/final-rehearsal
python -m src demo --output outputs/final-rehearsal
```

Use an unused output-directory name for the first run. The first invocation should publish two versions, totaling 192 rows. The second should report unchanged-input deduplication and leave those predictions unchanged. No API key or weather network access is required for `demo`.

If an older run left misencoded preparation metadata, run `python -m src prepare`, then repeat the demo in a fresh output directory. Keep checksum validation enabled. See [the Windows recovery instructions](../README.md#windows-powershell-enable-utf-8).

Start the dashboard in PowerShell:

```powershell
$env:CINDRELO_OUTPUT_DIR = "outputs/final-rehearsal"
python -m streamlit run app.py --browser.gatherUsageStats false
```

- [x] The page identifies published model output and displays degraded status and source warnings.
- [x] Turbine and issuance selections, actuals, revisions, metrics and trace views work.
- [x] Switching designs preserves turbine and issuance selections.
- [ ] Download a forecast using the browser, locate the saved file, and open it. Confirm 96 data rows, 48 per turbine, and the original forecast columns. The chart's turbine selector must not reduce export scope.
- [x] Choose one design for the presentation; keep it selected during rehearsal.
- [x] Capture clear screenshots with normalized-power units and limitations visible.
- [x] Record the native Windows test result and actual browser-save result in the demo notes. (Save attempt is recorded as **unconfirmed**, not passed.)

## 2. Backend owner: live execution and revision

Use the backend machine with its existing trained models, cached weather and ignored `.env` containing `OPENAI_API_KEY`. The portable `demo` command alone does not create the full model/cache artifacts needed by `forecast`. On a fresh machine, the setup path is `python -m src train`; weather retrieval also requires network access when caches are absent.

Use a fresh output directory for this rehearsal. These commands use cached weather but make real OpenAI API calls:

```sh
python -m src forecast --issue 2026-01-09T19:00:00Z --agent --offline --output outputs/final-agent
python -m src forecast --issue 2026-01-10T07:00:00Z --agent --offline --output outputs/final-agent
python -m src forecast --issue 2026-01-10T07:00:00Z --agent --offline --output outputs/final-agent
```

- [ ] First call publishes 96 rows for both turbines.
- [ ] Second call advances the simulated clock by 12 hours and publishes a new version using a newer eligible weather run.
- [ ] Revisions compare 36 overlapping target hours per turbine. Revision measures changed predictions, not prediction error.
- [ ] Third call reports unchanged-input deduplication; forecasts remain at 192 rows and a skipped event is recorded.
- [ ] Dashboard pointed at `outputs/final-agent` loads the new versions and trace. Refresh only reloads published files.
- [ ] Show the CLI execution alongside the dashboard when claiming a live agent run. Saved traces and deterministic offline regeneration are labelled accordingly.

On macOS/Linux, launch the matching dashboard with:

```sh
CINDRELO_OUTPUT_DIR=outputs/final-agent python -m streamlit run app.py --browser.gatherUsageStats false
```

The backend follow-up already verified a fresh live call and a full February replay. This step rehearses the complete presentation sequence. Existing recovery evidence is in [the explicitly simulated-failure trace](../examples/backend/recovery-events.jsonl); a new failure-injection demo is optional.

## 3. Backend owner: submission and backup

- [ ] Confirm the organizer's submission destination, deadline and required format.
- [ ] Include the [February day-ahead CSV](../examples/submission/february_day_ahead.csv): **1,344 unique rows, 672 February hours per turbine**, using horizons 25–48 from the preceding local midnight.
- [ ] Include or link [verification and provenance](../examples/submission/README.md), measured January metrics, setup commands and known limitations.
- [ ] Ensure the repository includes the portable offline example, source code and pinned requirements. Another machine must be able to run `python -m src demo` without a key or prior training.
- [ ] Keep a separate local backup of the working `data/weather/`, `artifacts/` and generated `outputs/` directories for the demonstration. These ignored directories are not included by a Git push.
- [ ] Exclude `.env`, API keys and the virtual environment from any package or backup shared with judges.
- [ ] Verify links, filenames and the final submission receipt before the deadline.

The day-ahead CSV is a submission table, not a complete dashboard directory: it intentionally contains only 24 targets per issuance. A full February dashboard needs the five artifact files generated by replay. With the existing complete cache and models:

```sh
python -m src replay --offline --output outputs/final-february
```

Expected result: 29 versions, 2,784 full forecast rows, plus the 1,344-row day-ahead table. A new clone must first acquire the required weather and train models; `--offline` cannot supply missing artifacts. See [backend verification](BACKEND_VERIFICATION.md).

## 4. Backend owner: resolve consequential assumptions

Ask organizers these two questions and record their answers:

1. **Raw timestamps:** which timezone do the turbine CSVs use, and do timestamps denote the beginning or end of the measurement interval?
2. **Weather provenance:** is the selected Open-Meteo ECMWF IFS Single Runs archive acceptable for their historical-availability requirement, given its unresolved operational-run/hindcast provenance and our assumed eight-hour publication delay?

Current assumptions are `Asia/Almaty`, interval starts and an eight-hour weather publication delay. The wind-height mapping and turbine capacities are also unconfirmed. If confirmed information changes configuration or time alignment, regenerate affected data/models/forecasts and verify results before using them. Recompute reported scores if their assumptions change.

Until resolved, retain the warnings and degraded status. Report normalized power, avoid capacity-based energy claims, and make no February accuracy claim: the supplied data contain no February observations.

## 5. Both: three-minute rehearsal

| Time | Show and explain |
|---|---|
| 0:00–0:30 | The day-ahead wind forecasting problem, two turbines and normalized-power units |
| 0:30–1:30 | Real agent execution, weather/model metadata, then the revised forecast on overlapping target hours |
| 1:30–2:00 | January holdout comparison: pooled curve MAE **0.1669**, persistence **0.3340**; December selected the curve before January evaluation |
| 2:00–2:30 | The complete February export, browser download and reproducible offline example |
| 2:30–3:00 | Unresolved assumptions, absence of February actuals and practical next steps |

Individual turbine/horizon metrics differ from the pooled January score. The empirical curve outperformed the tested CatBoost candidate; describe the measured result directly. Scores remain conditional on the documented timezone and weather-archive assumptions.

Have the offline demo and saved live traces ready if API access fails during the presentation. Clearly state when showing saved execution evidence. After rehearsal, prioritize failures that affect execution, correctness, reproducibility or submission. Freeze visual changes and further model experiments.
