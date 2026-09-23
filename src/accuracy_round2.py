"""Second exploratory batch: smooth, shrunk and season-aware power curves."""
import argparse
import hashlib
from pathlib import Path
from urllib.error import HTTPError

import numpy as np
import pandas as pd

from .accuracy import curves, curve_predict, observed_before, training_pairs, target_window, summarize, diagnostics, uncertainty
from .common import config, local_date, iso, write_csv, write_json
from .data import load_hourly
from .training import collect
from .weather import Weather


def weighted_quantile(values, weights, quantile=.5):
    values, weights = np.asarray(values), np.asarray(weights)
    if not 0 <= quantile <= 1 or len(values) == 0 or len(values) != len(weights):
        raise ValueError("Invalid quantile input")
    if not np.isfinite(values).all() or not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
        raise ValueError("Invalid quantile weights or values")
    order = np.argsort(values)
    values, weights = np.asarray(values)[order], np.asarray(weights)[order]
    return np.interp(quantile * weights.sum(), np.cumsum(weights), values)


def smooth_curve(train, test, bandwidth, quantile=.5, half_life=None):
    predictions = np.zeros(len(test))
    grid = np.arange(0, 35.01, .1)
    for turbine, group in train.groupby("turbine_id"):
        x, y = group.wind_speed_100m.to_numpy(), group.power_normalized.to_numpy()
        age = (train.issued_at.max() - group.issued_at).dt.total_seconds().to_numpy() / 86400
        recent = np.ones(len(group)) if half_life is None else 2 ** (-age / half_life)
        values = []
        for point in grid:
            weights = np.exp(-.5 * ((x - point) / bandwidth) ** 2) * recent + 1e-100
            values.append(weighted_quantile(y, weights, quantile))
        mask = test.turbine_id.eq(turbine)
        predictions[mask] = np.interp(test.loc[mask, "wind_speed_100m"], grid, values)
    return predictions


def predictions(hourly, weather, start, end):
    test = target_window(weather, hourly, start, end)
    cutoff = test.issued_at.min()
    obs = observed_before(hourly, cutoff)
    train = training_pairs(hourly, weather, cutoff)
    base = curve_predict(curves(obs), test)
    median = curve_predict(curves(obs, statistic="median"), test)
    direct = curve_predict(curves(train, "wind_speed_100m", "median"), test)
    result = {"curve": base, "direct_median": direct, "measured_median": median}
    for weight in [.25, .5, .75]:
        result[f"blend_direct_{weight}"] = (1-weight)*base + weight*direct
        result[f"blend_medians_{weight}"] = (1-weight)*median + weight*direct
    # Same-season observations from prior years: no current/future targets enter fit.
    month = start.tz_convert("Asia/Almaty").month
    distance = np.abs(obs.valid_time.dt.month - month)
    seasonal = obs[np.minimum(distance, 12-distance) <= 1]
    seasonal_prediction = curve_predict(curves(seasonal, statistic="median"), test)
    result["seasonal_median"] = seasonal_prediction
    result["seasonal_blend"] = .5*seasonal_prediction + .5*direct
    for bandwidth in [.5, 1., 2.]:
        smoothed = smooth_curve(train, test, bandwidth)
        result[f"kernel_{bandwidth}"] = smoothed
        result[f"kernel_blend_{bandwidth}"] = .5*base + .5*smoothed
    for half_life in [14, 30]:
        result[f"recent_kernel_{half_life}"] = smooth_curve(train, test, 1., half_life=half_life)
    # Day-ahead-specific relationship uses only training horizons 25–48.
    result["dayahead_kernel"] = smooth_curve(train[train.horizon_hours>24], test, 1.)
    # Quantile variants are explicitly fixed before any round-two scores are read.
    for q in [.4, .6]:
        result[f"kernel_q{q}"] = smooth_curve(train, test, 1., quantile=q)
    return test, result, {"cutoff": iso(cutoff), "training_pairs": len(train), "observations": len(obs)}


def evaluate(hourly, weather, month, methods=None):
    cfg = config()
    start = local_date(month, cfg)
    end = (start.tz_convert(cfg["calendar_timezone"]) + pd.offsets.MonthBegin(1)).tz_convert("UTC")
    test, predicted, recipe = predictions(hourly, weather, start, end)
    results = []
    for name, values in predicted.items():
        if methods is not None and name not in methods:
            continue
        if not np.isfinite(values).all():
            raise ValueError("Nonfinite candidate prediction")
        part = test.copy()
        part["model"] = name
        part["prediction"] = np.clip(values, 0, 1)
        part["error"] = part.prediction - part.actual
        part["fold"] = month[:7]
        results.append(part)
    print("Finished", month, flush=True)
    return pd.concat(results, ignore_index=True), recipe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/accuracy-round2")
    parser.add_argument("--extension", action="store_true", help="Frozen shortlist on previously unused July-September folds")
    parser.add_argument("--fetch-weather", action="store_true", help="Acquire missing extension weather")
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError("Use a new output directory")
    out.mkdir(parents=True)
    hourly = load_hourly(config())
    if args.extension:
        provider = Weather(config(), offline=not args.fetch_weather)
        frames, unavailable = [], []
        for issue in pd.date_range("2025-06-01", "2025-10-01", inclusive="left", freq="D", tz=config()["calendar_timezone"]):
            try:
                frames.append(provider.horizon(issue))
            except HTTPError as exc:
                reason = exc.read().decode("utf-8")
                if exc.code != 400 or "requested model run is not available" not in reason:
                    raise
                unavailable.append({"issue": iso(issue), "reason": reason})
                print("Unavailable origin excluded for every model", iso(issue), flush=True)
            except ValueError as exc:
                if str(exc) != "Weather does not cover the complete 48-hour horizon":
                    raise
                unavailable.append({"issue": iso(issue), "reason": str(exc)})
                print("Incomplete origin excluded for every model", iso(issue), flush=True)
            write_json(out / "unavailable_weather.json", unavailable)
        weather = pd.concat(frames, ignore_index=True)
        write_csv(out / "weather.csv", weather)
        write_json(out / "unavailable_weather.json", unavailable)
        methods = ["curve", "direct_median", "kernel_q0.6", "kernel_q0.4", "kernel_blend_0.5", "blend_direct_0.5"]
        write_json(out / "protocol.json", {"methods": methods, "folds": ["2025-07", "2025-08", "2025-09"],
                                          "purpose": "Earlier-season check of a shortlist frozen after January diagnostics; no selection on these folds"})
        results, recipes = [], {}
        for month in ["2025-07-01", "2025-08-01", "2025-09-01"]:
            part, recipe = evaluate(hourly, weather, month, methods)
            results.append(part); recipes[month] = recipe
        combined = pd.concat(results, ignore_index=True)
        write_csv(out / "predictions.csv", combined)
        write_csv(out / "metrics.csv", summarize(combined))
        write_json(out / "recipes.json", recipes)
        scores = combined[combined.horizon_hours>24].groupby("model").error.apply(lambda e: e.abs().mean())
        write_json(out / "report.json", {"mae": scores.to_dict(), "production_changed": False})
        print(scores.to_string(), flush=True)
        return
    weather = collect(config(), "2025-09-01", "2026-02-01", offline=True)
    write_json(out / "protocol.json", {"selection": "lowest October-December day-ahead MAE", "january": "reused exploratory diagnostic; no untouched holdout", "deployment": "no automatic promotion", "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    results, recipes = [], {}
    for month in ["2025-10-01", "2025-11-01", "2025-12-01"]:
        part, recipe = evaluate(hourly, weather, month)
        results.append(part); recipes[month] = recipe
    pre = pd.concat(results, ignore_index=True)
    scores = pre[pre.horizon_hours>24].groupby("model").error.apply(lambda e: e.abs().mean()).sort_values()
    winner = scores.index[0]
    write_json(out / "selection.json", {"candidate": winner, "development_mae": scores.to_dict()})
    print("Frozen ranking", scores.to_string(), flush=True)
    # All recipes are reported, but January cannot change the frozen selection.
    jan, recipe = evaluate(hourly, weather, "2026-01-01")
    recipes["2026-01-01"] = recipe
    combined = pd.concat([pre, jan], ignore_index=True)
    write_csv(out / "predictions.csv", combined)
    write_csv(out / "metrics.csv", summarize(combined))
    write_csv(out / "diagnostics.csv", diagnostics(combined, hourly))
    write_json(out / "recipes.json", recipes)
    january = jan[jan.horizon_hours>24].groupby("model").error.apply(lambda e: e.abs().mean()).sort_values()
    report = {"candidate": winner, "development_mae": scores.to_dict(), "january_mae": january.to_dict(),
              "development_uncertainty": uncertainty(pre, winner), "january_uncertainty": uncertainty(jan, winner),
              "production_changed": False, "independent_validation_required": True}
    write_json(out / "report.json", report)
    print("January diagnostic", january.to_string(), flush=True)


if __name__ == "__main__":
    main()
