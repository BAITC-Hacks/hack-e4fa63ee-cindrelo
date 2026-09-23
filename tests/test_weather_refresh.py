"""Invalid refreshes must never destroy a working historical weather cache."""
import copy
import io
import json
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from src import weather
from src.common import ROOT, config

ISSUE = "2026-01-09T19:00:00Z"


def corrupt(payload, kind):
    if kind == "null_wind":
        payload["hourly"]["wind_speed_100m"][19] = None
    elif kind == "units":
        payload["hourly_units"]["wind_speed_100m"] = "km/h"
    elif kind == "duplicate_time":
        payload["hourly"]["time"][20] = payload["hourly"]["time"][19]
    elif kind == "missing_target":
        # Every remaining value is valid, but one required forecast hour is absent.
        for values in payload["hourly"].values():
            del values[40]
    elif kind == "missing_variable":
        del payload["hourly"]["wind_speed_100m"]
    elif kind == "coordinates":
        payload["latitude"] = None
    return payload


def responses(monkeypatch, directory, kind):
    by_latitude = {}
    for file in directory.glob("*.json"):
        envelope = json.loads(file.read_text(encoding="utf-8"))
        key = (str(envelope["request"]["latitude"]), envelope["request"]["run"])
        by_latitude[key] = envelope["response"]

    def urlopen(url, **kwargs):
        query = parse_qs(urlparse(url).query)
        response = corrupt(copy.deepcopy(by_latitude[(query["latitude"][0], query["run"][0])]), kind)
        return io.BytesIO(json.dumps(response).encode("utf-8"))

    monkeypatch.setattr(weather, "urlopen", urlopen)
    monkeypatch.setattr(weather.time, "sleep", lambda _: None)


@pytest.mark.parametrize("kind", ["null_wind", "units", "duplicate_time", "missing_target", "missing_variable", "coordinates"])
def test_invalid_http200_preserves_good_cache_and_recovers_explicitly(tmp_path, monkeypatch, kind):
    directory = tmp_path/"weather"
    shutil.copytree(ROOT/"examples/backend/weather", directory)
    original = {p.name: p.read_bytes() for p in directory.glob("*.json")}
    before = weather.Weather(config(), offline=True, cache_dir=directory, stable_hashes=True).horizon(ISSUE)
    responses(monkeypatch, directory, kind)
    provider = weather.Weather(config(), cache_dir=directory, refresh=True, attempts=1, stable_hashes=True)
    result = provider.horizon(ISSUE)
    assert len(result) == 96
    assert result.weather_hash.iloc[0] == before.weather_hash.iloc[0]
    assert {r["source"] for r in provider.retrievals} == {"cached_fallback"}
    assert original == {p.name: p.read_bytes() for p in directory.glob("*.json")}
    assert len(weather.Weather(config(), offline=True, cache_dir=directory).horizon(ISSUE)) == 96


def test_incomplete_http200_is_not_cached_without_a_valid_fallback(tmp_path, monkeypatch):
    responses(monkeypatch, ROOT/"examples/backend/weather", "missing_target")
    directory = tmp_path/"new-weather"
    provider = weather.Weather(config(), cache_dir=directory, refresh=True, attempts=1)
    with pytest.raises(ValueError, match="complete 48-hour horizon"):
        provider.horizon(ISSUE)
    assert not list(directory.glob("*.json"))
