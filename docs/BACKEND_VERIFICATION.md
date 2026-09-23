# Backend follow-up to PR #6

Reviewed [PR #6](https://github.com/BAITC-Hacks/hack-e4fa63ee-cindrelo/pull/6), merged at `2c320ed`. Its remaining backend items were locale-dependent text I/O, an unrepeated full February replay, and an unrepeated live OpenAI invocation. Browser file-saving is a separate manual UI check. No UI files or original example fixtures were changed in this follow-up.

## Encoding fix

All backend file reads now explicitly accept UTF-8, including an optional BOM; JSON, CSV and event writes explicitly use UTF-8. This covers configuration, raw observations, prepared metadata, model JSON, weather envelopes, published CSVs, event logs and `.env`. The weather checksum algorithm and existing hashes are unchanged. Corrupted cached weather still fails validation.

`tests/test_portability.py` simulates the Windows `cp1252` default for implicit Python text I/O, while preserving explicitly requested encodings. From isolated source files with no prepared data or model binaries, it runs the offline demo twice, checks exact equality with all 192 committed predictions and matching actuals/metrics, and verifies deduplication. It also covers BOM-prefixed configuration/model/environment files, Cyrillic metadata, Unicode JSON/CSV/events and rejection of a modified weather value with its original checksum.

This is a regression simulation on macOS/Python 3.14.7, not a claim that the fixed code was executed on native Windows. Previously misencoded generated metadata/events require the documented `prepare` plus fresh-output-directory recovery. The code does not guess legacy encodings or rewrite committed cache files.

## Repeated integration checks

Executed after the fix on 23 September 2026:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m src replay --offline --output outputs/pr6-backend/february
python -m src forecast --issue 2026-01-09T19:00:00Z --agent --offline --output outputs/pr6-backend/agent
```

The replay used the previously trained local model bundles and full weather cache. To reproduce on a fresh clone, run `python -m src train` and `python -m src replay` first; `--offline` does not supply missing training/weather artifacts. The agent command used the configured local OpenAI key and real API access; only its weather acquisition was offline. Use a new output directory to demonstrate execution again instead of deduplication.

- Combined backend/UI suite: **25 tests, 10 subtests passed**.
- Full replay: **29 versions, 2,784 rows**, all accepted by the merged dashboard loader. Each issuance's download payload contains 96 rows and both turbines.
- February day-ahead export: **1,344 unique rows**, exactly 672 hourly targets per turbine, with no gaps. Issuance is always the preceding local midnight, horizons 25–48. Predictions exactly match the original backend replay.
- No February actuals were invented. Available January observations remain confined to their real coverage.
- A fresh live OpenAI tool loop completed and published **96 rows**, numerically identical to the matching committed January example. The saved trace includes actual execution times.

The portable [February export, verification record and fresh live-agent trace](../examples/submission/README.md) are committed; full generated dashboard files remain under the ignored output directories. `verification.json` records the CSV checksum, evaluation environment, coverage checks and outstanding provenance assumptions.

## Still unresolved

- Native Windows verification of this encoding fix is available for the teammate to repeat using the updated PowerShell commands.
- Browser save-to-disk behavior remains a manual check; backend CSV payload generation and dashboard parsing passed.
- Raw timezone, interval convention, wind-height mapping and historical weather publication provenance remain assumptions. They need organizer/source confirmation, not a code change. Outputs continue to report `degraded`; there are no February accuracy claims.
