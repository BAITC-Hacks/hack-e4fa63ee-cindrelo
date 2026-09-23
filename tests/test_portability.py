"""Exercise real artifacts under a simulated Windows legacy text encoding."""
import builtins
import io
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

from src import __main__ as cli
from src import agent, common, data, pipeline
from src.weather import Weather
from ui.data import load_dashboard

ROOT = common.ROOT


@pytest.fixture
def windows_default_encoding(monkeypatch):
    # Python 3.14/macOS cannot select a Windows ANSI locale. Emulate its file
    # default, including Path's encoding="locale", without changing explicit
    # encodings. Unlike an -X utf8 test, this reproduces the original failure.
    def legacy_default(original):
        def open_file(file, mode="r", buffering=-1, encoding=None, errors=None,
                      newline=None, closefd=True, opener=None):
            if "b" not in mode and encoding in (None, "locale"):
                encoding = "cp1252"
            return original(file, mode, buffering, encoding, errors, newline, closefd, opener)
        return open_file

    monkeypatch.setattr(builtins, "open", legacy_default(builtins.open))
    monkeypatch.setattr(io, "open", legacy_default(io.open))


def test_demo_reproduces_and_deduplicates_with_windows_default(tmp_path, monkeypatch, windows_default_encoding):
    # Isolate from local prepared data/models/credentials; exercise cold start.
    for module in (cli, common, data, pipeline):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    (tmp_path / "docs").mkdir()
    for path in (ROOT / "docs").glob("*.csv"):
        shutil.copyfile(path, tmp_path / "docs" / path.name)
    # Also accept UTF-8 BOMs commonly emitted by Windows editors.
    (tmp_path / "config.json").write_bytes(b"\xef\xbb\xbf" + (ROOT / "config.json").read_bytes())
    curve = tmp_path / "examples/backend/curve.json"
    curve.write_bytes(b"\xef\xbb\xbf" + curve.read_bytes())
    monkeypatch.setattr(sys, "argv", ["src", "demo"])
    cli.main()
    directory = tmp_path / "outputs/offline-demo"
    actual = load_dashboard(directory)
    expected = load_dashboard(ROOT / "examples/dashboard")
    pd.testing.assert_frame_equal(actual.original_forecasts, expected.original_forecasts)
    pd.testing.assert_frame_equal(actual.actuals, expected.actuals)
    pd.testing.assert_frame_equal(actual.metrics, expected.metrics)
    metadata_path = tmp_path / "data/preparation.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert "участников" in metadata["turbines"]["turbine_1"]["source"]
    before = (directory / "forecasts.csv").read_bytes()
    # The repeat must load the generated UTF-8 metadata and append skip events.
    cli.main()
    assert (directory / "forecasts.csv").read_bytes() == before
    assert load_dashboard(directory).events.status.eq("skipped").sum() == 2


def test_unicode_artifact_and_environment_roundtrip(tmp_path, monkeypatch, windows_default_encoding):
    message = "Температура −5 °C; прогноз выработки ВЭС"
    common.write_json(tmp_path / "metadata.json", {"message": message})
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["message"] == message
    common.write_csv(tmp_path / "labels.csv", pd.DataFrame({"label": [message]}))
    assert pd.read_csv(tmp_path / "labels.csv", encoding="utf-8").label.iloc[0] == message
    bundle = json.loads((ROOT / "examples/backend/curve.json").read_text(encoding="utf-8"))
    run = pipeline.ForecastRun(common.config(), "2026-01-09T19:00Z", tmp_path, bundle=bundle)
    run.event("encoding_check", "completed", message)
    run.flush_events()
    assert json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))["message"] == message
    monkeypatch.setattr(agent, "ROOT", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-only\n# Проверка\nOPENAI_MODEL=test-model\n", encoding="utf-8-sig")
    agent.load_environment()
    assert agent.os.environ["OPENAI_API_KEY"] == "test-only"
    assert agent.os.environ["OPENAI_MODEL"] == "test-model"


def test_cache_corruption_still_fails_checksum(tmp_path, windows_default_encoding):
    cache = ROOT / "examples/backend/weather"
    shutil.copytree(cache, tmp_path / "weather")
    cfg = common.config()
    provider = Weather(cfg, offline=True, cache_dir=tmp_path / "weather")
    run = common.utc("2026-01-09T00:00Z")
    # Correct UTF-8 decoding verifies the existing digest; hashes are unchanged.
    envelope = provider.fetch("turbine_1", run)
    assert envelope["response"]["hourly_units"]["temperature_2m"] == "°C"
    path = provider.cache_dir / (common.digest(envelope["request"]) + ".json")
    envelope["response"]["hourly"]["temperature_2m"][0] += 1
    common.write_json(path, envelope)
    with pytest.raises(ValueError, match="checksum mismatch"):
        provider.fetch("turbine_1", run)
