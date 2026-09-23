"""December-selected weather postprocessing; January observations never train it."""
import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from .accuracy import observed_before, training_pairs, target_window, curves, curve_predict, summarize, diagnostics
from .common import config, local_date, write_csv, write_json, iso
from .data import load_hourly
from .model import features


METHODS = ["curve", "wind_mae4", "wind_mae6", "wind_rmse4", "wind_mae4_blend",
           "wind_mae6_blend", "direct_mae4", "direct_mae6", "physical_mae4", "physical_mae6",
           "sector_bias", "analog"]


def inputs(frame, physical=False):
    x = features(frame)
    x["wind_shear"] = frame.wind_speed_100m - frame.wind_speed_10m
    x["wind_u"] = frame.wind_speed_100m * x.direction_cos
    x["wind_v"] = frame.wind_speed_100m * x.direction_sin
    # Only neighboring FORECAST values in this same published run; never observations.
    temp = frame.sort_values(["turbine_id", "issued_at", "valid_time"])
    group = temp.groupby(["turbine_id", "issued_at"])
    for shift in [-3, -1, 1, 3]:
        values = group.wind_speed_100m.shift(shift).fillna(temp.wind_speed_100m)
        x[f"wind_shift_{shift}"] = values.reindex(frame.index)
    x["forecast_ramp"] = x["wind_shift_-3"] - x["wind_shift_3"]
    if physical:
        x = x.drop(columns=["hour_sin", "hour_cos", "season_sin", "season_cos"])
    return x


def forecast(method, train, obs, test):
    base = curves(obs)
    baseline = curve_predict(base, test)
    if method == "curve":
        return baseline
    if method == "sector_bias":
        adjusted = test.wind_speed_100m.to_numpy().copy()
        for turbine, part in train.groupby("turbine_id"):
            residual = part.wind_speed - part.wind_speed_100m
            sector = (part.wind_direction_100m / 45).round().astype(int) % 8
            for value in range(8):
                errors = residual[sector.eq(value)]
                mask = test.turbine_id.eq(turbine) & ((test.wind_direction_100m / 45).round().astype(int) % 8).eq(value)
                bias = float(errors.median()) if len(errors) >= 30 else 0.
                adjusted[mask] += bias
        return curve_predict(base, test, np.maximum(0, adjusted))
    if method == "analog":
        output = np.zeros(len(test))
        cols = ["wind_speed_100m", "wind_speed_10m", "direction_sin", "direction_cos", "forecast_ramp", "temperature_2m"]
        a, b = inputs(train)[cols], inputs(test)[cols]
        scale = np.maximum(a.std().to_numpy(), .1)
        for turbine, part in train.groupby("turbine_id"):
            positions = np.flatnonzero(test.turbine_id.eq(turbine))
            historical = a.loc[part.index].to_numpy()/scale
            targets = part.power_normalized.to_numpy()
            for pos in positions:
                distance = ((historical-b.iloc[pos].to_numpy()/scale)**2).sum(axis=1)
                neighbours = np.argpartition(distance, min(49, len(distance)-1))[:50]
                output[pos] = np.median(targets[neighbours])
        return output
    wind = method.startswith("wind_")
    physical = wind or method.startswith("physical_")
    depth = 6 if "6" in method else 4
    loss = "RMSE" if "rmse" in method else "MAE"
    model = CatBoostRegressor(iterations=300, depth=depth, learning_rate=.03, loss_function=loss,
                             l2_leaf_reg=10, random_seed=42, thread_count=4, verbose=False, allow_writing_files=False)
    model.fit(inputs(train, physical), train.wind_speed if wind else train.power_normalized, cat_features=["turbine_id"])
    prediction = model.predict(inputs(test, physical))
    if wind:
        prediction = curve_predict(base, test, np.maximum(0, prediction))
    if method.endswith("blend"):
        prediction = .5*baseline + .5*prediction
    return np.clip(prediction, 0, 1)


def weather_table():
    frames = [pd.read_csv("outputs/accuracy-round2-extension-v2/weather.csv"), pd.read_csv("outputs/accuracy/weather.csv")]
    w = pd.concat(frames, ignore_index=True).drop_duplicates(["turbine_id", "issued_at", "valid_time"])
    for col in ["valid_time", "issued_at", "weather_run_time", "weather_available_at"]:
        w[col] = pd.to_datetime(w[col], utc=True)
    return w.reset_index(drop=True)


def evaluate(hourly, weather, start, end, methods):
    cutoff = local_date(start, config())
    obs = observed_before(hourly, cutoff)
    train = training_pairs(hourly, weather, cutoff).reset_index(drop=True)
    test = target_window(weather, hourly, cutoff, local_date(end, config()))
    # Preserve original selection/test issuance-window convention. Do not train
    # through month end then backdate the preceding origin for the month's first day.
    test = test[test.issued_at >= cutoff].reset_index(drop=True)
    results = []
    for method in methods:
        part = test.copy()
        pred = forecast(method, train, obs, test)
        if not np.isfinite(pred).all():
            raise ValueError("Nonfinite predictions")
        part["prediction"] = np.clip(pred, 0, 1)
        part["error"] = part.prediction - part.actual
        part["model"] = method
        part["fold"] = start[:7]
        results.append(part)
        print(start, method, part.loc[part.horizon_hours>24, "error"].abs().mean(), flush=True)
    return pd.concat(results, ignore_index=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/weather-calibration")
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError("Use a fresh output directory")
    out.mkdir(parents=True)
    hourly, weather = load_hourly(config()), weather_table()
    protocol = {"methods": METHODS, "training_weather_start": "2025-06-01", "selection": "December 2025 day-ahead MAE",
                "january": "previously examined historical test, no January training or recipe selection",
                "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(out / "protocol.json", protocol)
    december = evaluate(hourly, weather, "2025-12-01", "2026-01-01", METHODS)
    scores = december[december.horizon_hours>24].groupby("model").error.apply(lambda e:e.abs().mean()).sort_values()
    selected = scores.index[0]
    write_json(out / "selection.json", {"selected": selected, "december_mae": scores.to_dict()})
    january = evaluate(hourly, weather, "2026-01-01", "2026-02-01", list(dict.fromkeys(["curve", selected])))
    combined = pd.concat([december, january], ignore_index=True)
    write_csv(out / "predictions.csv", combined)
    write_csv(out / "metrics.csv", summarize(combined))
    write_csv(out / "diagnostics.csv", diagnostics(combined, hourly))
    day = january[january.horizon_hours>24]
    scores_jan = day.groupby("model").error.apply(lambda e:e.abs().mean()).to_dict()
    jan31 = day[day.valid_time >= local_date("2026-01-31", config())]
    report = {"selected": selected, "december_mae": scores.to_dict(), "january_mae": scores_jan,
              "january31_mae": jan31.groupby("model").error.apply(lambda e:e.abs().mean()).to_dict(),
              "training_cutoff_january": iso(local_date("2026-01-01", config())), "production_changed": False}
    write_json(out / "report.json", report)
    print(report, flush=True)


if __name__ == "__main__":
    main()
