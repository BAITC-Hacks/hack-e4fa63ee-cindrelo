import argparse
import json
from pathlib import Path

import pandas as pd

from .common import ROOT, config, local_date, write_csv


def main():
    parser = argparse.ArgumentParser(description="Cindrelo wind forecasting pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    demo = sub.add_parser("demo", help="Recompute two real forecasts offline from the bundled curve and weather")
    demo.add_argument("--output", default="outputs/offline-demo")
    training = sub.add_parser("train")
    training.add_argument("--offline", action="store_true")
    for command in ["forecast", "replay"]:
        p = sub.add_parser(command)
        p.add_argument("--offline", action="store_true")
        p.add_argument("--output", default="outputs/dashboard")
        if command == "forecast":
            p.add_argument("--issue", required=True, help="Explicit UTC/offset timestamp, on the hour")
            p.add_argument("--agent", action="store_true")
        else:
            p.add_argument("--start", default="2026-01-31")
            p.add_argument("--end", default="2026-03-01", help="Exclusive local-calendar date")
    args = parser.parse_args()
    cfg = config()
    if args.command == "prepare":
        from .data import prepare
        print(json.dumps(prepare(cfg)[1], indent=2))
    elif args.command == "train":
        from .training import train
        train(cfg, args.offline)
    elif args.command == "demo":
        from .pipeline import ForecastRun, run_deterministic
        from .weather import Weather
        source = ROOT / "examples/backend"
        bundle = json.loads((source / "curve.json").read_text(encoding="utf-8-sig"))
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        for issue in ["2026-01-09T19:00:00Z", "2026-01-10T07:00:00Z"]:
            run = ForecastRun(cfg, issue, output, offline=True, bundle=bundle)
            run.provider = Weather(cfg, offline=True, cache_dir=source / "weather")
            print(run_deterministic(run))
        # The bundled metrics are measured by the documented January evaluation.
        import shutil
        shutil.copyfile(ROOT / "examples/dashboard/metrics.csv", output / "metrics.csv")
    else:
        from .pipeline import ForecastRun, read_forecasts, run_deterministic
        output = Path(args.output)
        if not output.is_absolute():
            output = ROOT / output
        if args.command == "forecast":
            run = ForecastRun(cfg, args.issue, output, args.offline)
            if args.agent:
                from .agent import run_agent
                print(run_agent(run))
            else:
                print(run_deterministic(run))
        else:
            origins = pd.date_range(args.start, args.end, freq="D", inclusive="left", tz=cfg["calendar_timezone"])
            for issue in origins:
                print(run_deterministic(ForecastRun(cfg, issue, output, args.offline)), flush=True)
            forecasts = read_forecasts(output)
            # Fixed 24–48-hour-ahead policy: preceding local midnight's horizon 25–48.
            february = forecasts.loc[(forecasts.valid_time >= local_date("2026-02-01", cfg)) &
                                     (forecasts.valid_time < local_date("2026-03-01", cfg)) &
                                     (forecasts.horizon_hours > 24) &
                                     (forecasts.issued_at.dt.tz_convert(cfg["calendar_timezone"]).dt.hour == 0)]
            february = february.sort_values("issued_at", kind="stable").drop_duplicates(["turbine_id", "valid_time"], keep="last")
            if len(february) != 1344:
                raise ValueError(f"February export incomplete: {len(february)}/1344 rows; use default replay range")
            write_csv(output / "february_day_ahead.csv", february)
            print("February day-ahead export: 1,344 rows")


if __name__ == "__main__":
    main()
