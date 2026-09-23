"""Research: nearby wind/pressure fields with a causal full-month benchmark."""
import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from threadpoolctl import threadpool_limits

from .accuracy import curves, curve_predict, target_window, summarize, uncertainty
from .common import config, digest, local_date, write_csv, write_json
from .data import load_hourly
from .fresh_provider_benchmark import eligible_features, matrix as site_matrix, causal_training, scores
from .weather_calibration import weather_table

MODELS = ["gfs_global", "icon_global"]
VARIABLES = ["wind_speed_10m", "wind_speed_100m", "wind_direction_100m", "pressure_msl", "temperature_2m"]
POINTS = {f"r{i+1}c{j+1}": (round(43.644174 + .25*i, 6), round(78.537216 + .25*j, 6))
          for i in [-1, 0, 1] for j in [-1, 0, 1]}
METHODS = ["curve", "site_wind", "spatial_wind", "gradient_wind", "spatial_direct", "spatial_hist", "blend_spatial_direct"]


def acquire(output):
    """Immutable monthly multi-location requests; cache keys include coordinates."""
    out, cache = Path(output), Path("data/spatial-weather")
    cache.mkdir(parents=True, exist_ok=True)
    frames, provenance = [], []
    for month in pd.date_range("2025-06-01", "2026-01-01", freq="MS"):
        params = {"latitude": ",".join(str(p[0]) for p in POINTS.values()),
            "longitude": ",".join(str(p[1]) for p in POINTS.values()), "start_date": str(month.date()),
            "end_date": str((month + pd.offsets.MonthEnd()).date()), "models": ",".join(MODELS),
            "hourly": ",".join(f"{v}_previous_day{d}" for v in VARIABLES for d in [2, 3]),
            "wind_speed_unit": "ms", "timezone": "UTC"}
        path = cache / (digest(params) + ".json")
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if digest(record["response"]) != record["content_hash"]:
                raise ValueError("Spatial weather cache checksum mismatch")
        else:
            url = "https://previous-runs-api.open-meteo.com/v1/forecast?" + urlencode(params)
            for attempt in range(3):
                try:
                    with urlopen(url, timeout=60) as response:
                        payload = json.load(response)
                    break
                except (HTTPError, URLError, TimeoutError) as exc:
                    print("Archive retry", month.date(), attempt+1, str(exc), flush=True)
                    if attempt == 2:
                        raise
                    time.sleep(2**attempt)
            record = {"request": params, "url": url, "retrieved_at": pd.Timestamp.now(tz="UTC").isoformat(),
                      "response": payload, "content_hash": digest(payload)}
            write_json(path, record)
        payload = record["response"]
        if len(payload) != len(POINTS):
            raise ValueError("Incorrect number of spatial locations")
        for (point, coords), item in zip(POINTS.items(), payload):
            part = pd.DataFrame(item["hourly"]).rename(columns={"time": "valid_time"})
            part["point"] = point
            frames.append(part)
            provenance.append({"month": str(month.date()), "point": point, "request_latitude": coords[0],
                "request_longitude": coords[1], "returned_latitude": item["latitude"],
                "returned_longitude": item["longitude"], "elevation": item.get("elevation"),
                "cache": str(path), "response_sha256": record["content_hash"]})
        print("Acquired spatial month", month.date(), flush=True)
    result = pd.concat(frames, ignore_index=True)
    result.valid_time = pd.to_datetime(result.valid_time, utc=True)
    if result.duplicated(["valid_time", "point"]).any():
        raise ValueError("Duplicate spatial input")
    write_csv(out / "weather.csv", result)
    write_csv(out / "locations.csv", pd.DataFrame(provenance))
    missing = result.drop(columns=["valid_time", "point"]).isna().sum().rename_axis("column").reset_index(name="missing")
    missing["rows"] = len(result)
    write_csv(out / "missingness.csv", missing)


def spatial_features(weather, archive):
    """Join only the older or conditionally available newer offset per target."""
    wide = archive.pivot(index="valid_time", columns="point")
    wide.columns = [f"{variable}__{point}" for variable, point in wide.columns]
    part = weather.merge(wide.reset_index(), on="valid_time", how="left", validate="many_to_one")
    offset = np.where(part.valid_time - pd.Timedelta(hours=40) <= part.issued_at, 2, 3)
    available = part.valid_time - pd.to_timedelta(offset*24-8, unit="h")
    if (available > part.issued_at).any():
        raise ValueError("No spatial offset available at issuance")
    chosen = {}
    for model in MODELS:
        for variable in VARIABLES:
            for point in POINTS:
                a, b = [f"{variable}_previous_day{d}_{model}__{point}" for d in [2, 3]]
                chosen[f"spatial_{variable}_{model}__{point}"] = np.where(offset == 2, part[a], part[b])
    part = part.drop(columns=[c for c in part if "_previous_day" in c])
    return pd.concat([part, pd.DataFrame(chosen, index=part.index)], axis=1)


def matrix(frame, method):
    # Control matches the preceding selected wind recipe, including no calendar.
    x = site_matrix(frame, "fresh_wind_cat" if method.endswith("wind") else "fresh_cat_time")
    if method == "site_wind":
        return x
    values = {}
    for model in MODELS:
        for variable in VARIABLES:
            cols = [f"spatial_{variable}_{model}__{p}" for p in POINTS]
            if "direction" in variable:
                for col in cols:
                    if method != "gradient_wind":
                        values[col + "_sin"] = np.sin(np.deg2rad(frame[col]))
                        values[col + "_cos"] = np.cos(np.deg2rad(frame[col]))
                continue
            stem = f"spatial_{variable}_{model}__"
            # Central finite differences, common requested-point distances.
            values[stem + "gradient_east"] = (frame[stem+"r1c2"]-frame[stem+"r1c0"]) / (111.32 * .5 * np.cos(np.deg2rad(43.644174)))
            values[stem + "gradient_north"] = (frame[stem+"r2c1"]-frame[stem+"r0c1"]) / (111.32 * .5)
            values[stem + "grid_mean"] = frame[cols].mean(axis=1)
            values[stem + "grid_std"] = frame[cols].std(axis=1)
            values[stem + "center"] = frame[stem+"r1c1"]
            if method != "gradient_wind":
                values.update({c: frame[c] for c in cols})
    return pd.concat([x, pd.DataFrame(values, index=frame.index)], axis=1)


def predict_all(train, obs, test):
    curve = curves(obs)
    result = {"curve": curve_predict(curve, test)}
    for method in METHODS[1:]:
        if method.startswith("blend_"):
            result[method] = .5 * result["curve"] + .5 * result[method.removeprefix("blend_")]
            continue
        if method == "spatial_hist":
            model = HistGradientBoostingRegressor(loss="absolute_error", max_iter=300, max_leaf_nodes=15,
                min_samples_leaf=40, l2_regularization=10, early_stopping=False, learning_rate=.05, random_state=42)
        else:
            model = CatBoostRegressor(iterations=350, depth=5, learning_rate=.03, l2_leaf_reg=10,
                loss_function="MAE", random_seed=42, thread_count=2, verbose=False, allow_writing_files=False)
        x, y = matrix(train, method), matrix(test, method)
        usable = x.columns[~x.isna().all()]
        pipeline = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), model)
        wind = method.endswith("wind")
        with threadpool_limits(limits=2):
            pipeline.fit(x[usable], train.wind_speed if wind else train.power_normalized)
            p = pipeline.predict(y[usable])
        if wind:
            p = curve_predict(curve, test, np.maximum(p, 0))
        result[method] = np.clip(p, 0, 1)
    return result


def evaluate(h, w, start, end):
    cutoff = local_date(start, config())
    test = target_window(w, h, cutoff, local_date(end, config()))
    test["fit_cutoff"] = test.issued_at.where(test.issued_at < cutoff, cutoff)
    results = []
    for fit_cutoff, part in test.groupby("fit_cutoff"):
        train, obs = causal_training(h, w, fit_cutoff)
        train, part = train.reset_index(drop=True), part.reset_index(drop=True)
        for method, p in predict_all(train, obs, part).items():
            result = part[["issued_at", "valid_time", "turbine_id", "horizon_hours", "actual", "fit_cutoff"]].copy()
            result["prediction"], result["error"] = p, p-part.actual
            result["model"], result["fold"] = method, start[:7]
            results.append(result)
        print(start, "fit", fit_cutoff, "train", len(train), "targets", len(part), flush=True)
    result = pd.concat(results, ignore_index=True)
    if result.duplicated(["model", "turbine_id", "valid_time"]).any() or not np.isfinite(result.prediction).all():
        raise ValueError("Invalid predictions")
    return result


def run(output, input_dir):
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    h, w = load_hourly(config()), weather_table()
    archives = {}
    for offset, path in [(2, "outputs/provider-weather-day2/weather.csv"), (3, "outputs/provider-weather/weather.csv")]:
        archives[offset] = pd.read_csv(path)
        archives[offset].valid_time = pd.to_datetime(archives[offset].valid_time, utc=True)
    archive = pd.read_csv(Path(input_dir)/"weather.csv")
    archive.valid_time = pd.to_datetime(archive.valid_time, utc=True)
    w = spatial_features(eligible_features(w[w.horizon_hours > 24], archives), archive)
    write_json(out/"protocol.json", {"methods": METHODS, "points": POINTS, "models": MODELS, "variables": VARIABLES,
        "selection": "December 1-29, all labels available before Jan1 boundary issuance",
        "telemetry_and_training_label_delay_hours": 2, "publication_delay_assumed_hours": 8,
        "january": "Reused diagnostic, never fitting or selection; full month and separate causal Jan1 fit",
        "spatial_design": "Fixed 3x3 grid at 0.25-degree steps around turbine midpoint, chosen without target scoring",
        "publication_provenance_verified": False, "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    dec = evaluate(h, w, "2025-12-01", "2026-01-01")
    selection = scores(dec[dec.valid_time < local_date("2025-12-30", config())])
    selected = next(iter(selection))
    write_json(out/"selection.json", {"selected": selected, "december1_29_mae": selection})
    write_csv(out/"december_predictions.csv", dec)
    jan = evaluate(h, w, "2026-01-01", "2026-02-01")
    if not jan.groupby("model").size().eq(1488).all():
        raise ValueError("Incomplete January")
    last = jan[jan.valid_time >= local_date("2026-01-31", config())]
    p = pd.concat([dec, jan], ignore_index=True)
    write_csv(out/"predictions.csv", p)
    write_csv(out/"metrics.csv", summarize(p))
    report = {"selected": selected, "december1_29_mae": selection, "december_full_mae": scores(dec),
        "january_full_mae": scores(jan), "january31_mae": scores(last), "january_pairs_per_method": 1488,
        "january31_pairs_per_method": len(last[last.model == "curve"]),
        "numerical_target_met_by": [m for m in METHODS if scores(jan)[m] <= .05 and scores(last)[m] <= .05],
        "production_changed": False, "publication_provenance_verified": False}
    write_json(out/"report.json", report)
    if selected != "curve":
        write_json(out/"uncertainty.json", uncertainty(jan, selected))
    print(report, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--acquire", action="store_true")
    parser.add_argument("--input-dir", default="outputs/spatial-weather")
    parser.add_argument("--output", default="outputs/spatial-weather-benchmark")
    args = parser.parse_args()
    if args.acquire:
        acquire(args.input_dir)
    else:
        run(args.output, args.input_dir)
