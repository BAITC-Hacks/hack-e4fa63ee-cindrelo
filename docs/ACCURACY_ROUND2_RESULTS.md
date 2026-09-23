# Second accuracy batch: better blends, no universal winner

Executed 23 September 2026. Tested **22 recipes including controls** on October–December, reported January as a reused diagnostic, then evaluated a frozen six-method shortlist on previously unused July–September folds. No production model or February predictions were changed.

## Outcome

The simplest useful challenger is a **50/50 blend of the existing measured-wind mean curve and the direct forecast-wind median curve**. Across seven scored months its day-ahead MAE is **0.180219 versus 0.186366**, a **3.30% reduction**. It wins six of seven months and improves January. However, July regresses and July–September pooled MAE is slightly worse; keep it in shadow evaluation rather than claiming an established universal improvement.

The previous direct median challenger still has the lowest seven-month pooled MAE, **0.179250**, but is worse in January. There is no method that is best for every period. Choosing the smallest January number would overfit a month already used repeatedly for diagnostics.

| Recipe | July–September | October–December | January | All seven months |
|---|---:|---:|---:|---:|
| Existing curve | **0.177526** | 0.198236 | 0.176779 | 0.186366 |
| Previous direct median | 0.181467 | 0.176859 | 0.179969 | **0.179250** |
| 50/50 existing + direct median | 0.177938 | 0.185062 | 0.172617 | 0.180219 |
| 50/50 existing + smooth median | 0.178017 | 0.185011 | 0.172566 | 0.180222 |
| Smooth 60th percentile | 0.189386 | **0.175507** | 0.199365 | 0.184883 |
| Smooth 40th percentile | 0.193798 | 0.193971 | **0.169787** | 0.190271 |

All numbers are normalized-power MAE for hours 25–48 on identical scored pairs within each period. Seven-month scores are sample-weighted over 9,920 pairs, not unweighted averages of monthly scores. They are exploratory summaries spanning development, earlier-season checks and a reused January diagnostic, not an independent test score. Earlier folds use June-onward weather training; the October–January experiment retains September-onward weather training to preserve comparability with round one. This is therefore not a single expanding June–January production replay.

## What changed

- Fixed 25/50/75% blends of the original mean or measured-wind median curve with the previous direct median challenger.
- Gaussian-kernel conditional medians of power versus issued 100 m wind, with 0.5/1/2 m/s bandwidths; evaluated on a 0.1 m/s grid with interpolation.
- Fixed 40th/60th-percentile kernel variants, 14/30-day training recency weights, and day-ahead-only calibration.
- Seasonal measured-wind median curves using the target month and adjacent months from observations available before the training cutoff, plus a fixed seasonal/direct blend.

Everything is fitted before the earliest test issuance. No future observed power/wind is used. January was not allowed to change the frozen development winner: `kernel_q0.6` remains the recorded selection, and it fails January badly. The blend recommendation was made after inspecting the diagnostic results and is explicitly exploratory. The earlier-season shortlist was frozen before its July–September scores were inspected.

The smooth and unsmoothed 50/50 blends are nearly tied. Prefer the unsmoothed blend for a follow-up integration experiment because it uses existing curve machinery and avoids another bandwidth parameter; its 0.000003 advantage in pooled MAE is not meaningful evidence of superiority.

## Seasonal stability

| Month | Existing curve | 50/50 direct blend |
|---|---:|---:|
| July | **0.164119** | 0.167011 |
| August | 0.189169 | **0.188639** |
| September | 0.181257 | **0.179941** |
| October | 0.173249 | **0.164655** |
| November | 0.192623 | **0.175675** |
| December | 0.227012 | **0.213255** |
| January | 0.176779 | **0.172617** |

The smooth blend's January gain is 0.004213 MAE, but its 3-day paired-block 95% interval is [-0.00551, 0.01258]. It includes no gain. Positive development intervals do not resolve this uncertainty, especially after trying multiple recipes. Bootstrap intervals are not corrected for model selection. Full interval summaries are stored with the evidence.

## Coverage and limitations

July/August/September contribute 1,472 / 1,234 / 1,438 day-ahead pairs. The provider reports unavailable runs at August 5, 6, 8 and 9 UTC initializations; the August 7 run fails complete-horizon validation. The corresponding local forecast issuances are August 6–10, whose day-ahead targets are August 7–11. All five affected origins are explicitly excluded for every method; incomplete actual observations cause additional exclusions. Their error is unknown and no replacement weather was invented. `earlier-season/unavailable_weather.json` contains exact issuance timestamps and provider messages.

An initial attempt was stopped during weather collection to correct local-month boundary arithmetic before any candidate scores were produced. Two archive collection attempts subsequently stopped on provider gaps; the final extension explicitly records those gaps. Completed experiment outputs are not overwritten. Quantile input validation and extension support were added after the original 22-recipe run without changing its numerical recipes.

Timezone, wind-height and historical publication assumptions remain unresolved. January is reused, February actuals are absent, and earlier-season retrospective testing is not prospective operational validation. Keep degraded-source disclosures. No seasonal switch is fitted using these scored months.

## Reproduction and next decision

```powershell
python -m src.accuracy_round2 --output outputs/round2-repeat
python -m src.accuracy_round2 --extension --fetch-weather --output outputs/round2-extension-repeat
python -m pytest -q
```

The primary batch requires the September–January cache acquired in round one. Extension mode acquires missing June–September runs, records unavailable/incomplete origins and scores all shortlisted methods on the same surviving targets. New output directories are required. Predictions/cache remain local under ignored `outputs/` and `data/weather/`; compact evidence, recipes and hashes are under [examples/accuracy-round2](../examples/accuracy-round2/).

Validation: **32 tests and 10 subtests passed** on Windows. New checks cover weighted-quantile validity, turbine/order preservation and complete local-month evaluation bounds.

Recommendation: retain the existing curve as production default; take the 50/50 direct blend into a separate shadow-output integration, with the original as fallback. Decide on promotion using new observations or a separately frozen evaluation period rather than another January tuning pass. The seven-month direct median ranking can inform research, but its known January failure remains part of the comparison.
