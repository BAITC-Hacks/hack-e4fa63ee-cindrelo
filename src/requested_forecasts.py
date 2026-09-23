"""Generate January 31 / February 1 day-ahead targets with frozen December model."""
import argparse
import json
import hashlib
from pathlib import Path

import pandas as pd

from .common import ROOT, config, local_date, iso, write_csv, write_json
from .data import load_hourly
from .pipeline import ForecastRun, run_deterministic, read_forecasts


def generate(output, offline=False):
    output = Path(output)
    cfg = config()
    bundle = json.loads((ROOT / "examples/backend/curve.json").read_text(encoding="utf-8-sig"))
    cutoff = local_date("2026-01-01", cfg)
    if pd.Timestamp(bundle["trained_until"]) != cutoff or bundle["selected"] != "curve":
        raise ValueError("Expected the December-trained, December-selected empirical curve")
    dates = ["2026-01-31", "2026-02-01"]
    existing = read_forecasts(output)
    if not existing.empty:
        expected_issues = {local_date(date, cfg) - pd.Timedelta(days=1) for date in dates}
        if not set(existing.issued_at).issubset(expected_issues) or not existing.model_version.eq(bundle["version"] + ":curve").all():
            raise ValueError("Output directory contains unrelated forecasts; use a dedicated directory")
    for date in dates:
        issue = local_date(date, cfg) - pd.Timedelta(days=1)
        run_deterministic(ForecastRun(cfg, issue, output, offline=offline, bundle=bundle))
    full = read_forecasts(output)
    if len(full) != 192:
        raise ValueError("Use a dedicated output directory for these two issuances")
    # Keep the established December-selection / January-test metrics separate
    # from the one-day diagnostic computed below.
    import shutil
    shutil.copyfile(ROOT / "examples/dashboard/metrics.csv", output / "metrics.csv")
    exports, summaries = [], []
    hourly = load_hourly(cfg)
    truth = hourly.loc[hourly.complete, ["valid_time", "turbine_id", "power_normalized"]]
    for date in dates:
        start = local_date(date, cfg)
        rows = full.loc[(full.valid_time >= start) & (full.valid_time < start + pd.Timedelta(days=1)) & (full.horizon_hours > 24)].copy()
        assert len(rows) == 48
        assert not rows.duplicated(["turbine_id", "valid_time"]).any()
        assert (rows.groupby("turbine_id").size() == 24).all()
        assert rows.issued_at.eq(start - pd.Timedelta(days=1)).all()
        write_csv(output / f"day_ahead_{date}.csv", rows)
        exports.append(rows)
        matched = rows.merge(truth.rename(columns={"power_normalized": "actual"}), on=["valid_time", "turbine_id"], how="left", validate="one_to_one")
        for turbine, group in matched.groupby("turbine_id"):
            scored = group.dropna(subset=["actual"])
            error = scored.power_normalized - scored.actual
            summaries.append({"target_date_local": date, "turbine_id": turbine, "issued_at": iso(rows.issued_at.iloc[0]),
                              "forecast_rows": len(group), "actual_rows": len(scored),
                              "mean_prediction": float(group.power_normalized.mean()),
                              "mae": None if scored.empty else float(error.abs().mean()),
                              "rmse": None if scored.empty else float((error.pow(2).mean())**.5)})
        write_csv(output / f"comparison_{date}.csv", matched)
    write_csv(output / "requested_day_ahead.csv", pd.concat(exports, ignore_index=True))
    report = {"target_dates_local": dates, "timezone": cfg["calendar_timezone"], "horizons": "25-48 (24 to <48 hours ahead)",
              "trained_until": bundle["trained_until"], "model_version": bundle["version"],
              "selection_window": "December 2025", "january_role": "historical test; not training for these outputs",
              "summaries": summaries, "february_actuals_available": False,
              "reason_for_frozen_model": "Both preceding-midnight issuances occur in January; training through January end would be future information.",
              "csv_sha256": hashlib.sha256((output / "requested_day_ahead.csv").read_bytes()).hexdigest()}
    write_json(output / "verification.json", report)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="outputs/requested-day-ahead")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    generate(args.output, args.offline)
