"""Load and validate dashboard contract v1 without changing source artifacts."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import re

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TURBINES = {"turbine_1": "Turbine 1", "turbine_2": "Turbine 2"}
FORECAST_COLUMNS = (
    "forecast_id", "issued_at", "valid_time", "turbine_id", "horizon_hours",
    "power_normalized", "weather_run_time", "weather_available_at",
    "model_version", "input_hash", "status",
)
ACTUAL_COLUMNS = ("valid_time", "turbine_id", "power_normalized")
METRIC_COLUMNS = ("model", "turbine_id", "horizon_bucket", "mae", "rmse", "n_samples", "period_start", "period_end")
EVENT_COLUMNS = ("forecast_id", "timestamp", "tool", "status", "message")


class ArtifactError(ValueError):
    """An actionable error safe to display without a Python traceback."""


@dataclass(frozen=True)
class DashboardData:
    manifest: dict
    forecasts: pd.DataFrame
    original_forecasts: pd.DataFrame
    actuals: pd.DataFrame
    metrics: pd.DataFrame
    events: pd.DataFrame


def output_directory() -> Path:
    configured = os.environ.get("CINDRELO_OUTPUT_DIR")
    if configured is not None and not configured.strip():
        raise ArtifactError("CINDRELO_OUTPUT_DIR: the override is empty. Set a directory, or unset it to use the fixture.")
    try:
        path = Path(configured).expanduser() if configured is not None else ROOT / "docs/fixtures/dashboard"
        return (path if path.is_absolute() else ROOT / path).resolve()
    except (OSError, ValueError, RuntimeError):
        raise ArtifactError("CINDRELO_OUTPUT_DIR: invalid directory. Set an accessible artifact directory and restart the app.") from None


def read_text(directory: Path, filename: str) -> str:
    path = directory / filename
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ArtifactError(f"{filename}: file not found. Publish this file in {directory}, then select Refresh.") from None
    except (OSError, UnicodeError):
        raise ArtifactError(f"{filename}: cannot read UTF-8 text. Check the file permissions and encoding, then select Refresh.") from None


def reject_constant(value: str):
    raise ValueError(f"Non-standard JSON constant: {value}")


def load_manifest(directory: Path) -> dict:
    filename = "manifest.json"
    try:
        manifest = json.loads(read_text(directory, filename), parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ArtifactError):
            raise
        raise ArtifactError(f"{filename}: invalid JSON. Use standard JSON without NaN or Infinity.") from None
    required = {"schema_version", "data_kind", "timezone", "target_unit", "description", "warnings"}
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise ArtifactError(f"{filename}: required fields are {', '.join(sorted(required))}.")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ArtifactError(f"{filename}: unsupported schema_version; this dashboard reads version 1.")
    if manifest["data_kind"] not in ("synthetic", "model_output"):
        raise ArtifactError(f"{filename}: data_kind must be synthetic or model_output.")
    if manifest["timezone"] != "UTC" or manifest["target_unit"] != "normalized_power":
        raise ArtifactError(f"{filename}: timezone must be UTC and target_unit must be normalized_power.")
    if not isinstance(manifest["description"], str):
        raise ArtifactError(f"{filename}: description must be a string.")
    if not isinstance(manifest["warnings"], list) or not all(isinstance(x, str) for x in manifest["warnings"]):
        raise ArtifactError(f"{filename}: warnings must be an array of strings.")
    metadata = manifest.get("forecast_metadata", {})
    if not isinstance(metadata, dict):
        raise ArtifactError(f"{filename}: forecast_metadata must be an object keyed by forecast ID.")
    for item in metadata.values():
        if (not isinstance(item, dict) or not isinstance(item.get("description"), str)
                or not isinstance(item.get("warnings"), list)
                or not all(isinstance(w, str) for w in item["warnings"])
                or not isinstance(item.get("metrics_scope", ""), str)):
            raise ArtifactError(f"{filename}: invalid per-forecast description, warnings or metric scope.")
    return manifest


def read_csv(directory: Path, filename: str, required: tuple[str, ...]) -> pd.DataFrame:
    content = read_text(directory, filename)
    try:
        reader = csv.reader(io.StringIO(content), strict=True)
        header = next(reader, [])
        if len(header) != len(set(header)):
            raise ArtifactError(f"{filename}: duplicate column names. Publish a unique header.")
        missing = sorted(set(required) - set(header))
        if missing:
            raise ArtifactError(f"{filename}: missing required columns: {', '.join(missing)}.")
        for row in reader:
            if row and len(row) != len(header):
                raise ArtifactError(f"{filename}: row near line {reader.line_num} has {len(row)} fields; expected {len(header)}. Check delimiters and quoting.")
        return pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, csv.Error):
        raise ArtifactError(f"{filename}: malformed CSV. Use a comma-delimited UTF-8 file with a header.") from None


def parse_times(frame: pd.DataFrame, columns: tuple[str, ...], filename: str) -> None:
    for column in columns:
        text = frame[column].astype(str)
        valid_format = text.str.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z")
        values = pd.to_datetime(text, utc=True, errors="coerce", format="mixed")
        if not valid_format.all() or values.isna().any():
            raise ArtifactError(f"{filename}: {column} must contain ISO 8601 UTC timestamps with a Z suffix.")
        frame[column] = values


def parse_number(frame: pd.DataFrame, column: str, filename: str, *, minimum=0, maximum=None, integer=False, nullable=False) -> None:
    text = frame[column]
    numbers = pd.to_numeric(text, errors="coerce")
    missing = text.str.strip().eq("") if nullable else pd.Series(False, index=frame.index)
    bad = ~np.isfinite(numbers) | numbers.lt(minimum)
    if maximum is not None:
        bad |= numbers.gt(maximum)
    if integer:
        bad |= numbers.mod(1).ne(0)
    if (bad & ~missing).any():
        bounds = f"{minimum}–{maximum}" if maximum is not None else f"at least {minimum}"
        raise ArtifactError(f"{filename}: {column} must be {'an integer' if integer else 'a finite number'} {bounds}.")
    frame[column] = numbers


def require_strings(frame: pd.DataFrame, columns: tuple[str, ...], filename: str) -> None:
    for column in columns:
        if frame[column].str.strip().eq("").any():
            raise ArtifactError(f"{filename}: {column} must not be empty.")


def validate_turbines(frame: pd.DataFrame, filename: str) -> None:
    if not frame["turbine_id"].isin(TURBINES).all():
        raise ArtifactError(f"{filename}: turbine_id must be turbine_1 or turbine_2.")


def load_forecasts(directory: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    filename = "forecasts.csv"
    original = read_csv(directory, filename, FORECAST_COLUMNS)
    if original.empty:
        raise ArtifactError(f"{filename}: no published forecasts. Add forecast rows before opening this view.")
    frame = original.copy()
    require_strings(frame, ("forecast_id", "model_version", "input_hash"), filename)
    parse_times(frame, ("issued_at", "valid_time", "weather_run_time", "weather_available_at"), filename)
    parse_number(frame, "power_normalized", filename, maximum=1)
    parse_number(frame, "horizon_hours", filename, minimum=1, maximum=48, integer=True)
    validate_turbines(frame, filename)
    if not frame.status.isin(["ok", "degraded"]).all():
        raise ArtifactError(f"{filename}: status must be ok or degraded; failed runs belong in events.jsonl.")
    if frame.duplicated(["forecast_id", "turbine_id", "valid_time"]).any():
        raise ArtifactError(f"{filename}: duplicate forecast/turbine/target-time keys. Publish one row per key.")
    if not frame.issued_at.eq(frame.issued_at.dt.floor("h")).all() or not frame.valid_time.eq(frame.valid_time.dt.floor("h")).all():
        raise ArtifactError(f"{filename}: issued_at and valid_time must be on hourly boundaries.")
    expected = (frame.valid_time - frame.issued_at).dt.total_seconds() / 3600 + 1
    if not frame.horizon_hours.eq(expected).all():
        raise ArtifactError(f"{filename}: horizon_hours must equal (valid_time − issued_at) / 1 hour + 1.")
    if (frame.weather_run_time > frame.weather_available_at).any() or (frame.weather_available_at > frame.issued_at).any():
        raise ArtifactError(f"{filename}: weather must initialize no later than availability and be available by issuance.")
    for forecast_id, run in frame.groupby("forecast_id", sort=False):
        if run.issued_at.nunique() != 1:
            raise ArtifactError(f"{filename}: forecast {forecast_id} has inconsistent issuance times.")
        for turbine in TURBINES:
            part = run[run.turbine_id.eq(turbine)]
            if set(part.horizon_hours) != set(range(1, 49)) or len(part) != 48:
                raise ArtifactError(f"{filename}: forecast {forecast_id} needs 48 unique hourly targets for {turbine}.")
    return frame.sort_values(["issued_at", "forecast_id", "turbine_id", "valid_time"]), original


def load_actuals(directory: Path) -> pd.DataFrame:
    filename = "actuals.csv"
    frame = read_csv(directory, filename, ACTUAL_COLUMNS)
    parse_times(frame, ("valid_time",), filename)
    validate_turbines(frame, filename)
    parse_number(frame, "power_normalized", filename, maximum=1, nullable=True)
    if frame.duplicated(["turbine_id", "valid_time"]).any():
        raise ArtifactError(f"{filename}: duplicate turbine/target-time keys. Publish one observation per key.")
    if not frame.valid_time.eq(frame.valid_time.dt.floor("h")).all():
        raise ArtifactError(f"{filename}: valid_time must label the start of an hourly interval.")
    return frame.sort_values("valid_time")


def load_metrics(directory: Path) -> pd.DataFrame:
    filename = "metrics.csv"
    frame = read_csv(directory, filename, METRIC_COLUMNS)
    require_strings(frame, ("model",), filename)
    validate_turbines(frame, filename)
    if not frame.horizon_bucket.isin(["1-24", "25-48"]).all():
        raise ArtifactError(f"{filename}: horizon_bucket must be 1-24 or 25-48.")
    parse_times(frame, ("period_start", "period_end"), filename)
    if not frame.period_start.lt(frame.period_end).all():
        raise ArtifactError(f"{filename}: period_end must be later than period_start (end-exclusive).")
    for column in ("mae", "rmse"):
        parse_number(frame, column, filename)
    parse_number(frame, "n_samples", filename, integer=True)
    return frame.sort_values(["period_start", "horizon_bucket", "model"])


def load_events(directory: Path) -> pd.DataFrame:
    filename = "events.jsonl"
    records = []
    for number, line in enumerate(read_text(directory, filename).splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, parse_constant=reject_constant)
        except (ValueError, json.JSONDecodeError):
            raise ArtifactError(f"{filename}: line {number} must be a standard JSON object without NaN or Infinity.") from None
        if not isinstance(record, dict) or any(not isinstance(record.get(key), str) for key in EVENT_COLUMNS):
            raise ArtifactError(f"{filename}: line {number} requires string fields: {', '.join(EVENT_COLUMNS)}.")
        records.append({key: record[key] for key in EVENT_COLUMNS})
    frame = pd.DataFrame(records, columns=EVENT_COLUMNS)
    require_strings(frame, ("forecast_id", "tool"), filename)
    parse_times(frame, ("timestamp",), filename)
    if not frame.status.isin(["started", "completed", "warning", "failed", "skipped"]).all():
        raise ArtifactError(f"{filename}: status must be started, completed, warning, failed or skipped.")
    return frame.sort_values("timestamp", kind="stable")


def load_dashboard(directory: Path) -> DashboardData:
    manifest = load_manifest(directory)
    forecasts, original = load_forecasts(directory)
    return DashboardData(manifest, forecasts, original, load_actuals(directory), load_metrics(directory), load_events(directory))


def forecast_choices(forecasts: pd.DataFrame) -> pd.DataFrame:
    return forecasts[["forecast_id", "issued_at"]].drop_duplicates().sort_values(["issued_at", "forecast_id"], ascending=[False, True]).reset_index(drop=True)


def export_forecast(data: DashboardData, forecast_id: str) -> bytes:
    selected = data.original_forecasts[data.original_forecasts.forecast_id.eq(forecast_id)]
    return selected.to_csv(index=False, lineterminator="\n").encode("utf-8")


def export_filename(forecast_id: str) -> str:
    safe_id = re.sub(r"[^\w.-]", "_", forecast_id, flags=re.UNICODE)[:120]
    return f"cindrelo-forecast-{safe_id}.csv"


def previous_issuances(forecasts: pd.DataFrame, forecast_id: str) -> pd.DataFrame:
    issue = forecasts.loc[forecasts.forecast_id.eq(forecast_id), "issued_at"].iloc[0]
    choices = forecast_choices(forecasts)
    earlier = choices[choices.issued_at.lt(issue)]
    # IDs are opaque, not ordered revision numbers. If the prior issuance has
    # multiple versions, let the user choose explicitly between those versions.
    return earlier[earlier.issued_at.eq(earlier.issued_at.max())]


def compare_forecasts(selected: pd.DataFrame, previous: pd.DataFrame) -> pd.DataFrame:
    return selected[["valid_time", "power_normalized"]].merge(
        previous[["valid_time", "power_normalized"]], on="valid_time", how="inner",
        suffixes=("_selected", "_previous"), validate="one_to_one",
    ).sort_values("valid_time")


def aligned_actuals(selected: pd.DataFrame, actuals: pd.DataFrame, turbine: str) -> pd.DataFrame:
    # Keep the selected hourly grid so missing observations break the line.
    return selected[["valid_time"]].merge(
        actuals.loc[actuals.turbine_id.eq(turbine), ["valid_time", "power_normalized"]],
        on="valid_time", how="left", validate="one_to_one",
    ).sort_values("valid_time")
