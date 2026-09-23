"""Portable December-selected AIFS/GEM wind model with an explicit short head.

Serving imports no research modules or scikit-learn. The learned head sees only
horizons 25--48, including when a full 48-hour frame is supplied, so trajectory
features have exactly the same window as the recorded research experiment.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from catboost import CatBoostError, CatBoostRegressor

from .common import ROOT, config, digest, iso, local_date, utc, write_json

MODELS = ["gfs_global", "icon_global", "jma_gsm"]
VARIABLES = ["wind_speed_10m", "wind_speed_100m", "wind_direction_10m", "temperature_2m", "surface_pressure", "relative_humidity_2m", "cloud_cover", "precipitation"]
COMMON = ["temperature_2m", "surface_pressure", "relative_humidity_2m", "cloud_cover", "precipitation"]
EXTRA_VARIABLES = {
    "ecmwf_aifs025_single": ["wind_speed_10m", "wind_speed_100m", "wind_direction_10m", "wind_direction_100m", *COMMON],
    "cmc_gem_gdps": ["wind_speed_10m", "wind_speed_80m", "wind_speed_120m", "wind_direction_10m", "wind_direction_80m", "wind_direction_120m", *COMMON],
}
POLICY = {"horizons_1_24": "empirical_curve", "horizons_25_48": "add_wind_cat",
          "update_issuances": "same fixed recipe; 12-hour updates are operational replay, not separately accuracy-validated",
          "publication_provenance_verified": False,
          "forecast_publication_delay_hours_assumed": 8,
          "reporting_delay_after_interval_end_hours": 2}


def sha256(path):
    path = Path(path)
    content = path.read_bytes()
    # Git may normalize text to LF when cloning a Windows export elsewhere.
    # Hash the same text bytes on either platform; native model bytes stay exact.
    if path.suffix.lower() in {".json", ".py", ".md", ".csv"}:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def matrix(frame):
    """Exact numeric whitelist/order of research matrix(add_wind_cat)."""
    x = frame[["turbine_id", "wind_speed_10m", "wind_speed_100m", "temperature_2m", "horizon_hours"]].copy()
    angle = np.deg2rad(frame.wind_direction_100m)
    x["direction_sin"], x["direction_cos"] = np.sin(angle), np.cos(angle)
    x["weather_lead_hours"] = (frame.valid_time - frame.weather_run_time).dt.total_seconds() / 3600
    x["wind_shear"] = frame.wind_speed_100m - frame.wind_speed_10m
    x["wind_u"], x["wind_v"] = frame.wind_speed_100m * x.direction_cos, frame.wind_speed_100m * x.direction_sin
    temp = frame.sort_values(["turbine_id", "issued_at", "valid_time"])
    groups = temp.groupby(["turbine_id", "issued_at"])
    for shift in [-3, -1, 1, 3]:
        x[f"wind_shift_{shift}"] = groups.wind_speed_100m.shift(shift).fillna(temp.wind_speed_100m).reindex(frame.index)
    x["forecast_ramp"] = x["wind_shift_-3"] - x["wind_shift_3"]
    x["turbine_id"] = frame.turbine_id.map({"turbine_1": 0., "turbine_2": 1.})
    columns = ["provider_offset_hours"] + [f"provider_{v}_{m}" for m in MODELS for v in VARIABLES]
    for column in columns:
        if "direction" in column:
            x[column + "_sin"], x[column + "_cos"] = np.sin(np.deg2rad(frame[column])), np.cos(np.deg2rad(frame[column]))
        else:
            x[column] = frame[column]
    for column in [c for c in columns if "wind_speed" in c]:
        for shift in [-6, -3, 3, 6]:
            x[f"{column}_shift{shift}"] = groups[column].shift(shift).fillna(temp[column]).reindex(frame.index)
        x[column + "_day_mean"] = groups[column].transform("mean").reindex(frame.index)
        x[column + "_day_std"] = groups[column].transform("std").reindex(frame.index)
    x = x.copy()
    x["extra_offset_hours"] = frame.extra_offset_hours
    speed_columns = []
    for model, variables in EXTRA_VARIABLES.items():
        for variable in variables:
            column = f"extra_{variable}_{model}"
            if "direction" in variable:
                x[column + "_sin"], x[column + "_cos"] = np.sin(np.deg2rad(frame[column])), np.cos(np.deg2rad(frame[column]))
            else:
                x[column] = frame[column]
            if "wind_speed" in variable:
                speed_columns.append(column)
    extra = {}
    for column in speed_columns:
        for shift in [-6, -3, 3, 6]:
            extra[f"{column}_shift{shift}"] = groups[column].shift(shift).fillna(temp[column]).reindex(frame.index)
        extra[column + "_day_mean"] = groups[column].transform("mean").reindex(frame.index)
        extra[column + "_day_std"] = groups[column].transform("std").reindex(frame.index)
    extra["aifs_ifs_100m_difference"] = frame.extra_wind_speed_100m_ecmwf_aifs025_single - frame.wind_speed_100m
    extra["gem_120m_minus_80m"] = frame.extra_wind_speed_120m_cmc_gem_gdps - frame.extra_wind_speed_80m_cmc_gem_gdps
    return pd.concat([x, pd.DataFrame(extra, index=frame.index)], axis=1)


def transform(frame, metadata):
    x = matrix(frame).loc[:, metadata["features"]].to_numpy(dtype=float)
    if np.isinf(x).any():
        raise ValueError("Infinite candidate weather input")
    missing = np.isnan(x)
    imputed = np.where(missing, np.asarray(metadata["medians"]), x)
    return np.column_stack([imputed, missing[:, metadata["indicator_indices"]].astype(float)])


def _curves(observed):
    result = {}
    for turbine, part in observed.groupby("turbine_id"):
        curve = part.groupby((part.wind_speed * 2).round() / 2).power_normalized.agg(["mean", "count"])
        curve = curve.loc[curve["count"] >= 6]
        if len(curve) < 2:
            raise ValueError("Insufficient candidate curve observations")
        result[turbine] = [curve.index.to_list(), curve["mean"].to_list()]
    return result


def curve_predict(frame, curves, wind=None):
    speeds = frame.wind_speed_100m.to_numpy() if wind is None else np.asarray(wind)
    return np.array([np.interp(speed, *curves[t]) for t, speed in zip(frame.turbine_id, speeds)])


def train(hourly, weather, cutoff, cfg):
    """Fit the fixed recipe from mature labels; callers supply eligible archives."""
    cutoff = utc(cutoff)
    obs = hourly.loc[hourly.complete & (hourly.valid_time + pd.Timedelta(hours=3) <= cutoff)].copy()
    part = weather.loc[(weather.horizon_hours > 24) & (weather.issued_at < cutoff) &
                       (weather.valid_time + pd.Timedelta(hours=3) <= cutoff)].copy()
    for col in ["weather_available_at", "provider_available_bound", "extra_available_bound"]:
        if (part[col] > part.issued_at).any():
            raise ValueError("Candidate training weather unavailable at issue")
    train_frame = part.merge(obs[["valid_time", "turbine_id", "power_normalized", "wind_speed"]],
                            on=["valid_time", "turbine_id"], validate="many_to_one").reset_index(drop=True)
    if len(train_frame) < 100 or not np.isfinite(train_frame.wind_speed).all():
        raise ValueError("Insufficient/invalid candidate wind training labels")
    x = matrix(train_frame)
    usable = x.columns[~x.isna().all()]
    values = x[usable].to_numpy(dtype=float)
    metadata = {"schema_version": 1, "selected": "aifs_gem", "research_method": "add_wind_cat",
        "trained_until": iso(cutoff), "config_hash": digest(cfg), "policy": POLICY,
        "features": list(usable), "medians": np.nanmedian(values, axis=0).tolist(),
        "indicator_indices": np.flatnonzero(np.isnan(values).any(axis=0)).tolist(),
        "dropped_all_null_features": list(x.columns[x.isna().all()]),
        "curves": _curves(obs), "training_rows": len(train_frame), "observed_rows": len(obs),
        "last_training_target": iso(train_frame.valid_time.max()),
        "last_training_target_available_at": iso(train_frame.valid_time.max() + pd.Timedelta(hours=3)),
        "recipe": {"iterations": 300, "depth": 4, "loss_function": "RMSE", "learning_rate": .03,
                   "l2_leaf_reg": 10, "random_seed": 42, "calendar_features": False}}
    model = CatBoostRegressor(**{k: v for k, v in metadata["recipe"].items() if k != "calendar_features"},
                             thread_count=2, verbose=False, allow_writing_files=False)
    model.fit(transform(train_frame, metadata), train_frame.wind_speed)
    return {**metadata, "metadata": metadata, "model": model}


def export_bundle(bundle, directory, name):
    """Export native CatBoost bytes and checksummed explicit preprocessing JSON."""
    directory = Path(directory)
    if Path(name).name != name or not name:
        raise ValueError("Invalid candidate bundle name")
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / (name + ".cbm")
    bundle["model"].save_model(str(model_path), format="cbm")
    metadata = {**bundle["metadata"], "model_file": model_path.name, "model_sha256": sha256(model_path)}
    metadata["version"] = "aifs-gem-" + digest(metadata)[:16]
    path = directory / (name + ".json")
    write_json(path, metadata)
    return {"file": path.name, "sha256": sha256(path), "trained_until": metadata["trained_until"], "version": metadata["version"]}


def _local_file(directory, name):
    if not isinstance(name, str) or Path(name).name != name or not name:
        raise ValueError("Invalid portable candidate file path")
    return directory / name


def load_bundle(cfg, issue, directory=None):
    directory = Path(directory) if directory is not None else ROOT / "examples/candidate"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["schema_version"] != 1 or manifest["config_hash"] != digest(cfg):
        raise ValueError("Candidate manifest/configuration mismatch")
    eligible = [r for r in manifest["bundles"] if utc(r["trained_until"]) <= utc(issue)]
    if not eligible:
        raise ValueError("No candidate model trained before this issuance")
    record = max(eligible, key=lambda r: utc(r["trained_until"]))
    path = _local_file(directory, record["file"])
    if sha256(path) != record["sha256"]:
        raise ValueError("Candidate metadata checksum mismatch")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if metadata["config_hash"] != digest(cfg) or metadata["trained_until"] != record["trained_until"] or metadata["version"] != record["version"]:
        raise ValueError("Candidate bundle identity mismatch")
    if metadata["selected"] != "aifs_gem" or utc(metadata["last_training_target_available_at"]) > utc(metadata["trained_until"]):
        raise ValueError("Invalid candidate method/training boundary")
    model_path = _local_file(directory, metadata["model_file"])
    if sha256(model_path) != metadata["model_sha256"]:
        raise ValueError("Candidate model checksum mismatch")
    model = CatBoostRegressor(thread_count=2)
    model.load_model(str(model_path), format="cbm")
    return {**metadata, "metadata": metadata, "model": model, "metrics": manifest.get("metrics", []),
            "selection": manifest.get("selection", {}), "manifest": manifest}


def predict(fullweather, bundle):
    """Full horizon hybrid, with learned trajectories restricted to 25--48."""
    frame = fullweather.reset_index(drop=True)
    if frame.empty or frame.duplicated(["issued_at", "valid_time", "turbine_id"]).any():
        raise ValueError("Empty/duplicate candidate weather")
    if (frame.issued_at < utc(bundle["trained_until"])).any():
        raise ValueError("Candidate model trained after forecast issue")
    if not frame.turbine_id.isin(bundle["curves"]).all() or not frame.horizon_hours.between(1, 48).all():
        raise ValueError("Unknown candidate turbine or horizon")
    prediction = curve_predict(frame, bundle["curves"])
    mask = frame.horizon_hours > 24
    if mask.any():
        day = frame.loc[mask].copy()
        for _, group in day.groupby(["issued_at", "turbine_id"]):
            if sorted(group.horizon_hours.tolist()) != list(range(25, 49)):
                raise ValueError("Candidate learned head requires complete horizons 25--48")
        for col in ["weather_available_at", "provider_available_bound", "extra_available_bound"]:
            if (day[col] > day.issued_at).any():
                raise ValueError("Candidate weather unavailable at issue")
        try:
            wind = bundle["model"].predict(transform(day, bundle["metadata"]), thread_count=2)
        except CatBoostError as exc:
            raise RuntimeError("Candidate native model inference failed; empirical-curve recovery is available") from exc
        prediction[mask] = curve_predict(day, bundle["curves"], np.maximum(0, wind))
    if not np.isfinite(prediction).all():
        raise ValueError("Nonfinite candidate forecast")
    return np.clip(prediction, 0, 1)


def export_research(directory=None):
    """Offline training/export entry point; optional research imports stay here."""
    from .ai_weather_benchmark import existing_inputs, select_additional
    from .fresh_provider_benchmark import eligible_features
    from .accuracy import target_window
    cfg = config()
    directory = Path(directory) if directory is not None else ROOT / "examples/candidate"
    hourly, original, archives = existing_inputs(cfg)
    extra = pd.read_csv(ROOT / "outputs/ai-weather-benchmark/additional_weather.csv")
    extra.valid_time = pd.to_datetime(extra.valid_time, utc=True)
    weather = select_additional(eligible_features(original[original.horizon_hours > 24], archives), extra)
    weather = weather[(weather.valid_time >= local_date("2025-06-01", cfg)) & (weather.valid_time < local_date("2026-02-01", cfg))].copy()
    test = target_window(weather, hourly, local_date("2026-01-01", cfg), local_date("2026-02-01", cfg))
    records, predictions = [], []
    for name, cutoff in [("boundary", local_date("2025-12-31", cfg)), ("january", local_date("2026-01-01", cfg))]:
        bundle = train(hourly, weather, cutoff, cfg)
        records.append(export_bundle(bundle, directory, name))
    selection_path = ROOT / "examples/ai-weather-benchmark/selection.json"
    metric_frame = pd.read_csv(ROOT / "examples/ai-weather-benchmark/metrics.csv")
    metric_frame = metric_frame.loc[metric_frame.model.eq("add_wind_cat") & metric_frame.fold.eq("2026-01") &
                                    metric_frame.turbine.isin(cfg["turbines"]) & metric_frame.bucket.eq("25-48")].copy()
    metric_frame = metric_frame.rename(columns={"turbine": "turbine_id", "bucket": "horizon_bucket", "n": "n_samples"})
    metric_frame["model"] = "aifs_gem"
    metric_frame["period_start"] = iso(local_date("2026-01-01", cfg))
    metric_frame["period_end"] = iso(local_date("2026-02-01", cfg))
    metric_frame = metric_frame[["model", "turbine_id", "horizon_bucket", "mae", "rmse", "n_samples", "period_start", "period_end"]]
    manifest = {"schema_version": 1, "config_hash": digest(cfg), "bundles": records, "policy": POLICY,
        "checksum_policy": "SHA-256 with CRLF normalized to LF for text; native CBM bytes unchanged",
        "selection": json.loads(selection_path.read_text(encoding="utf-8")), "selection_sha256": sha256(selection_path),
        "research_protocol_sha256": sha256(ROOT / "examples/ai-weather-benchmark/protocol.json"),
        "metrics": metric_frame.to_dict("records"),
        "metrics_scope": "January research evaluation for hours 25–48 only; the full 48-hour hybrid and 12-hour revisions are not independently evaluated.",
        "january_is_reused_diagnostic": True, "target_005_met": False}
    write_json(directory / "manifest.json", manifest)
    for issue, part in test.groupby("issued_at", sort=False):
        bundle = load_bundle(cfg, issue, directory)
        result = part[["issued_at", "valid_time", "turbine_id", "actual"]].copy()
        result["prediction"] = predict(part, bundle)
        predictions.append(result)
    actual = pd.concat(predictions, ignore_index=True)
    reference = pd.read_csv(ROOT / "outputs/ai-weather-benchmark/predictions.csv")
    reference = reference.loc[reference.model.eq("add_wind_cat") & reference.fold.eq("2026-01")]
    for col in ["issued_at", "valid_time"]:
        reference[col] = pd.to_datetime(reference[col], utc=True)
    paired = actual.merge(reference[["issued_at", "valid_time", "turbine_id", "prediction"]],
                          on=["issued_at", "valid_time", "turbine_id"], suffixes=("", "_research"), validate="one_to_one")
    delta = float((paired.prediction - paired.prediction_research).abs().max())
    if len(paired) != 1488 or delta > 1e-12:
        raise ValueError(f"Portable candidate changed research predictions: {len(paired)} rows, max delta {delta}")
    day = actual.valid_time >= local_date("2026-01-31", cfg)
    # A portable golden slice also checks the serving weather adapter in a fresh
    # checkout, where ignored full research archives need not exist.
    demo = reference.loc[reference.issued_at.eq(utc("2026-01-09T19:00:00Z")),
                         ["turbine_id", "valid_time", "prediction"]].sort_values(["turbine_id", "valid_time"])
    from .candidate_weather import CandidateWeather
    demo_weather = CandidateWeather(cfg, offline=True).horizon(utc("2026-01-09T19:00:00Z"))
    demo_weather["prediction"] = predict(demo_weather, load_bundle(cfg, utc("2026-01-09T19:00:00Z"), directory))
    runtime = demo_weather.loc[demo_weather.horizon_hours > 24].sort_values(["turbine_id", "valid_time"])
    demo_delta = float(np.max(np.abs(runtime.prediction.to_numpy() - demo.prediction.to_numpy())))
    if len(demo) != 48 or demo_delta > 1e-12:
        raise ValueError(f"Serving weather adapter changed demo day-ahead predictions: {demo_delta}")
    demo["valid_time"] = demo.valid_time.map(iso)
    write_json(directory / "demo_reference.json", {"issued_at": "2026-01-09T19:00:00Z", "horizons": "25-48",
        "source": "Recorded add_wind_cat January research predictions", "max_runtime_difference": demo_delta,
        "predictions": demo.to_dict("records")})
    verification = {"pairs": len(paired), "max_abs_prediction_difference": delta,
        "january_mae": float((actual.prediction - actual.actual).abs().mean()),
        "january31_mae": float((actual.loc[day, "prediction"] - actual.loc[day, "actual"]).abs().mean()),
        "source_sha256": sha256(__file__), "no_january_fitting_labels": True,
        "artifact_sha256": {p.name: sha256(p) for p in directory.iterdir() if p.is_file() and p.name != "verification.json"}}
    write_json(directory / "verification.json", verification)
    return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export the fixed candidate from existing offline research inputs")
    parser.add_argument("--output", default=str(ROOT / "examples/candidate"))
    print(json.dumps(export_research(parser.parse_args().output), indent=2))
