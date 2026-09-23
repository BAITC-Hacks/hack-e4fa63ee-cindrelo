import pickle

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from .common import ROOT, digest, iso, utc, write_json


def features(weather):
    x = weather[["turbine_id", "wind_speed_10m", "wind_speed_100m", "temperature_2m", "horizon_hours"]].copy()
    angle = np.deg2rad(weather.wind_direction_100m)
    x["direction_sin"], x["direction_cos"] = np.sin(angle), np.cos(angle)
    hour = weather.valid_time.dt.hour
    x["hour_sin"], x["hour_cos"] = np.sin(hour * np.pi / 12), np.cos(hour * np.pi / 12)
    day = weather.valid_time.dt.dayofyear
    x["season_sin"], x["season_cos"] = np.sin(day * 2 * np.pi / 365.25), np.cos(day * 2 * np.pi / 365.25)
    x["weather_lead_hours"] = (weather.valid_time - weather.weather_run_time).dt.total_seconds() / 3600
    return x


def fit(hourly, weather, cutoff, cfg, name, selected="catboost"):
    cutoff = utc(cutoff)
    observed = hourly.loc[hourly.complete & (hourly.valid_time + pd.Timedelta(hours=1) <= cutoff)]
    if observed.empty:
        raise ValueError("No training observations before cutoff")
    train = weather.merge(observed[["valid_time", "turbine_id", "power_normalized"]], on=["valid_time", "turbine_id"], validate="many_to_one")
    train = train.loc[(train.valid_time + pd.Timedelta(hours=1) <= cutoff) & (train.issued_at < cutoff)]
    if len(train) < 100:
        raise ValueError("Insufficient archived weather/target pairs")
    if (train.weather_available_at > train.issued_at).any():
        raise ValueError("Training weather was unavailable at issuance")
    model = CatBoostRegressor(iterations=400, depth=6, learning_rate=0.05, loss_function="RMSE",
                             random_seed=42, thread_count=4, verbose=False, allow_writing_files=False)
    model.fit(features(train), train.power_normalized, cat_features=["turbine_id"])
    curves, means = {}, {}
    for turbine, part in observed.groupby("turbine_id"):
        curve = part.groupby((part.wind_speed * 2).round() / 2).power_normalized.agg(["mean", "count"])
        curve = curve.loc[curve["count"] >= 6]
        if len(curve) < 2:
            raise ValueError(f"Insufficient empirical curve coverage for {turbine}")
        curves[turbine] = (curve.index.to_numpy(), curve["mean"].to_numpy())
        means[turbine] = float(part.power_normalized.mean())
    identity = digest({"cfg": cfg, "cutoff": iso(cutoff), "selected": selected,
                       "training_hash": digest(train.astype(str).to_dict("list")),
                       "observed_hash": digest(observed.astype(str).to_dict("list")), "recipe": "catboost400-depth6-v1"})
    bundle = {"model": model, "curves": curves, "means": means, "trained_until": iso(cutoff),
              "selected": selected, "version": name + "-" + identity[:12], "config_hash": digest(cfg),
              "training_rows": len(train), "training_last_target": iso(train.valid_time.max())}
    path = ROOT / "artifacts" / f"{name}.pkl"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(pickle.dumps(bundle))
    write_json(path.with_suffix(".json"), {k: v for k, v in bundle.items() if k not in {"model", "curves", "means"}})
    return bundle


def predict(bundle, weather, method=None):
    if (weather.issued_at < utc(bundle["trained_until"])).any():
        raise ValueError("Model trained on observations after forecast issuance")
    method = method or bundle["selected"]
    if method == "catboost":
        prediction = bundle["model"].predict(features(weather))
    elif method == "mean":
        prediction = weather.turbine_id.map(bundle["means"]).to_numpy()
    elif method == "curve":
        prediction = np.array([np.interp(row.wind_speed_100m, *bundle["curves"][row.turbine_id]) for row in weather.itertuples()])
    else:
        raise ValueError(f"Unknown model {method}")
    if not np.isfinite(prediction).all():
        raise ValueError("Model produced nonfinite power")
    return np.clip(prediction, 0, 1)


def load_bundle(name, cfg):
    # Only load artifacts generated locally by this repository, never untrusted pickle files.
    bundle = pickle.loads((ROOT / "artifacts" / f"{name}.pkl").read_bytes())
    if bundle["config_hash"] != digest(cfg):
        raise ValueError("Model config mismatch; prepare and train again")
    return bundle


def bundle_for_issue(issue, cfg):
    for name in ["final", "validation", "tuning"]:
        if (ROOT / "artifacts" / f"{name}.pkl").exists():
            bundle = load_bundle(name, cfg)
            if utc(bundle["trained_until"]) <= utc(issue):
                return bundle
    raise ValueError("No model trained strictly before this forecast origin; run train")
