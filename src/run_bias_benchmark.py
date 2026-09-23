"""Research: recent measured wind bias against the same eligible ECMWF run.

Only already-reported observations estimate bias. The empirical curve remains
frozen before January, with a separate earlier fit for the January 1 origin.
"""
import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from .accuracy import curves, curve_predict, observed_before, target_window, summarize, uncertainty
from .common import config, local_date, write_csv, write_json
from .data import load_hourly
from .weather import Weather
from .weather_calibration import weather_table

RECIPES = {"curve": None, **{f"run_bias{window}_{strength}": (window, strength)
           for window in [12, 18] for strength in [.25, .5, 1.]}}
REPORTING_DELAY = pd.Timedelta(hours=2)


def same_run_state(hourly, weather, source, publication_delay_hours=8):
    """Use pre-issuance values from the actual run used for the target forecast.

    The run's past forecasts are not replaced by later runs. Cache access is
    offline; historical availability remains the documented delay assumption.
    """
    records = []
    for (issue, turbine), part in weather.groupby(["issued_at", "turbine_id"]):
        if part.weather_run_time.nunique() != 1:
            raise ValueError("One eligible run per issuance and turbine is required")
        run = part.weather_run_time.iloc[0]
        if run + pd.Timedelta(hours=publication_delay_hours) > issue or (part.weather_available_at > issue).any():
            raise ValueError("Weather was not available at issuance")
        envelope = source.fetch(turbine, run)
        if pd.Timestamp(envelope["run_time"]) != run:
            raise ValueError("Cache run does not match the issued forecast")
        payload = envelope["response"]
        if payload["hourly_units"]["wind_speed_100m"] != "m/s":
            raise ValueError("Expected forecast wind in m/s")
        forecast = pd.DataFrame(payload["hourly"])
        forecast.index = pd.to_datetime(forecast.time, utc=True)
        if forecast.index.duplicated().any():
            raise ValueError("Duplicate forecast timestamps")
        history = hourly[hourly.complete & hourly.turbine_id.eq(turbine) &
            (hourly.valid_time + pd.Timedelta(hours=3) <= issue) &
            (hourly.valid_time >= issue - pd.Timedelta(hours=20))].set_index("valid_time")
        if history.index.duplicated().any():
            raise ValueError("Duplicate observation timestamps")
        record = {"issued_at": issue, "turbine_id": turbine, "bias_run_time": run,
                  "bias_weather_content_hash": envelope["content_hash"]}
        for window in [12, 18]:
            selected = history[history.index >= issue - pd.Timedelta(hours=window+2)]
            paired = pd.DataFrame({"measured": selected.wind_speed,
                                   "forecast": forecast.wind_speed_100m.reindex(selected.index)})
            paired = paired.replace([np.inf, -np.inf], np.nan).dropna()
            age = float((issue - paired.index.max() - pd.Timedelta(hours=1)).total_seconds()/3600) if len(paired) else 999.
            usable = len(paired) >= 6 and age <= 5
            record[f"bias{window}"] = float((paired.measured-paired.forecast).median()) if usable else 0.
            record[f"pairs{window}"] = len(paired)
            record[f"age{window}"] = age
            record[f"fallback{window}"] = not usable
        records.append(record)
    return pd.DataFrame(records)


def predictions(curve, test):
    result = {"curve": curve_predict(curve, test)}
    for method, recipe in RECIPES.items():
        if recipe is None:
            continue
        window, strength = recipe
        correction = test[f"bias{window}"].clip(-4, 4).to_numpy() * strength
        correction *= np.exp(-(test.horizon_hours.to_numpy()-1)/48)
        adjusted = np.maximum(0, test.wind_speed_100m.to_numpy()+correction)
        result[method] = np.clip(curve_predict(curve, test, adjusted), 0, 1)
    return result


def evaluate(hourly, weather, source, start, end):
    month_start, month_end = local_date(start, config()), local_date(end, config())
    test = target_window(weather[weather.horizon_hours > 24], hourly, month_start, month_end)
    state = same_run_state(hourly, test, source, config()["weather_delay_hours"])
    test = test.merge(state, on=["issued_at", "turbine_id"], validate="many_to_one")
    test["fit_cutoff"] = test.issued_at.where(test.issued_at < month_start, month_start)
    records = []
    for cutoff, part in test.groupby("fit_cutoff"):
        observations = observed_before(hourly, cutoff-REPORTING_DELAY)
        if (observations.valid_time + pd.Timedelta(hours=3) > cutoff).any():
            raise ValueError("Training observations have not been reported")
        curve = curves(observations)
        for method, prediction in predictions(curve, part).items():
            output = part[["issued_at", "valid_time", "turbine_id", "horizon_hours", "actual", "fit_cutoff",
                           "bias_run_time", "bias_weather_content_hash", "bias12", "bias18", "pairs12", "pairs18",
                           "age12", "age18", "fallback12", "fallback18"]].copy()
            output["prediction"], output["error"] = prediction, prediction-part.actual
            output["model"], output["fold"] = method, start[:7]
            records.append(output)
    result = pd.concat(records, ignore_index=True)
    if result.duplicated(["model", "turbine_id", "valid_time"]).any() or not np.isfinite(result.prediction).all():
        raise ValueError("Invalid predictions")
    return result, state


def scores(frame):
    return frame.groupby("model").error.apply(lambda e: e.abs().mean()).sort_values().to_dict()


def run(output):
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    cfg = config()
    hourly, weather = load_hourly(cfg), weather_table()
    source = Weather(cfg, offline=True)
    write_json(out/"protocol.json", {"recipes": RECIPES, "selection": "December 1-29, saved before January evaluation",
        "reporting_delay_hours_after_interval_end": 2, "weather_delay_assumption_hours": cfg["weather_delay_hours"],
        "bias_source": "Past forecast values of the same eligible ECMWF run used for the target forecast",
        "minimum_history_pairs": 6, "maximum_observation_age_after_interval_end_hours": 5,
        "bias_clip_ms": 4, "damping": "exp(-(horizon_hours-1)/48)",
        "scope": "Full January1-31 and January31; horizons25-48; separate earlier boundary fit",
        "january": "Reused research diagnostic; no January fitting or recipe selection",
        "publication_provenance_verified": False, "production_changed": False,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    dec, dec_state = evaluate(hourly, weather, source, "2025-12-01", "2026-01-01")
    selection = scores(dec[dec.valid_time < local_date("2025-12-30", cfg)])
    selected = next(iter(selection))
    write_json(out/"selection.json", {"selected": selected, "december1_29_mae": selection})
    write_csv(out/"december_predictions.csv", dec)
    jan, jan_state = evaluate(hourly, weather, source, "2026-01-01", "2026-02-01")
    if not jan.groupby(["model", "turbine_id"]).size().eq(744).all():
        raise ValueError("Full January requires744 hours per turbine and model")
    last = jan[jan.valid_time >= local_date("2026-01-31", cfg)]
    if not last.groupby(["model", "turbine_id"]).size().eq(24).all():
        raise ValueError("January31 requires24 hours per turbine and model")
    combined = pd.concat([dec, jan], ignore_index=True)
    write_csv(out/"predictions.csv", combined)
    write_csv(out/"metrics.csv", summarize(combined))
    state = pd.concat([dec_state.assign(fold="2025-12"), jan_state.assign(fold="2026-01")], ignore_index=True)
    write_csv(out/"bias_state.csv", state)
    january, january31 = scores(jan), scores(last)
    report = {"selected": selected, "december1_29_mae": selection, "december_full_mae": scores(dec),
        "january_full_mae": january, "january31_mae": january31, "january_pairs_per_method": 1488,
        "january31_pairs_per_method": 48,
        "neutral_fallback_issuances": {f"{fold}_{window}": int(part[f"fallback{window}"].sum())
            for fold, part in state.groupby("fold") for window in [12, 18]},
        "numerical_target_met_by": [m for m in RECIPES if january[m] <= .05 and january31[m] <= .05],
        "production_changed": False, "publication_provenance_verified": False}
    write_json(out/"report.json", report)
    if selected != "curve":
        write_json(out/"uncertainty.json", {"candidate": selected, "scope": "Full January; exploratory reused evaluation",
            "jan31_inference": "One target day; no independent day-level significance inference", **uncertainty(jan, selected)})
    print(report, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/run-bias-benchmark")
    run(parser.parse_args().output)
