"""Isolated, chronological accuracy experiment; never replaces production models."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from .common import ROOT, config, iso, local_date, write_csv, write_json
from .data import load_hourly
from .model import features
from .training import collect

CANDIDATES = ["curve", "median_curve", "recent_curve", "direct_mean", "direct_median",
              "calibrated_curve", "catboost_original", "mae4", "mae6", "rmse4", "residual"]


def observed_before(hourly, cutoff):
    return hourly.loc[hourly.complete & (hourly.valid_time + pd.Timedelta(hours=1) <= cutoff)].copy()


def training_pairs(hourly, weather, cutoff):
    obs = observed_before(hourly, cutoff)
    part = weather.loc[(weather.issued_at < cutoff) & (weather.valid_time + pd.Timedelta(hours=1) <= cutoff)]
    if (part.weather_available_at > part.issued_at).any():
        raise ValueError("Unavailable weather")
    return part.merge(obs[["valid_time", "turbine_id", "power_normalized", "wind_speed"]],
                      on=["valid_time", "turbine_id"], validate="many_to_one")


def target_window(weather, hourly, start, end):
    part = weather.loc[(weather.valid_time >= start) & (weather.valid_time + pd.Timedelta(hours=1) <= end)].copy()
    truth = hourly.loc[hourly.complete, ["valid_time", "turbine_id", "power_normalized"]]
    result = part.merge(truth.rename(columns={"power_normalized": "actual"}), on=["valid_time", "turbine_id"], validate="many_to_one")
    if (result.weather_available_at > result.issued_at).any():
        raise ValueError("Unavailable test weather")
    if result.duplicated(["issued_at", "valid_time", "turbine_id"]).any():
        raise ValueError("Duplicate test keys")
    return result


def curves(frame, wind="wind_speed", statistic="mean"):
    output = {}
    for turbine, part in frame.groupby("turbine_id"):
        binned = part.groupby((part[wind] * 2).round() / 2).power_normalized.agg([statistic, "count"])
        binned = binned[binned["count"] >= 6]
        if len(binned) < 2:
            raise ValueError("Insufficient curve coverage")
        output[turbine] = (binned.index.to_numpy(), binned[statistic].to_numpy())
    return output


def curve_predict(curve, weather, wind=None):
    speeds = weather.wind_speed_100m.to_numpy() if wind is None else np.asarray(wind)
    return np.array([np.interp(speed, *curve[t]) for t, speed in zip(weather.turbine_id, speeds)])


def boosted(train, test, loss, depth, original=False, residual=False):
    x = features(train)
    y = train.power_normalized.to_numpy() - (train.base.to_numpy() if residual else 0)
    iterations = 400
    if not original:
        split = train.issued_at.max() - pd.Timedelta(days=7)
        inner = train.valid_time + pd.Timedelta(hours=1) <= split
        valid = train.issued_at >= split
        if inner.sum() < 100 or valid.sum() < 100:
            raise ValueError("Insufficient inner chronological split")
        probe = CatBoostRegressor(iterations=400, depth=depth, learning_rate=.05, loss_function=loss,
                                  random_seed=42, thread_count=4, verbose=False, allow_writing_files=False)
        probe.fit(x.loc[inner], y[inner], cat_features=["turbine_id"],
                  eval_set=(x.loc[valid], y[valid]), early_stopping_rounds=40)
        iterations = max(1, probe.get_best_iteration() + 1)
    model = CatBoostRegressor(iterations=iterations, depth=depth, learning_rate=.05, loss_function=loss,
                             random_seed=42, thread_count=4, verbose=False, allow_writing_files=False)
    model.fit(x, y, cat_features=["turbine_id"])
    return model.predict(features(test)), iterations


def crossfit_base(train, hourly):
    train = train.copy()
    train["base"] = np.nan
    # Weekly blocks: the curve sees only observations matured at block start.
    week = ((train.issued_at - train.issued_at.min()).dt.days // 7)
    for _, part in train.groupby(week):
        cutoff = part.issued_at.min()
        curve = curves(observed_before(hourly, cutoff))
        train.loc[part.index, "base"] = curve_predict(curve, part)
    return train


def run_fold(hourly, weather, start, end, methods, legacy=False):
    test = target_window(weather, hourly, start, end)
    if legacy:
        test = test[test.issued_at >= start].copy()
    cutoff = start if legacy else test.issued_at.min()
    obs = observed_before(hourly, cutoff)
    train = training_pairs(hourly, weather, cutoff)
    base = curves(obs)
    outputs, recipes = [], {}
    for method in methods:
        meta = {"cutoff": iso(cutoff), "training_pairs": len(train), "observations": len(obs)}
        if method == "curve":
            pred = curve_predict(base, test)
        elif method == "persistence":
            pred = np.zeros(len(test))
            for turbine in test.turbine_id.unique():
                history = hourly.loc[hourly.complete & hourly.turbine_id.eq(turbine)].copy()
                history["available_at"] = history.valid_time + pd.Timedelta(hours=1)
                mask = test.turbine_id.eq(turbine)
                part = test.loc[mask].assign(row_position=np.flatnonzero(mask)).sort_values("issued_at")
                joined = pd.merge_asof(part, history[["available_at", "power_normalized"]].sort_values("available_at"), left_on="issued_at", right_on="available_at", direction="backward")
                pred[joined.row_position.to_numpy()] = joined.power_normalized.to_numpy()
            if not np.isfinite(pred).all():
                raise ValueError("Persistence history missing")
        elif method == "median_curve":
            pred = curve_predict(curves(obs, statistic="median"), test)
        elif method == "recent_curve":
            pred = curve_predict(curves(obs[obs.valid_time >= weather.issued_at.min()]), test)
        elif method.startswith("direct_"):
            pred = curve_predict(curves(train, "wind_speed_100m", method.split("_")[1]), test)
        elif method == "calibrated_curve":
            corrected = test.wind_speed_100m.to_numpy().copy()
            for turbine, part in train.groupby("turbine_id"):
                # A small, explicit affine calibration; no test observations used.
                slope, intercept = np.polyfit(part.wind_speed_100m, part.wind_speed, 1)
                mask = test.turbine_id.eq(turbine).to_numpy()
                corrected[mask] = np.maximum(0, slope * corrected[mask] + intercept)
                meta[turbine] = {"slope": float(slope), "intercept": float(intercept)}
            pred = curve_predict(base, test, corrected)
        elif method == "residual":
            correction, rounds = boosted(crossfit_base(train, hourly), test, "MAE", 4, residual=True)
            pred = curve_predict(base, test) + correction
            meta["iterations"] = rounds
        else:
            loss = "MAE" if method.startswith("mae") else "RMSE"
            depth = 4 if method.endswith("4") else 6
            pred, rounds = boosted(train, test, loss, depth, original=method == "catboost_original")
            meta.update(loss=loss, depth=depth, iterations=rounds)
        result = test.copy()
        result["prediction"] = np.clip(pred, 0, 1)
        result["error"] = result.prediction - result.actual
        result["model"] = method
        result["fold"] = start.tz_convert("Asia/Almaty").strftime("%Y-%m")
        outputs.append(result)
        recipes[method] = meta
        print(result.fold.iloc[0], method, "day-ahead MAE", result.loc[result.horizon_hours > 24, "error"].abs().mean(), flush=True)
    return pd.concat(outputs, ignore_index=True), recipes


def summarize(pairs):
    pairs = pairs.copy()
    pairs["bucket"] = np.where(pairs.horizon_hours > 24, "25-48", "1-24")
    records = []
    for (fold, model, bucket), group in pairs.groupby(["fold", "model", "bucket"]):
        for turbine, part in [("all", group), *group.groupby("turbine_id")]:
            records.append(dict(fold=fold, model=model, bucket=bucket, turbine=turbine, n=len(part),
                                mae=float(part.error.abs().mean()), rmse=float(np.sqrt((part.error**2).mean())), bias=float(part.error.mean())))
    return pd.DataFrame(records)


def diagnostics(pairs, hourly):
    part = pairs[pairs.horizon_hours > 24].copy()
    part["wind_regime"] = pd.cut(part.wind_speed_100m, [-np.inf, 4, 8, 12, np.inf],
                                 labels=["below4", "4to8", "8to12", "above12"]).astype(str)
    # Actual ramp is for retrospective error analysis only, never a model input.
    truth = hourly.loc[hourly.complete, ["valid_time", "turbine_id", "power_normalized"]].copy()
    previous = truth.copy()
    previous["valid_time"] += pd.Timedelta(hours=1)
    truth = truth.merge(previous, on=["valid_time", "turbine_id"], suffixes=("", "_previous"))
    truth["actual_ramp"] = (truth.power_normalized - truth.power_normalized_previous).abs()
    part = part.merge(truth[["valid_time", "turbine_id", "actual_ramp"]], on=["valid_time", "turbine_id"], how="left", validate="many_to_one")
    part["ramp_regime"] = np.where(part.actual_ramp.isna(), "unknown", np.where(part.actual_ramp >= .2, "at_least_0.2", "below_0.2"))
    records = []
    for dimension in ["wind_regime", "ramp_regime"]:
        for (fold, model, regime), group in part.groupby(["fold", "model", dimension]):
            records.append(dict(fold=fold, model=model, dimension=dimension, regime=regime, n=len(group),
                                mae=float(group.error.abs().mean()), bias=float(group.error.mean())))
    return pd.DataFrame(records)


def uncertainty(pairs, candidate):
    keys = ["fold", "issued_at", "valid_time", "turbine_id"]
    part = pairs[pairs.horizon_hours > 24]
    base = part[part.model.eq("curve")][keys + ["error"]]
    other = part[part.model.eq(candidate)][keys + ["error"]]
    joined = base.merge(other, on=keys, validate="one_to_one", suffixes=("_base", "_candidate"))
    joined["gain"] = joined.error_base.abs() - joined.error_candidate.abs()
    joined["day"] = joined.valid_time.dt.tz_convert("Asia/Almaty").dt.date
    daily = joined.groupby(["fold", "day"]).gain.agg(["sum", "count"])
    intervals = {}
    for length in [1, 3, 7]:
        rng = np.random.default_rng(42)
        samples = []
        for _ in range(2000):
            total, count = 0., 0
            for _, fold in daily.groupby(level=0):
                a = fold.to_numpy(); n = len(a)
                starts = rng.integers(0, n, size=int(np.ceil(n / length)))
                indices = np.concatenate([(s + np.arange(length)) % n for s in starts])[:n]
                sampled = a[indices].sum(axis=0)
                total += sampled[0]
                count += sampled[1]
            samples.append(total / count)
        intervals[str(length)] = np.quantile(samples, [.025, .975]).tolist()
    return {"absolute_mae_gain": float(joined.gain.mean()), "block_day_95pct_intervals": intervals}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/accuracy")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    if (out / "report.json").exists():
        raise ValueError("Use a fresh output directory; completed experiments are immutable")
    cfg = config(); hourly = load_hourly(cfg)
    weather = collect(cfg, "2025-09-01", "2026-02-01", args.offline)
    write_csv(out / "weather.csv", weather)
    records, recipes = [], {}
    for start, end in [("2025-10-01", "2025-11-01"), ("2025-11-01", "2025-12-01"), ("2025-12-01", "2026-01-01")]:
        pairs, recipe = run_fold(hourly, weather, local_date(start, cfg), local_date(end, cfg), CANDIDATES)
        records.append(pairs); recipes[start] = recipe
    pre = pd.concat(records, ignore_index=True)
    day = pre[pre.horizon_hours > 24]
    scores = day.groupby("model").error.apply(lambda e: e.abs().mean()).sort_values()
    chosen = scores.drop("curve").index[0]
    ci = uncertainty(pre, chosen)
    byfold = day.groupby(["fold", "model"]).error.apply(lambda e: e.abs().mean()).unstack()
    turbine = day.groupby(["turbine_id", "model"]).error.apply(lambda e: e.abs().mean()).unstack()
    gates = {"relative_gain_at_least_3pct": bool(1 - scores[chosen] / scores["curve"] >= .03),
             "wins_at_least_two_folds": bool((byfold[chosen] < byfold["curve"]).sum() >= 2),
             "turbine_regression_at_most_005": bool(((turbine[chosen] - turbine["curve"]) <= .005).all()),
             "positive_block_intervals": bool(all(v[0] > 0 for v in ci["block_day_95pct_intervals"].values()))}
    # Persist selection BEFORE examining January. January does not select a runner-up.
    selection = {"candidate": chosen, "scores": scores.to_dict(), "gates": gates, "uncertainty": ci,
                 "weather_sha256": hashlib.sha256((out / "weather.csv").read_bytes()).hexdigest(), "config": cfg}
    write_json(out / "selection.json", selection)
    jan, recipe = run_fold(hourly, weather, local_date("2026-01-01", cfg), local_date("2026-02-01", cfg), list(dict.fromkeys(["curve", "catboost_original", "persistence", chosen])))
    recipes["2026-01-01"] = recipe
    january = jan[jan.horizon_hours > 24].groupby("model").error.apply(lambda e: e.abs().mean())
    selection["january_mae"] = january.to_dict()
    selection["january_no_regression"] = bool(january[chosen] <= january["curve"])
    selection["promote"] = bool(all(gates.values()) and selection["january_no_regression"])
    selection["january_is_reused_evaluation"] = True
    all_pairs = pd.concat([pre, jan], ignore_index=True)
    write_csv(out / "predictions.csv", all_pairs)
    write_csv(out / "metrics.csv", summarize(all_pairs))
    write_csv(out / "diagnostics.csv", diagnostics(all_pairs, hourly))
    write_json(out / "recipes.json", recipes)
    write_json(out / "report.json", selection)
    legacy, _ = run_fold(hourly, weather, local_date("2026-01-01", cfg), local_date("2026-02-01", cfg), ["curve", "catboost_original", "persistence"], legacy=True)
    write_csv(out / "legacy_metrics.csv", summarize(legacy))
    write_json(out / "legacy_pooled_mae.json", legacy.groupby("model").error.apply(lambda e: e.abs().mean()).to_dict())
    print(json.dumps(selection, indent=2), flush=True)


if __name__ == "__main__":
    main()
