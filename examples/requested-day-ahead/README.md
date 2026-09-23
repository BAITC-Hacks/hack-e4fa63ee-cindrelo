# January 31 and February 1 day-ahead forecasts

Dates below are **target dates**, in the configured (still assumed) `Asia/Almaty` timezone, UTC+05:00 for these dates. Each target day contains 24 hourly intervals per turbine. Horizons 25–48 are interval starts 24 through 47 hours after issuance, covering the 24–48-hour-ahead interval.

| Target day | Issue, local midnight | UTC issuance | Rows, both turbines |
|---|---|---|---:|
| 31 January 2026 | 30 January 2026 | 2026-01-29 19:00Z | 48 |
| 1 February 2026 | 31 January 2026 | 2026-01-30 19:00Z | 48 |

## Data roles respected

- Earlier history, including October: training. The original CatBoost recipe uses archived weather/production pairs beginning September 2025; the empirical curve uses the longer pre-cutoff observed turbine history.
- December 2025: candidate selection. The empirical curve won (MAE 0.223621 versus CatBoost 0.239514).
- January 2026: historical test using the model trained through December, not a training source for these outputs.
- February 2026: forecast period with no supplied actuals.

Both requested preceding-midnight issuances occur before January observations are complete. Therefore both use the frozen `validation-be2706022497:curve`, trained until 2026-01-01 00:00 local (2025-12-31 19:00Z), with the final included training target starting 2025-12-31 18:00Z. Training through January 31 and backdating either issuance would leak future data. A model refitted through the end of January becomes eligible only at February 1 midnight or later; its horizons 25–48 would target February 2, not February 1.

Earlier accuracy-research fold comparisons are exploratory and do not replace these official training/selection/test roles. No challenger or January-driven model selection is used here.

## Files and verification

- `day_ahead_2026-01-31.csv`: 48 forecast rows, 24 per turbine.
- `day_ahead_2026-02-01.csv`: 48 forecast rows, 24 per turbine.
- `requested_day_ahead.csv`: combined 96 rows, with original lineage columns and degraded status.
- `comparison_*.csv`: predictions joined to actuals where they exist; all February actual fields remain empty.
- `verification.json`: training cutoff, UTC issuances, row counts, per-turbine means/errors and export SHA-256.

January 31 MAE: turbine 1 **0.300511**, turbine 2 **0.315769**, pooled **0.308140**. This is a one-day historical result, not the full January score. February 1 has no actuals and no accuracy score. February 1's 48 predictions match the existing February submission within 1e-14 normalized power; regenerating the established model should not change those values.

Generation and repeat deduplication succeeded. The complete dashboard directory at local `outputs/requested-day-ahead/` contains two full 48-hour issuances (192 rows), valid actuals/metrics/manifest/events, and passes the dashboard loader. The committed day-ahead CSVs alone are not a complete dashboard directory.

```powershell
python -m src.requested_forecasts --offline
# On a fresh clone, omit --offline to obtain missing archived weather.
$env:CINDRELO_OUTPUT_DIR = "outputs/requested-day-ahead"
python -m streamlit run app.py
```

Generation uses deterministic guarded pipeline tools, not a new live OpenAI call. The historical as-issued weather provenance, raw timestamp convention and wind-height assumptions remain unresolved; outputs retain source warnings. Power is normalized, not MW or MWh.
