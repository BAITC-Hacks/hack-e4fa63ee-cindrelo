"""One user action runs the guarded forecast and an optional weather update.

This service is independent of Streamlit. It preserves committed examples and
only exposes a new dashboard snapshot after the complete requested cycle passes.
"""
import json
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

import pandas as pd

from .agent import load_environment, run_agent
from .common import ROOT, config, digest, iso, utc, write_csv, write_json
from .model import load_bundle
from .pipeline import ForecastRun, read_forecasts, run_deterministic
from .weather import Weather

FILES = ("manifest.json", "forecasts.csv", "actuals.csv", "metrics.csv", "events.jsonl")
_CYCLE_LOCK = threading.Lock()


class ForecastCycleError(RuntimeError):
    """A concise operational error safe to display in the dashboard."""


def _bundle(issue, cfg):
    for name in ["final", "validation", "tuning"]:
        if (ROOT / "artifacts" / f"{name}.pkl").exists():
            bundle = load_bundle(name, cfg)
            if utc(bundle["trained_until"]) <= issue:
                return bundle
    bundle = json.loads((ROOT / "examples/backend/curve.json").read_text(encoding="utf-8-sig"))
    if bundle["config_hash"] != digest(cfg):
        raise ForecastCycleError("Configuration changed. Run python -m src train before using the forecast cycle.")
    if utc(bundle["trained_until"]) > issue:
        raise ForecastCycleError("This issuance predates the available trained model. Choose January 2026 or later.")
    return bundle


def run_forecast_cycle(*, issued_at, output, controller="agent", offline=False,
                       include_update=True, on_event=None):
    """Refresh weather, prepare, forecast, analyse, and replay an eligible update.

    An already trained model is used; training is deliberately outside inference.
    Calls are serialized within the dashboard process because source preparation
    and weather caches are shared. Separate Streamlit sessions use separate dirs.
    """
    try:
        issue = utc(issued_at)
        if pd.isna(issue) or issue != issue.floor("h"):
            raise ValueError("on the hour")
    except (ValueError, TypeError):
        raise ForecastCycleError("Enter an hourly UTC issuance, for example 2026-01-09T19:00:00Z.") from None
    if controller not in {"agent", "deterministic"}:
        raise ForecastCycleError("Choose the OpenAI agent or deterministic rehearsal controller.")
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT / "outputs").resolve()) or output == (ROOT / "outputs").resolve():
        raise ForecastCycleError("Generated forecasts must use a dedicated directory under outputs/.")
    if controller == "agent":
        load_environment()
        if not os.environ.get("OPENAI_API_KEY"):
            raise ForecastCycleError("Set OPENAI_API_KEY in the local .env file, or choose offline deterministic rehearsal.")
    if not _CYCLE_LOCK.acquire(blocking=False):
        raise ForecastCycleError("Another forecast cycle is running. Wait for it to finish, then retry.")
    try:
        cfg = config()
        issues = [issue, issue + pd.Timedelta(hours=12)] if include_update else [issue]
        bundles = [_bundle(origin, cfg) for origin in issues]
        output.parent.mkdir(parents=True, exist_ok=True)
        previous = None
        if (output / "current.json").exists():
            name = json.loads((output / "current.json").read_text(encoding="utf-8"))["snapshot"]
            if not isinstance(name, str) or Path(name).name != name or not name.startswith("snapshot-"):
                raise ForecastCycleError("Invalid saved snapshot reference. Choose a new run session.")
            previous = output / name
        snapshot = output / ("snapshot-" + uuid.uuid4().hex)
        # Keep the last completed view intact if fetching, the agent or a revision fails.
        with tempfile.TemporaryDirectory(prefix=".cycle-", dir=output.parent) as directory:
            staging = Path(directory)
            for filename in FILES:
                source = previous / filename if previous else None
                if source is not None and source.exists():
                    shutil.copyfile(source, staging / filename)
            results, ids = [], []
            hourly = None
            try:
                for index, (origin, bundle) in enumerate(zip(issues, bundles)):
                    run = ForecastRun(cfg, origin, staging, offline=offline, bundle=bundle,
                                      on_event=on_event, hourly=hourly, refresh_observations=True)
                    run.provider = Weather(cfg, offline=offline, refresh=not offline,
                                           fallback_cache_dirs=(ROOT / "examples/backend/weather",),
                                           stable_hashes=True, timeout=12, attempts=1)
                    run.event("cycle", "started", "Initial forecast" if index == 0 else
                              "Replay advanced 12 hours; checking newer eligible weather and recalculating")
                    run.event("model", "completed", json.dumps({"version": bundle["version"],
                              "trained_until": bundle["trained_until"], "controller": controller}))
                    result = run_agent(run) if controller == "agent" else run_deterministic(run)
                    if not run.done:
                        raise ForecastCycleError("The controller ended before completing the forecast.")
                    hourly = run.hourly
                    results.append({**result, "directory": str(snapshot)})
                    ids.append(run.forecast_id)
                # Observation updates are independent of numerical forecast identity.
                # Even an unchanged-weather skip must show newly available actuals.
                forecasts = read_forecasts(staging)
                actuals = hourly.loc[hourly.complete & hourly.valid_time.between(
                    forecasts.valid_time.min(), forecasts.valid_time.max()),
                    ["valid_time", "turbine_id", "power_normalized"]]
                write_csv(staging / "actuals.csv", actuals)
                # Fresh clones use the bundled measured January scores, with their
                # original evaluation window, when no new evaluation exists locally.
                if not (ROOT / "artifacts/metrics.csv").exists():
                    shutil.copyfile(ROOT / "examples/dashboard/metrics.csv", staging / "metrics.csv")
                for filename in FILES:
                    if not (staging / filename).exists():
                        raise ForecastCycleError(f"Forecast cycle did not produce {filename}.")
                output.mkdir(parents=True, exist_ok=True)
                # Each completed snapshot is immutable. The atomic pointer update
                # below is the only operation that makes a new snapshot current.
                os.replace(staging, snapshot)
                write_json(output / "current.json", {"snapshot": snapshot.name})
            except Exception as exc:
                # Persist the failed trace separately; do not replace the last
                # successful snapshot or label a partial two-origin cycle complete.
                logs = (snapshot if snapshot.exists() else staging) / "events.jsonl"
                if logs.exists():
                    failed = output.parent / "failed-cycles" / uuid.uuid4().hex
                    failed.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(logs, failed / "events.jsonl")
                if isinstance(exc, ForecastCycleError):
                    raise
                raise ForecastCycleError(
                    f"Forecast cycle failed ({type(exc).__name__}). Previous results were kept. "
                    "Check API/weather access and local run logs, or try offline deterministic rehearsal."
                ) from None
        return {"output_dir": str(snapshot), "forecast_id": ids[-1], "forecast_ids": ids, "results": results}
    except ForecastCycleError:
        raise
    except Exception as exc:
        raise ForecastCycleError(f"Cannot start the forecast cycle ({type(exc).__name__}). Check configuration and model artifacts.") from None
    finally:
        _CYCLE_LOCK.release()
