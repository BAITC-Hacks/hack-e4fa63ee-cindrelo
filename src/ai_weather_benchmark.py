"""Isolated AIFS/GEM archive benchmark with causal fits and fixed recipes.

Historical publication remains conditional: Previous Runs returns lead offsets,
not original run/publication timestamps. No production artifacts are changed.
"""
import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from threadpoolctl import threadpool_limits

from .accuracy import curves, curve_predict, target_window, summarize
from .common import ROOT, config, digest, iso, local_date, write_csv, write_json
from .fresh_provider_benchmark import causal_training, eligible_features, matrix as base_matrix, scores
from .live_bias import telemetry
from .weather_calibration import weather_table

COMMON_VARIABLES = ["temperature_2m", "surface_pressure", "relative_humidity_2m", "cloud_cover", "precipitation"]
EXTRA_VARIABLES = {
    "ecmwf_aifs025_single": ["wind_speed_10m", "wind_speed_100m", "wind_direction_10m", "wind_direction_100m", *COMMON_VARIABLES],
    "cmc_gem_gdps": ["wind_speed_10m", "wind_speed_80m", "wind_speed_120m", "wind_direction_10m", "wind_direction_80m", "wind_direction_120m", *COMMON_VARIABLES],
}
METHODS = ["curve", "no_extra_cat", "add_cat", "add_hist", "add_cat_live", "add_wind_cat", "blend_add_cat"]
PUBLICATION_DELAY = pd.Timedelta(hours=8)
RECIPES = {
    "no_extra_cat": {"family": "CatBoost", "target": "power", "iterations": 300, "depth": 5, "loss": "MAE", "calendar_features": True, "additional_weather": False},
    "add_cat": {"family": "CatBoost", "target": "power", "iterations": 300, "depth": 5, "loss": "MAE", "calendar_features": True},
    "add_hist": {"family": "HistGradientBoosting", "target": "power", "iterations": 250, "leaves": 15, "loss": "absolute_error", "calendar_features": True},
    "add_cat_live": {"family": "CatBoost", "target": "power", "iterations": 300, "depth": 5, "loss": "MAE", "calendar_features": False, "reporting_delay_hours": 2, "stale_fallback_hours": 5},
    "add_wind_cat": {"family": "CatBoost", "target": "measured_wind_then_curve", "iterations": 300, "depth": 4, "loss": "RMSE", "calendar_features": False},
    "blend_add_cat": {"curve_weight": .5, "add_cat_weight": .5},
}
SOURCES = ["https://open-meteo.com/en/docs/previous-runs-api", "https://open-meteo.com/en/docs/ecmwf-api", "https://open-meteo.com/en/docs/gem-api", "https://open-meteo.com/en/docs/model-updates"]


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def acquisition(out, offline=False):
    """Sequential monthly queries for both sites, both models and offsets 2/3."""
    cfg = config()
    cache = ROOT / "data/ai-weather"
    cache.mkdir(parents=True, exist_ok=True)
    variables = sorted(set(v for vs in EXTRA_VARIABLES.values() for v in vs))
    frames, missingness, gaps, records = [], [], [], []
    for start in pd.date_range("2025-06-01", "2026-01-01", freq="MS"):
        end = start + pd.offsets.MonthEnd(0)
        params = {"latitude": ",".join(str(p["latitude"]) for p in cfg["turbines"].values()),
            "longitude": ",".join(str(p["longitude"]) for p in cfg["turbines"].values()),
            "start_date": str(start.date()), "end_date": str(end.date()),
            "hourly": ",".join(f"{v}_previous_day{d}" for d in [2, 3] for v in variables),
            "models": ",".join(EXTRA_VARIABLES), "timezone": "UTC", "wind_speed_unit": "ms"}
        path = cache / (digest(params) + ".json")
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record["request"] != params or digest(record["response"]) != record["content_hash"]:
                raise ValueError("Additional-weather cache checksum/request mismatch")
        else:
            if offline:
                raise ValueError(f"Missing offline cache for {start.date()}")
            url = "https://previous-runs-api.open-meteo.com/v1/forecast?" + urlencode(params)
            for attempt in range(3):
                try:
                    with urlopen(url, timeout=45) as response:
                        raw = response.read()
                    payload = json.loads(raw)
                    break
                except (HTTPError, URLError, TimeoutError) as error:
                    if isinstance(error, HTTPError) and error.code not in [429, 500, 502, 503, 504]:
                        raise
                    if attempt == 2:
                        raise
                    print("archive retry", start.date(), type(error).__name__, flush=True)
                    time.sleep(2 * (attempt + 1))
            record = {"request": params, "url": url, "retrieved_at": pd.Timestamp.now(tz="UTC").isoformat(),
                "raw_response_sha256": hashlib.sha256(raw).hexdigest(), "response": payload, "content_hash": digest(payload)}
            write_json(path, record)
        payloads = record["response"]
        if not isinstance(payloads, list) or len(payloads) != len(cfg["turbines"]):
            raise ValueError("Unexpected multi-location response")
        expected = pd.date_range(start.tz_localize("UTC"), (end + pd.Timedelta(days=1)).tz_localize("UTC"), freq="h", inclusive="left")
        for turbine, payload in zip(cfg["turbines"], payloads):
            frame = pd.DataFrame(payload["hourly"]).rename(columns={"time": "valid_time"})
            frame["valid_time"] = pd.to_datetime(frame.valid_time, utc=True)
            if frame.valid_time.duplicated().any() or not pd.DatetimeIndex(frame.valid_time).equals(expected):
                raise ValueError("Unexpected archive hourly keys")
            frame["turbine_id"] = turbine
            frames.append(frame)
            for model in EXTRA_VARIABLES:
                for offset in [2, 3]:
                    for variable in variables:
                        column = f"{variable}_previous_day{offset}_{model}"
                        if column not in frame:
                            raise ValueError(f"Missing response field {column}")
                        missing = frame[column].isna()
                        supported = variable in EXTRA_VARIABLES[model]
                        row = {"month": str(start.date())[:7], "turbine_id": turbine, "model": model,
                            "variable": variable, "offset_days": offset, "native_field_expected": supported,
                            "expected_hours": len(expected), "non_null": int((~missing).sum()), "missing": int(missing.sum())}
                        missingness.append(row)
                        if supported and missing.any():
                            groups = missing.ne(missing.shift(fill_value=False)).cumsum()
                            for _, part in frame.loc[missing].groupby(groups[missing]):
                                gaps.append({**row, "gap_start": iso(part.valid_time.min()), "gap_last_hour": iso(part.valid_time.max()), "gap_hours": len(part)})
        records.append({"cache_file": str(path.relative_to(ROOT)), "request": params, "url": record["url"],
            "retrieved_at": record["retrieved_at"], "content_hash": record["content_hash"], "cache_sha256": sha256_file(path)})
        print("archive", start.date(), len(expected) * len(payloads), "location-hours", flush=True)
    archive = pd.concat(frames, ignore_index=True)
    if archive.duplicated(["turbine_id", "valid_time"]).any():
        raise ValueError("Duplicate monthly archive keys")
    audit = pd.DataFrame(missingness)
    write_csv(out / "archive_missingness.csv", audit)
    write_csv(out / "archive_missing_ranges.csv", pd.DataFrame(gaps, columns=[*audit.columns, "gap_start", "gap_last_hour", "gap_hours"]))
    write_csv(out / "additional_weather.csv", archive)
    write_json(out / "archive_provenance.json", {"requests": records, "sources": SOURCES,
        "month_windows_are_utc": True, "no_day_zero_or_reanalysis": True, "publication_provenance_verified": False,
        "native_time_steps": {"ecmwf_aifs025_single": "6-hourly, API interpolates hourly", "cmc_gem_gdps": "3-hourly, API interpolates hourly"},
        "warning": "Availability is conditional on documented offset semantics and assumed eight-hour delay; exact historical run/publication timestamps are absent."})
    return archive, audit


def select_additional(weather, archive):
    """Select eligible offsets before trajectory features; preserve every key."""
    keys = ["turbine_id", "valid_time"]
    columns = [f"{v}_previous_day{d}_{m}" for m, vs in EXTRA_VARIABLES.items() for v in vs for d in [2, 3]]
    frame = weather.merge(archive[keys + columns], on=keys, how="left", validate="many_to_one")
    use_day2 = frame.valid_time - pd.Timedelta(hours=48) + PUBLICATION_DELAY <= frame.issued_at
    frame["extra_offset_hours"] = np.where(use_day2, 48, 72)
    frame["extra_available_bound"] = frame.valid_time - pd.to_timedelta(frame.extra_offset_hours, unit="h") + PUBLICATION_DELAY
    if (frame.extra_available_bound > frame.issued_at).any():
        raise ValueError("No eligible additional-weather offset at this issuance")
    for model, variables in EXTRA_VARIABLES.items():
        for variable in variables:
            a, b = [f"{variable}_previous_day{d}_{model}" for d in [2, 3]]
            frame[f"extra_{variable}_{model}"] = np.where(use_day2, frame[a], frame[b])
    return frame.drop(columns=[c for c in frame if "_previous_day" in c])


def matrix(frame, method):
    """Explicit forecast/eligible-telemetry whitelist; actual labels are ignored."""
    base_method = "fresh_cat_live" if method == "add_cat_live" else ("fresh_wind_cat" if method == "add_wind_cat" else "fresh_cat_time")
    x = base_matrix(frame, base_method)
    if method == "no_extra_cat":
        return x
    x["extra_offset_hours"] = frame.extra_offset_hours
    speed_columns = []
    for model, variables in EXTRA_VARIABLES.items():
        for variable in variables:
            column = f"extra_{variable}_{model}"
            if "direction" in variable:
                x[column + "_sin"] = np.sin(np.deg2rad(frame[column]))
                x[column + "_cos"] = np.cos(np.deg2rad(frame[column]))
            else:
                x[column] = frame[column]
            if "wind_speed" in variable:
                speed_columns.append(column)
    temp = frame.sort_values(["turbine_id", "issued_at", "valid_time"])
    groups = temp.groupby(["turbine_id", "issued_at"])
    extra = {}
    for column in speed_columns:
        for shift in [-6, -3, 3, 6]:
            extra[f"{column}_shift{shift}"] = groups[column].shift(shift).fillna(temp[column]).reindex(frame.index)
        extra[column + "_day_mean"] = groups[column].transform("mean").reindex(frame.index)
        extra[column + "_day_std"] = groups[column].transform("std").reindex(frame.index)
    extra["aifs_ifs_100m_difference"] = frame.extra_wind_speed_100m_ecmwf_aifs025_single - frame.wind_speed_100m
    extra["gem_120m_minus_80m"] = frame.extra_wind_speed_120m_cmc_gem_gdps - frame.extra_wind_speed_80m_cmc_gem_gdps
    return pd.concat([x, pd.DataFrame(extra, index=frame.index)], axis=1)


def fit_cutoffs(test, month_start):
    return test.issued_at.where(test.issued_at < month_start, month_start)


def predict_all(train, obs, test):
    base = curves(obs)
    predictions = {"curve": np.clip(curve_predict(base, test), 0, 1)}
    audits = []
    for method in METHODS[1:]:
        if method == "blend_add_cat":
            predictions[method] = .5 * predictions["curve"] + .5 * predictions["add_cat"]
            continue
        recipe = RECIPES[method]
        if method == "add_hist":
            model = HistGradientBoostingRegressor(loss="absolute_error", max_iter=250, max_leaf_nodes=15,
                min_samples_leaf=40, l2_regularization=10, early_stopping=False, learning_rate=.05, random_state=42)
        else:
            model = CatBoostRegressor(iterations=recipe["iterations"], depth=recipe["depth"], learning_rate=.03,
                l2_leaf_reg=10, loss_function=recipe["loss"], random_seed=42, thread_count=2, verbose=False, allow_writing_files=False)
        x, y = matrix(train, method), matrix(test, method)
        usable = x.columns[~x.isna().all()]
        pipeline = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), model)
        with threadpool_limits(limits=2):
            pipeline.fit(x[usable], train.wind_speed if method == "add_wind_cat" else train.power_normalized)
            prediction = pipeline.predict(y[usable])
        if method == "add_wind_cat":
            prediction = curve_predict(base, test, np.maximum(0, prediction))
        if method == "add_cat_live":
            stale = test.telemetry_age.gt(5).to_numpy()
            prediction[stale] = predictions["curve"][stale]
        if not np.isfinite(prediction).all():
            raise ValueError("Nonfinite learned prediction")
        predictions[method] = np.clip(prediction, 0, 1)
        audits.append({"method": method, "feature_count": len(usable), "dropped_all_null_training_features": list(x.columns[x.isna().all()]),
            "train_rows": len(train), "target": recipe["target"]})
        print("fitted", method, "train", len(train), "targets", len(test), flush=True)
    return predictions, audits


def evaluate(hourly, weather, start, end):
    cfg = config()
    month_start = local_date(start, cfg)
    test = target_window(weather, hourly, month_start, local_date(end, cfg))
    test["fit_cutoff"] = fit_cutoffs(test, month_start)
    rows, fits = [], []
    for cutoff, part in test.groupby("fit_cutoff"):
        train, obs = causal_training(hourly, weather, cutoff)
        train, part = train.reset_index(drop=True), part.reset_index(drop=True)
        if train.empty or (train.valid_time + pd.Timedelta(hours=3) > cutoff).any():
            raise ValueError("Unmatured or empty training labels")
        if start == "2026-01-01" and (train.valid_time >= local_date("2026-01-01", cfg)).any():
            raise ValueError("January fitting labels forbidden")
        if (cutoff > part.issued_at).any():
            raise ValueError("Fit cutoff exceeds issuance")
        print("fold", start, "cutoff", iso(cutoff), flush=True)
        predictions, model_audits = predict_all(train, obs, part)
        fits.append({"fold": start[:7], "fit_cutoff": iso(cutoff), "train_rows": len(train), "observed_rows": len(obs),
            "max_training_valid_time": iso(train.valid_time.max()), "max_training_available_time": iso(train.valid_time.max() + pd.Timedelta(hours=3)),
            "target_rows": len(part), "first_target": iso(part.valid_time.min()), "last_target": iso(part.valid_time.max()), "models": model_audits})
        keep = ["issued_at", "valid_time", "turbine_id", "horizon_hours", "actual", "fit_cutoff", "provider_offset_hours", "provider_available_bound", "extra_offset_hours", "extra_available_bound"]
        for method, prediction in predictions.items():
            result = part[keep].copy()
            result["prediction"], result["error"], result["model"], result["fold"] = prediction, prediction - part.actual, method, start[:7]
            rows.append(result)
    result = pd.concat(rows, ignore_index=True)
    if result.duplicated(["model", "turbine_id", "valid_time"]).any():
        raise ValueError("Duplicate day-ahead target keys")
    return result, fits


def existing_inputs(cfg):
    """Read only; do not invoke preparation that could write shared artifacts."""
    meta = json.loads((ROOT / "data/preparation.json").read_text(encoding="utf-8-sig"))
    if meta["config_hash"] != digest(cfg):
        raise ValueError("Shared hourly cache configuration mismatch; preparation is outside this module's scope")
    h = pd.read_csv(ROOT / "data/hourly.csv")
    h["valid_time"] = pd.to_datetime(h.valid_time, utc=True)
    w = weather_table()
    archives = {}
    for offset, directory in [(2, "outputs/provider-weather-day2"), (3, "outputs/provider-weather")]:
        frame = pd.read_csv(ROOT / directory / "weather.csv")
        frame["valid_time"] = pd.to_datetime(frame.valid_time, utc=True)
        archives[offset] = frame
    return h, w, archives


def protocol():
    return {"created_at": pd.Timestamp.now(tz="UTC").isoformat(), "methods": METHODS, "recipes": RECIPES,
        "models_and_native_variables": EXTRA_VARIABLES, "acquisition_months_utc": ["2025-06", "2026-01"],
        "baseline": "Empirical measured-wind power curve fitted only from matured pre-cutoff observations, applied to existing IFS 100m forecasts",
        "existing_forecast_features": "IFS plus eligible GFS/ICON/JMA from immutable baseline readers",
        "matched_control": "no_extra_cat vs add_cat holds iterations/depth/loss/calendar/training/timing fixed; only additional weather features differ",
        "shared_hyperparameters": {"seed": 42, "cat_learning_rate": .03, "cat_l2_leaf_reg": 10, "hist_learning_rate": .05, "hist_min_samples_leaf": 40, "hist_l2": 10, "hist_early_stopping": False},
        "selection": "December 1-29 local target days only; score and freeze winner before January evaluation",
        "training": "June onward forecast pairs; each month uses pre-month matured labels plus an earlier fit for its preceding-day boundary issuance",
        "january": "Reused diagnostic only; no January target labels fit models or select recipes",
        "telemetry_and_label_delay_hours_after_interval_end": 2, "forecast_publication_delay_hours_assumed": 8,
        "conditional_weather_rule": "valid_time - offset_hours + 8h <= issue; prefer 48h only when eligible, otherwise 72h",
        "live_features": "Earlier January measurements can enter later forecast features after interval-end+2h; no future actual wind",
        "full_scope": "Both sites, complete January1-31, 1488 pairs per method; Jan31 48 pairs",
        "target_mae_max": .05, "max_cpu_threads": 2, "all_null_features": "Drop only when all-null in training; median imputation fitted on training only",
        "publication_provenance_verified": False, "production_changed": False, "sources": SOURCES,
        "source_sha256": sha256_file(__file__)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/ai-weather-benchmark")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    out = Path(args.output)
    if not out.is_absolute():
        out = ROOT / out
    if out.parent != ROOT / "outputs" or not out.name.startswith("ai-weather-benchmark"):
        raise ValueError("Output must be outputs/ai-weather-benchmark* within this project")
    out.mkdir(parents=True, exist_ok=False)
    # Fixed protocol is persisted before acquisition, fitting or label scoring.
    write_json(out / "protocol.json", protocol())
    archive, raw_audit = acquisition(out, offline=args.offline)
    cfg = config()
    hourly, original_weather, base_archives = existing_inputs(cfg)
    eligible = eligible_features(original_weather[original_weather.horizon_hours > 24], base_archives)
    eligible = select_additional(eligible, archive)
    eligible = eligible[(eligible.valid_time >= local_date("2025-06-01", cfg)) & (eligible.valid_time < local_date("2026-02-01", cfg))].copy()
    state = telemetry(hourly, original_weather, eligible.issued_at.unique(), delay=2)
    eligible = eligible.merge(state, on=["issued_at", "turbine_id"], validate="many_to_one")
    support = []
    for (month, turbine), part in eligible.groupby([eligible.valid_time.dt.tz_convert(cfg["calendar_timezone"]).dt.strftime("%Y-%m"), "turbine_id"]):
        for column in [c for c in eligible if c.startswith("extra_") and c not in ["extra_available_bound", "extra_offset_hours"]]:
            support.append({"local_month": month, "turbine_id": turbine, "feature": column, "n_forecast_rows": len(part), "missing": int(part[column].isna().sum())})
    write_csv(out / "selected_feature_missingness.csv", pd.DataFrame(support))
    dec, fits_dec = evaluate(hourly, eligible, "2025-12-01", "2026-01-01")
    selected_days = dec[dec.valid_time < local_date("2025-12-30", cfg)]
    selection = scores(selected_days)
    selected = next(iter(selection))
    write_json(out / "selection.json", {"created_at": pd.Timestamp.now(tz="UTC").isoformat(), "selected": selected,
        "december1_29_mae": selection, "max_selection_target_available_at": iso(selected_days.valid_time.max() + pd.Timedelta(hours=3)),
        "first_january_issue": iso(local_date("2025-12-31", cfg))})
    if selected_days.valid_time.max() + pd.Timedelta(hours=3) > local_date("2025-12-31", cfg):
        raise ValueError("Selection labels unavailable before first January issuance")
    write_csv(out / "december_predictions.csv", dec)
    jan, fits_jan = evaluate(hourly, eligible, "2026-01-01", "2026-02-01")
    if not jan.groupby("model").size().eq(1488).all():
        raise ValueError("Full January requires 1488 pairs per method")
    day = jan[jan.valid_time >= local_date("2026-01-31", cfg)]
    if not day.groupby("model").size().eq(48).all():
        raise ValueError("January31 requires 48 pairs per method")
    january, january31 = scores(jan), scores(day)
    combined = pd.concat([dec, jan], ignore_index=True)
    write_csv(out / "predictions.csv", combined)
    write_csv(out / "metrics.csv", summarize(combined))
    write_json(out / "fit_audit.json", {"fits": fits_dec + fits_jan})
    report = {"selected": selected, "december1_29_mae": selection, "december_full_mae": scores(dec), "january_full_mae": january,
        "january2_31_mae": scores(jan[jan.valid_time >= local_date("2026-01-02", cfg)]), "january31_mae": january31,
        "n_january_per_model": 1488, "n_january31_per_model": 48, "numerical_target_met_by": [m for m in METHODS if january[m] <= .05 and january31[m] <= .05],
        "source_note": "AIFS/GEM raw coverage audited in every requested UTC month; known absent baseline August dates persist in training only",
        "native_archive_missing_values": int(raw_audit.loc[raw_audit.native_field_expected, "missing"].sum()),
        "production_changed": False, "publication_provenance_verified": False, "january_is_reused_diagnostic": True}
    write_json(out / "report.json", report)
    comparison = [{"model": m, "selected": m == selected, "december1_29_mae": selection[m], "december_full_mae": report["december_full_mae"][m],
        "january_full_mae": january[m], "january31_mae": january31[m], "january_gain_vs_curve": january["curve"] - january[m],
        "january31_gain_vs_curve": january31["curve"] - january31[m], "both_mae_le_005": january[m] <= .05 and january31[m] <= .05} for m in METHODS]
    write_csv(out / "comparisons.csv", pd.DataFrame(comparison))
    dependencies = ["config.json", "src/fresh_provider_benchmark.py", "src/accuracy.py", "src/live_bias.py", "src/weather_calibration.py", "src/model.py", "src/common.py", "src/provider_weather.py",
        "data/hourly.csv", "data/preparation.json", "outputs/accuracy-round2-extension-v2/weather.csv", "outputs/accuracy/weather.csv", "outputs/provider-weather-day2/weather.csv", "outputs/provider-weather/weather.csv"]
    write_json(out / "hashes.json", {"source_sha256": sha256_file(__file__), "input_sha256": {p: sha256_file(ROOT / p) for p in dependencies},
        "output_sha256": {p.name: sha256_file(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "hashes.json"}})
    compact = ROOT / "examples/ai-weather-benchmark"
    export_compact = not compact.exists()
    if export_compact:
        compact.mkdir(parents=True)
    for name in ["protocol.json", "selection.json", "report.json", "metrics.csv", "comparisons.csv", "fit_audit.json", "hashes.json", "archive_missingness.csv", "archive_missing_ranges.csv", "selected_feature_missingness.csv", "archive_provenance.json"]:
        if export_compact:
            shutil.copyfile(out / name, compact / name)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
