"""Conditional as-of weather inputs, full-month causal boundaries, fixed recipes.

Previous Runs does not expose exact historical publication timestamps. Offset
eligibility uses documented lead semantics plus an assumed eight-hour delay;
this benchmark cannot establish operational publication provenance.
"""
import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from threadpoolctl import threadpool_limits

from .accuracy import curves, curve_predict, observed_before, training_pairs, target_window, summarize
from .common import config, local_date, write_csv, write_json
from .data import load_hourly
from .live_bias import telemetry
from .provider_weather import MODELS, VARIABLES
from .weather_calibration import inputs, weather_table

METHODS = ["curve", "fresh_cat", "fresh_hist", "fresh_cat_time", "fresh_hist_time",
           "fresh_cat_live", "fresh_wind_cat", "blend_fresh_hist_time", "blend_fresh_cat_time"]
DELAY = pd.Timedelta(hours=2)
PUBLICATION_DELAY = pd.Timedelta(hours=8)


def eligible_features(weather, archives):
    """Select each fixed-offset value only if conditionally available at issue.

    Drop raw archive columns before downstream trajectory feature engineering:
    even a neighbouring target must use its own eligible offset at this issue.
    """
    keys = ["turbine_id", "valid_time"]
    frame = weather.copy()
    for offset in [2, 3]:
        archive = archives[offset]
        columns = [c for c in archive if f"_previous_day{offset}_" in c]
        frame = frame.merge(archive[keys + columns], on=keys, how="left", validate="many_to_one")
    use_day2 = frame.valid_time - pd.Timedelta(hours=48) + PUBLICATION_DELAY <= frame.issued_at
    frame["provider_offset_hours"] = np.where(use_day2, 48, 72)
    frame["provider_available_bound"] = (frame.valid_time - pd.to_timedelta(frame.provider_offset_hours, unit="h") + PUBLICATION_DELAY)
    if (frame.provider_available_bound > frame.issued_at).any():
        raise ValueError("No eligible archive offset at this issuance")
    for model in MODELS:
        for variable in VARIABLES:
            a, b = [f"{variable}_previous_day{d}_{model}" for d in [2, 3]]
            frame[f"provider_{variable}_{model}"] = np.where(use_day2, frame[a], frame[b])
    return frame.drop(columns=[c for c in frame if "_previous_day" in c])


def matrix(frame, method):
    x = inputs(frame, physical=not method.endswith("time"))
    x["turbine_id"] = frame.turbine_id.map({"turbine_1": 0., "turbine_2": 1.})
    columns = [c for c in frame if c.startswith("provider_") and c != "provider_available_bound"]
    for column in columns:
        if "direction" in column:
            x[column + "_sin"] = np.sin(np.deg2rad(frame[column]))
            x[column + "_cos"] = np.cos(np.deg2rad(frame[column]))
        else:
            x[column] = frame[column]
    # Every value in the trajectory has already passed the same-issuance bound.
    temp = frame.sort_values(["turbine_id", "issued_at", "valid_time"])
    groups = temp.groupby(["turbine_id", "issued_at"])
    for column in [c for c in columns if "wind_speed" in c]:
        for shift in [-6, -3, 3, 6]:
            x[f"{column}_shift{shift}"] = groups[column].shift(shift).fillna(temp[column]).reindex(frame.index)
        x[column + "_day_mean"] = groups[column].transform("mean").reindex(frame.index)
        x[column + "_day_std"] = groups[column].transform("std").reindex(frame.index)
    if method.endswith("live"):
        for col in ["last_power", "last_wind", "telemetry_age", "power6", "power24", "bias6", "bias24", "bias_count"]:
            x[col] = frame[col]
    return x


def predict_all(train, obs, test):
    base = curves(obs)
    predictions = {"curve": curve_predict(base, test)}
    for method in METHODS[1:]:
        if method.startswith("blend_"):
            predictions[method] = .5 * predictions["curve"] + .5 * predictions[method.removeprefix("blend_")]
            continue
        if "hist" in method:
            model = HistGradientBoostingRegressor(loss="absolute_error", max_iter=300, max_leaf_nodes=15,
                min_samples_leaf=40, l2_regularization=10, early_stopping=False, learning_rate=.05, random_state=42)
        else:
            model = CatBoostRegressor(iterations=350, depth=5, learning_rate=.03, l2_leaf_reg=10,
                loss_function="MAE", random_seed=42, thread_count=2, verbose=False, allow_writing_files=False)
        x, y = matrix(train, method), matrix(test, method)
        usable = x.columns[~x.isna().all()]
        pipeline = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), model)
        with threadpool_limits(limits=2):
            pipeline.fit(x[usable], train.wind_speed if method == "fresh_wind_cat" else train.power_normalized)
            p = pipeline.predict(y[usable])
        if method == "fresh_wind_cat":
            p = curve_predict(base, test, np.maximum(0, p))
        if method.endswith("live"):
            stale = test.telemetry_age.gt(5).to_numpy()
            p[stale] = predictions["curve"][stale]
        predictions[method] = np.clip(p, 0, 1)
    return predictions


def causal_training(hourly, weather, cutoff):
    """All fitted labels must be reported at least two hours after interval end."""
    return (training_pairs(hourly, weather, cutoff - DELAY),
            observed_before(hourly, cutoff - DELAY))


def evaluate(hourly, weather, state, archives, start, end):
    month_start = local_date(start, config())
    all_weather = eligible_features(weather[weather.horizon_hours > 24], archives)
    all_weather = all_weather.merge(state, on=["issued_at", "turbine_id"], validate="many_to_one")
    test = target_window(all_weather, hourly, month_start, local_date(end, config()))
    test["fit_cutoff"] = test.issued_at.where(test.issued_at < month_start, month_start)
    rows = []
    for cutoff, part in test.groupby("fit_cutoff"):
        train, obs = causal_training(hourly, all_weather, cutoff)
        # Archive coverage starts June; left joins preserve all target keys.
        train, part = train.reset_index(drop=True), part.reset_index(drop=True)
        if (train.valid_time + pd.Timedelta(hours=3) > cutoff).any():
            raise ValueError("Training label was not available at model cutoff")
        predictions = predict_all(train, obs, part)
        for method, prediction in predictions.items():
            result = part.copy()
            result["prediction"] = prediction
            result["error"] = prediction - result.actual
            result["model"] = method
            result["fold"] = start[:7]
            rows.append(result)
        print(start, "fit", cutoff, "training", len(train), "targets", len(part), flush=True)
    result = pd.concat(rows, ignore_index=True)
    if result.duplicated(["model", "turbine_id", "valid_time"]).any():
        raise ValueError("Duplicate day-ahead targets")
    return result


def scores(frame):
    return frame.groupby("model").error.apply(lambda x: x.abs().mean()).sort_values().to_dict()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/fresh-provider-benchmark")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    h, w = load_hourly(config()), weather_table()
    state = telemetry(h, w, w.issued_at.unique())
    archives = {}
    for offset, directory in [(2, "outputs/provider-weather-day2"), (3, "outputs/provider-weather")]:
        archives[offset] = pd.read_csv(Path(directory) / "weather.csv")
        archives[offset].valid_time = pd.to_datetime(archives[offset].valid_time, utc=True)
    protocol = {"methods": METHODS, "telemetry_and_label_delay_hours": 2, "weather_publication_delay_hours": 8,
        "selection": "December 1-29 target days; all labels available before Jan1 forecast issuance on Dec31",
        "training": "Before each month plus separate earlier boundary fit, labels must mature cutoff minus 2h",
        "january": "Reused diagnostic; no January training or recipe selection",
        "scope": "Both turbines, full target month, interval starts 24-47h after issuance",
        "target_mae_max": .05, "publication_provenance_verified": False,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(out / "protocol.json", protocol)
    dec = evaluate(h, w, state, archives, "2025-12-01", "2026-01-01")
    selection = scores(dec[dec.valid_time < local_date("2025-12-30", config())])
    selected = next(iter(selection))
    write_json(out / "selection.json", {"selected": selected, "december1_29_mae": selection})
    write_csv(out / "december_predictions.csv", dec)
    jan = evaluate(h, w, state, archives, "2026-01-01", "2026-02-01")
    if not jan.groupby("model").size().eq(1488).all():
        raise ValueError("Full January requires 1,488 target pairs per model")
    allp = pd.concat([dec, jan], ignore_index=True)
    write_csv(out / "predictions.csv", allp)
    write_csv(out / "metrics.csv", summarize(allp))
    day = jan[jan.valid_time >= local_date("2026-01-31", config())]
    january, january31 = scores(jan), scores(day)
    report = {"selected": selected, "december1_29_mae": selection, "december_full_mae": scores(dec),
        "january_full_mae": january, "january2_31_mae": scores(jan[jan.valid_time >= local_date("2026-01-02", config())]),
        "january31_mae": january31, "n_january_per_model": 1488, "n_january31_per_model": len(day[day.model == "curve"]),
        "numerical_target_met_by": [m for m in METHODS if january[m] <= .05 and january31[m] <= .05],
        "production_changed": False, "publication_provenance_verified": False}
    write_json(out / "report.json", report)
    print(report, flush=True)


if __name__ == "__main__":
    main()
