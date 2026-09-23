"""Frozen daily sequence models with issuance-safe 168-hour telemetry histories.

This is an isolated research benchmark, not a production forecast entry point.
"""
import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.multioutput import MultiOutputRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from .accuracy import curves, curve_predict, observed_before, target_window, summarize
from .common import config, iso, local_date, write_csv, write_json
from .data import load_hourly
from .weather_calibration import weather_table

DELAY = 2
LAGS = 168
RAW_METHODS = ["ar_ridge100", "ar_ridge1000", "ar_extra", "weather_ridge100",
               "weather_ridge1000", "weather_extra", "weather_hist"]
METHODS = ["curve", *RAW_METHODS, *["blend_" + m for m in RAW_METHODS]]


def history_features(hourly, issues, delay=DELAY, lags=LAGS):
    """The last eligible interval starts issue-(delay+1) hours; no future fill."""
    if delay < 0 or lags < 1:
        raise ValueError("Invalid history window")
    records = []
    for turbine, group in hourly.groupby("turbine_id"):
        source = group[group.complete].set_index("valid_time").sort_index()
        if source.index.has_duplicates:
            raise ValueError("Duplicate observation timestamps")
        for issue in sorted(pd.to_datetime(issues, utc=True).unique()):
            issue = pd.Timestamp(issue)
            times = issue - pd.to_timedelta(np.arange(lags) + delay + 1, unit="h")
            past = source.reindex(times)
            row = {"issued_at": issue, "turbine_id": turbine,
                   "turbine_number": float(turbine.rsplit("_", 1)[-1]),
                   "season_sin": np.sin(issue.dayofyear * 2 * np.pi / 365.25),
                   "season_cos": np.cos(issue.dayofyear * 2 * np.pi / 365.25),
                   "observed_history_hours": int(past.power_normalized.notna().sum())}
            for variable in ["power_normalized", "wind_speed"]:
                values = past[variable].to_numpy()
                row.update({f"{variable}_lag{i}": value for i, value in enumerate(values)})
                for length in [6, 24, 48, 72, 168]:
                    selected = values[:length]
                    finite = selected[np.isfinite(selected)]
                    for name, func in [("mean", np.mean), ("std", np.std), ("min", np.min), ("max", np.max)]:
                        row[f"{variable}_{name}{length}"] = float(func(finite)) if len(finite) else np.nan
            records.append(row)
    return pd.DataFrame(records).set_index(["issued_at", "turbine_id"]).sort_index()


def sequences(hourly, issues):
    """Twenty-four target values at leads 25--48; missing targets stay missing."""
    rows = []
    for turbine, group in hourly.groupby("turbine_id"):
        values = group[group.complete].set_index("valid_time").power_normalized
        for issue in sorted(pd.to_datetime(issues, utc=True).unique()):
            issue = pd.Timestamp(issue)
            future = values.reindex(issue + pd.to_timedelta(np.arange(24, 48), unit="h"))
            rows.append({"issued_at": issue, "turbine_id": turbine,
                         **{f"target_{lead}": value for lead, value in zip(range(25, 49), future)}})
    return pd.DataFrame(rows).set_index(["issued_at", "turbine_id"]).sort_index()


def weather_features(weather):
    if (weather.weather_available_at > weather.issued_at).any():
        raise ValueError("Weather unavailable at forecast issuance")
    rows = []
    for (issue, turbine), part in weather.groupby(["issued_at", "turbine_id"]):
        part = part.sort_values("horizon_hours")
        if part.horizon_hours.tolist() != list(range(1, 49)):
            raise ValueError("Expected exactly one complete 48-hour weather path")
        expected = issue + pd.to_timedelta(part.horizon_hours - 1, unit="h")
        if not (part.valid_time.to_numpy() == expected.to_numpy()).all():
            raise ValueError("Weather lead and target timestamp disagree")
        row = {"issued_at": issue, "turbine_id": turbine}
        for col in ["wind_speed_100m", "wind_speed_10m", "temperature_2m"]:
            row.update({f"weather_{col}_{i}": value for i, value in zip(part.horizon_hours, part[col])})
        for name, func in [("sin", np.sin), ("cos", np.cos)]:
            row.update({f"weather_direction_{name}_{i}": value for i, value in
                        zip(part.horizon_hours, func(np.deg2rad(part.wind_direction_100m)))})
        rows.append(row)
    return pd.DataFrame(rows).set_index(["issued_at", "turbine_id"]).sort_index()


def eligible_training(y, cutoff, delay=DELAY):
    """Every target interval and its reporting delay must finish before fitting."""
    if delay < 0:
        raise ValueError("Negative reporting delay")
    origin = y.index.get_level_values("issued_at")
    return y.loc[(origin + pd.Timedelta(hours=48 + delay) <= cutoff) & y.notna().all(axis=1)]


def estimator(method):
    if "ridge" in method:
        model = Ridge(alpha=1000. if method.endswith("1000") else 100.)
    elif method.endswith("extra"):
        model = ExtraTreesRegressor(n_estimators=180, min_samples_leaf=8, max_features=.65,
                                    max_depth=20, random_state=42, n_jobs=2)
    elif method.endswith("hist"):
        model = MultiOutputRegressor(HistGradientBoostingRegressor(loss="absolute_error", max_iter=120,
                    max_leaf_nodes=7, min_samples_leaf=20, learning_rate=.04, l2_regularization=20,
                    early_stopping=False, random_state=42), n_jobs=1)
    else:
        raise ValueError(method)
    return make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), model)


def evaluate(hourly, weather, history, targets, wx, start, end):
    start_time, end_time = local_date(start, config()), local_date(end, config())
    test = target_window(weather, hourly, start_time, end_time)
    test = test[test.horizon_hours.between(25, 48)].copy()
    expected = (end_time - start_time).days * 24 * len(config()["turbines"])
    if start.startswith("2026-01") and len(test) != expected:
        raise ValueError(f"Incomplete month: {len(test)} / {expected}")
    outputs, fitting = [], {"coverage": {"expected_pairs": expected, "scored_pairs": len(test),
                                       "missing_incomplete_observations": expected-len(test)}}
    # The month's first target day was issued the previous day. It must use an
    # earlier frozen model; the remainder uses the first-of-month cutoff.
    for label, mask in [("boundary", test.issued_at < start_time), ("main", test.issued_at >= start_time)]:
        part = test[mask].sort_values(["issued_at", "turbine_id", "horizon_hours"]).copy()
        if part.empty:
            continue
        cutoff = part.issued_at.min() if label == "boundary" else start_time
        # Training labels follow the same conservative delay as live inputs.
        curve = curves(observed_before(hourly, cutoff - pd.Timedelta(hours=DELAY)))
        baseline = curve_predict(curve, part)
        index = pd.MultiIndex.from_frame(part[["issued_at", "turbine_id"]].drop_duplicates())
        train_y = eligible_training(targets, cutoff)
        complete_history = history.observed_history_hours.ge(144)
        train_y = train_y.loc[train_y.index.intersection(history[complete_history].index)]
        fitting[label] = {"cutoff": iso(cutoff), "training_sequences": {}, "max_training_target_available_at": {}}
        predictions = {"curve": baseline}
        for method in RAW_METHODS:
            with_weather = method.startswith("weather_")
            all_x = history.join(wx, how="inner") if with_weather else history
            train_index = train_y.index.intersection(all_x.index)
            y = train_y.loc[train_index]
            if len(y) < 100:
                raise ValueError("Insufficient training sequences")
            model = estimator(method)
            with threadpool_limits(limits=2):
                model.fit(all_x.loc[train_index], y)
                predicted = model.predict(all_x.loc[index])
            # Multioutput columns are leads 25--48 in the same row order as part.
            sequence_result = pd.DataFrame(predicted, index=index, columns=range(25, 49)).stack()
            row_index = pd.MultiIndex.from_frame(part[["issued_at", "turbine_id", "horizon_hours"]])
            p = np.clip(sequence_result.reindex(row_index).to_numpy(), 0, 1)
            if not np.isfinite(p).all():
                raise ValueError(f"Nonfinite {method}")
            predictions[method] = p
            predictions["blend_" + method] = .5 * p + .5 * baseline
            fitting[label]["training_sequences"][method] = len(y)
            fitting[label]["max_training_target_available_at"][method] = iso(train_index.get_level_values("issued_at").max() + pd.Timedelta(hours=50))
            print(start, label, method, float(np.abs(p-part.actual).mean()), flush=True)
        for method in METHODS:
            record = part[["issued_at", "valid_time", "turbine_id", "horizon_hours", "actual"]].copy()
            record["prediction"] = predictions[method]
            record["error"] = record.prediction - record.actual
            record["fold"], record["model"], record["partition"] = start[:7], method, label
            record["trained_at"] = cutoff
            outputs.append(record)
    return pd.concat(outputs, ignore_index=True), fitting


def ranking(frame):
    return frame.groupby("model").error.apply(lambda e: float(e.abs().mean())).sort_values().to_dict()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/sequence-benchmark")
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError("Use a new output directory")
    out.mkdir(parents=True)
    hourly, weather = load_hourly(config()), weather_table()
    daily = pd.date_range(hourly.valid_time.min().normalize() + pd.Timedelta(hours=19),
                          hourly.valid_time.max(), freq="D")
    issues = daily.union(pd.DatetimeIndex(weather.issued_at.unique()))
    history, targets, wx = history_features(hourly, issues), sequences(hourly, issues), weather_features(weather)
    write_json(out / "protocol.json", {"methods": METHODS, "history_hours": LAGS,
        "reporting_delay_after_interval_end_hours": DELAY, "max_cpu_threads": 2,
        "selection": "December 1--29 complete target days; selection available before Dec 31 issuance for Jan 1",
        "boundary": "Separate earlier frozen model for first target day; no backdated fitting",
        "training": "AR uses all earlier turbine history; weather-conditioned models use archived paired period",
        "january": "Previously examined historical test; no January fitting, conservative past telemetry authorized",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    dec, dfit = evaluate(hourly, weather, history, targets, wx, "2025-12-01", "2026-01-01")
    selection_end = local_date("2025-12-30", config())
    dec_rank = ranking(dec[dec.valid_time < selection_end])
    selected = next(iter(dec_rank))
    write_json(out / "selection.json", {"selected": selected, "december_1_to_29_mae": dec_rank,
        "selection_available_by": iso(local_date("2025-12-31", config()))})
    write_csv(out / "december_predictions.csv", dec)
    print("FROZEN DECEMBER SELECTION", selected, flush=True)
    jan, jfit = evaluate(hourly, weather, history, targets, wx, "2026-01-01", "2026-02-01")
    result = pd.concat([dec, jan], ignore_index=True)
    write_csv(out / "predictions.csv", result)
    write_csv(out / "metrics.csv", summarize(result))
    write_json(out / "fitting.json", {"2025-12": dfit, "2026-01": jfit})
    last = jan[jan.valid_time >= local_date("2026-01-31", config())]
    report = {"selected": selected, "december_1_to_29_mae": dec_rank, "december_full_month_mae": ranking(dec), "january_mae": ranking(jan),
        "january_2_to_31_mae": ranking(jan[jan.partition.eq("main")]), "january31_mae": ranking(last),
        "pairs_per_method": jan.groupby("model").size().to_dict(),
        "jan31_pairs_per_method": last.groupby("model").size().to_dict(), "production_changed": False,
        "target_005_met_by_selected": bool(ranking(jan)[selected] <= .05 and ranking(last)[selected] <= .05)}
    write_json(out / "report.json", report)
    print(report, flush=True)


if __name__ == "__main__":
    main()
