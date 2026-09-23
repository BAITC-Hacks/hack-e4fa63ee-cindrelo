import numpy as np
import pandas as pd

from .common import ROOT, METRIC_COLUMNS, iso, local_date, write_csv, write_json
from .data import load_hourly
from .model import fit, predict
from .weather import Weather


def collect(cfg, start, end, offline=False):
    provider = Weather(cfg, offline)
    origins = pd.date_range(start, end, inclusive="left", freq="D", tz=cfg["calendar_timezone"])
    frames = []
    for i, issue in enumerate(origins):
        frames.append(provider.horizon(issue))
        if i % 10 == 0 or i == len(origins) - 1:
            print(f"Weather {i+1}/{len(origins)}: {issue.date()}", flush=True)
    return pd.concat(frames, ignore_index=True)


def evaluate(bundle, weather, hourly, start, end):
    forecasts = weather.loc[(weather.issued_at >= start) & (weather.issued_at < end)].copy()
    truth = hourly.loc[hourly.complete & (hourly.valid_time >= start) & (hourly.valid_time + pd.Timedelta(hours=1) <= end)]
    results, scored = [], []
    for method in ["catboost", "curve", "mean", "persistence"]:
        part = forecasts.copy()
        if method == "persistence":
            parts = []
            for turbine, group in part.groupby("turbine_id"):
                obs = hourly.loc[hourly.complete & hourly.turbine_id.eq(turbine), ["valid_time", "power_normalized"]].copy()
                obs["available_at"] = obs.valid_time + pd.Timedelta(hours=1)
                joined = pd.merge_asof(group.sort_values("issued_at"), obs[["available_at", "power_normalized"]].sort_values("available_at"),
                                       left_on="issued_at", right_on="available_at", direction="backward")
                parts.append(joined)
            part = pd.concat(parts, ignore_index=True)
        else:
            part["power_normalized"] = predict(bundle, part, method)
        matched = part.merge(truth[["valid_time", "turbine_id", "power_normalized"]].rename(columns={"power_normalized": "actual"}),
                             on=["valid_time", "turbine_id"], validate="many_to_one")
        if matched.power_normalized.isna().any():
            raise ValueError("Persistence baseline missing history; cannot compare on equal samples")
        matched["error"] = matched.power_normalized - matched.actual
        matched["horizon_bucket"] = np.where(matched.horizon_hours <= 24, "1-24", "25-48")
        matched["model"] = method
        scored.append(matched)
        for (turbine, bucket), group in matched.groupby(["turbine_id", "horizon_bucket"]):
            results.append(dict(model=method, turbine_id=turbine, horizon_bucket=bucket,
                                mae=float(group.error.abs().mean()), rmse=float(np.sqrt((group.error ** 2).mean())),
                                n_samples=len(group), period_start=iso(start), period_end=iso(end)))
    return pd.DataFrame(results, columns=METRIC_COLUMNS), pd.concat(scored, ignore_index=True)


def train(cfg, offline=False):
    hourly = load_hourly(cfg)
    weather = collect(cfg, cfg["train_start"], cfg["validation_end"], offline)
    december = local_date("2025-12-01", cfg)
    january = local_date(cfg["validation_start"], cfg)
    february = local_date(cfg["validation_end"], cfg)
    tuning = fit(hourly, weather, december, cfg, "tuning")
    tuning_metrics, tuning_pairs = evaluate(tuning, weather, hourly, december, january)
    choices = tuning_pairs.loc[tuning_pairs.model.isin(["catboost", "curve"])]
    selected = choices.groupby("model").error.apply(lambda x: x.abs().mean()).idxmin()
    write_csv(ROOT / "artifacts/december_metrics.csv", tuning_metrics)
    validation = fit(hourly, weather, january, cfg, "validation", selected)
    metrics, pairs = evaluate(validation, weather, hourly, january, february)
    write_csv(ROOT / "artifacts/metrics.csv", metrics)
    write_csv(ROOT / "artifacts/january_predictions.csv", pairs)
    final = fit(hourly, weather, february, cfg, "final", selected)
    report = {"selected": selected, "selection_window": "December 2025",
              "selection_mae": choices.groupby("model").error.apply(lambda x: x.abs().mean()).to_dict(),
              "january_mae": pairs.groupby("model").error.apply(lambda x: x.abs().mean()).to_dict(),
              "january_pairs_per_model": pairs.groupby("model").size().to_dict(),
              "final_model": final["version"], "weather_rows": len(weather)}
    write_json(ROOT / "artifacts/evaluation.json", report)
    print(report, flush=True)
    return report
