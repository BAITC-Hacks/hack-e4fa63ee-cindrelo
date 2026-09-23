# Backend rehearsal and repository submission

The backend sequence was rehearsed on integrated main `03f90bb7ef41f3a441c061edc24b00079b56cc10`, on macOS with Python 3.14.7. PR #8 and the finalization checklist were merged; PR #7's dashboard designs were still open. No forecasting or UI code changes were needed.

The organizer-created GitHub repository is the submission destination. The user confirmed the deadline is **23 September 2026**; an exact hour and additional submission format were not provided. Evidence added on this branch still needs to reach main through its PR.

## Completed

- Three real OpenAI controller invocations used cached historical weather. The first two published forecasts; the third deduplicated the repeated issuance.
- Two forecast versions contain **192 rows**. The repeated call left `forecasts.csv` byte-for-byte unchanged and added one skipped-prediction event.
- Revisions align **36 overlapping hours per turbine**. Mean absolute revision: Turbine 1 **0.0522214**, Turbine 2 **0.0515168**. These are revisions, not errors.
- Both versions reproduce the existing January example predictions exactly. Each CSV download payload contains 96 rows and both turbines.
- Streamlit AppTest loaded the real artifacts, switched turbines and issuances, verified both displayed revision values, and refreshed successfully. The February view correctly displayed unavailable actuals. This does not replace the teammate's browser save-to-disk check.
- Full February replay produced **29 versions / 2,784 forecast rows** and the **1,344-row** day-ahead export, which exactly matches the committed submission CSV.
- Integrated-main suite: **25 tests and 10 subtests passed**. The earlier 26-test/13-subtest result in the checklist included a simulated merge of PR #7; the different counts are expected.

Evidence: [rehearsal verification](../examples/submission/rehearsal-verification.json) and [all three live execution traces](../examples/submission/rehearsal-events.jsonl). Trace `timestamp` values are simulated issuance times; `executed_at` records real execution time. The API key was loaded locally and is excluded from published artifacts.

## Open the rehearsed views

| Local directory | Contents |
|---|---|
| `outputs/final-agent/` | Two live-generated January forecasts, actuals, January metrics and completed/repeated tool traces |
| `outputs/final-february/` | Full February replay, lineage, metrics and day-ahead export |

```sh
CINDRELO_OUTPUT_DIR=outputs/final-agent python -m streamlit run app.py --browser.gatherUsageStats false
# Full February view:
CINDRELO_OUTPUT_DIR=outputs/final-february python -m streamlit run app.py --browser.gatherUsageStats false
```

For a new live demonstration, follow [the finalization checklist](FINALIZATION_CHECKLIST.md) with a fresh output directory. Reusing the rehearsed directory intentionally demonstrates deduplication. `--offline` disables weather downloads; `--agent` still uses OpenAI network access.

## Local backup

The local rehearsal archive is stored at `outputs/packages/cindrelo-rehearsal.zip`, with its SHA-256 sidecar alongside it. It contains a source snapshot, the full local weather cache, model artifacts, and both generated dashboard directories. `PACKAGE_MANIFEST.json` records the packaged source revision and per-file checksums; `PACKAGE_README.md` gives extraction and reproduction commands.

Restoration verified all **451 payload-file checksums**. With socket connections disabled and no API key, the extracted source reproduced both the January demo and the complete February export exactly. This check reused installed dependencies; it did not test dependency installation without internet access. The archive source revision is `12c2540`; later changes on this branch only record the restoration result.

The archive excludes `.env`, `.git`, the virtual environment and unrelated output folders. It is an ignored local backup, not the GitHub submission. Dependency installation is still required on a new machine; offline reproduction refers to forecasting after setup. Only load packaged model pickles from this project's trusted backup.

The repository already includes the portable January example, measured results, complete February day-ahead CSV and reproduction instructions. The model/cache archive preserves the working local state if network access becomes unavailable during presentation. Refresh that backup after the final UI merge if it will be used to present the final design.

## Remaining handoff

1. Teammate completes native Windows and browser-save checks, selects a design and records results.
2. Merge the final dashboard PR and backend evidence PR into main, then verify that exact integrated revision.
3. Rehearse the three-minute presentation together and confirm repository access for organizers before their deadline.
4. Keep timezone, interval convention, wind-height and weather-archive provenance assumptions visible. No new organizer confirmation was received; degraded status and the absence of February accuracy claims remain appropriate.
