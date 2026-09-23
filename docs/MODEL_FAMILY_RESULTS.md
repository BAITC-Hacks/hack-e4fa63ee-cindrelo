# Other model families: histogram boosting and a promising blend

Executed 23 September 2026. Tested **16 configurations across seven model families**, plus the existing curve. A weather-only histogram gradient booster markedly improves January 31, but worsens the monthly score. An exploratory 50/50 blend improves both. No production model or official forecast was replaced.

## Main comparison

All figures are normalized-power MAE for horizons 25–48 on identical scored rows within each period.

| Method | December | January | January 31 |
|---|---:|---:|---:|
| Existing empirical curve | 0.228440 | 0.176235 | 0.308140 |
| December-selected histogram MAE booster, live inputs | **0.206940** | 0.190983 | 0.335300 |
| Histogram MAE booster, weather only | 0.210987 | 0.184748 | **0.214474** |
| 50/50 original curve + weather-only histogram MAE booster | 0.210795 | **0.172369** | 0.258783 |

The weather-only booster cuts January 31 error by **30.40%**, but increases January error by **4.83%**. Its fixed blend cuts January 31 error by **16.02%**, January error by **2.19%**, and December error by **7.72%**. This is a useful new challenger, not proof of an independently validated improvement.

The selected model was recorded using December before any January family results were computed. That winner fails January. All other January results are explicitly diagnostic. The blend batch was added after inspecting the single-model January results; it is post-hoc research. Even within that blend batch, the December winner was the neural/live blend (December 0.208474, January 0.177892), not the highlighted histogram/weather blend. Do not describe the highlighted blend as the December-selected winner.

## What was tested

Each configuration runs once with issued-weather inputs and once with authorized, delayed live observations:

- Random forest: 150 trees, depth 14, minimum leaf 20.
- Extra trees: 150 trees, depth 18, minimum leaf 10.
- Histogram gradient boosting: absolute-error and squared-error losses, 250 iterations, 15 leaves, minimum leaf 40, L2 regularization 10, learning rate 0.05.
- K-nearest neighbours: 80 distance-weighted neighbours.
- Additive cubic splines plus ridge regression: five knots and ridge penalty 100.
- Approximate RBF kernel regression: 200 Nystroem components and ridge penalty 10.
- Feed-forward neural network: hidden layers 64/32, L2 penalty 1, maximum 150 iterations, batch 256.

Fixed parameters are in `src/model_families.py`. No random validation split or January-based early stopping is used. Imputation/scaling is fitted only on training rows. Training is June–November for December selection, then June–December for January. Older weather gaps remain as previously documented. The existing curve retains its longer observed history.

Weather features include wind speed, direction, shear, lead and same-issuance forecast trajectory. Live inputs use the user-authorized two-hour delay after each observation interval ends; stale observations trigger the original-curve fallback. January target observations never fit the models; only already-available earlier January telemetry can be an input.

Both December and January follow the original issuance-window evaluation convention. January day-ahead scores cover January 2–31: **1,440 pairs**, 720 per turbine. January 31 covers 48 pairs. Do not compare these directly with earlier boundary-corrected experiments using 1,488 January pairs and an earlier model cutoff.

## Uncertainty and recommendation

The highlighted blend's January absolute MAE improvement is 0.003866. Its paired 3-day block-bootstrap 95% interval is **[-0.01230, 0.01881]**, which includes no improvement. One- and seven-day intervals also include zero. These intervals are exploratory and not adjusted for trying many recipes.

Keep the original as the official default. The **50/50 curve + histogram MAE weather model** is the strongest new candidate here for shadow evaluation of the difficult day without sacrificing the observed monthly score. It needs a newly frozen evaluation period or new observations before a reliable deployment claim. No further search on January can make January untouched again.

All 16 standalone configurations and 16 fixed blends are included in the machine-readable reports; the table above is not a claim that every alternative failed on every day. None of the standalone alternatives beat the original full-January MAE. The weakest and strongest days must remain visible when discussing improvements.

## Reproduce

```powershell
python -m pip install -r requirements-research.txt
python -m src.model_families --output outputs/model-families-repeat
python -m src.model_families --blends-from outputs/model-families-repeat --output outputs/model-families-blends-repeat
python -m pytest -q
```

Requires the previously acquired research weather exports and raw/prepared observations. Outputs are written to new research directories; completed directories cannot be overwritten. Compact scores, protocols, selection records, warnings and hashes are in [examples/model-families](../examples/model-families/). Full row predictions remain local and ignored. The final code also contains the later blend-analysis entry point, so its current hash differs from the original preregistered standalone-run hash.

The scikit-learn dependency is optional for the main dashboard and pinned in `requirements-research.txt`; its tests skip when it is absent. Package installation passed `pip check`. There were no recorded fitting warnings in the initial December run; per-run warning files retain the complete record.

Reference: [scikit-learn histogram gradient boosting](https://sklearn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html) documents the supported losses and early-stopping controls. This experiment explicitly disables automatic early stopping to avoid a random time-series validation split.
