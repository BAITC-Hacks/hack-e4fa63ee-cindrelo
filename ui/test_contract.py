"""Contract/interaction checks: run python -m unittest ui.test_contract."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from ui.charts import forecast_chart
from ui.data import (ArtifactError, ROOT, aligned_actuals, compare_forecasts,
                     export_forecast, load_dashboard, output_directory,
                     previous_issuances)

FIXTURE = ROOT / "docs/fixtures/dashboard"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "artifacts"
        shutil.copytree(FIXTURE, self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def load(self):
        return load_dashboard(self.directory)

    def selected(self, data, forecast="demo-2", turbine="turbine_1"):
        return data.forecasts[data.forecasts.forecast_id.eq(forecast) & data.forecasts.turbine_id.eq(turbine)]

    def test_export_retains_original_columns_values_and_both_turbines(self):
        path = self.directory / "forecasts.csv"
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
        raw["extra_backend_field"] = "000123"
        raw.to_csv(path, index=False)
        data = self.load()
        for forecast in ("demo-1", "demo-2"):
            exported = pd.read_csv(io.BytesIO(export_forecast(data, forecast)), dtype=str, keep_default_na=False)
            expected = raw[raw.forecast_id.eq(forecast)].reset_index(drop=True)
            pd.testing.assert_frame_equal(exported, expected)
            self.assertEqual(len(exported), 96)
            self.assertEqual(set(exported.turbine_id), {"turbine_1", "turbine_2"})

    def test_revision_matches_target_time_despite_shuffled_rows(self):
        data = self.load()
        current = self.selected(data).sample(frac=1, random_state=3)
        previous = self.selected(data, "demo-1").sample(frac=1, random_state=7)
        overlap = compare_forecasts(current, previous)
        self.assertEqual(len(overlap), 24)
        self.assertAlmostEqual((overlap.power_normalized_selected - overlap.power_normalized_previous).abs().mean(), .04)
        self.assertTrue(overlap.valid_time.is_monotonic_increasing)
        self.assertEqual(set(overlap.valid_time), set(current.valid_time) & set(previous.valid_time))

    def test_no_previous_or_no_overlap(self):
        data = self.load()
        self.assertTrue(previous_issuances(data.forecasts, "demo-1").empty)
        previous = self.selected(data, "demo-1").copy()
        previous["valid_time"] -= pd.Timedelta(days=10)
        self.assertTrue(compare_forecasts(self.selected(data), previous).empty)

    def test_same_issue_versions_are_explicit_choices_not_lexical_recency(self):
        data = self.load()
        copy = data.forecasts[data.forecasts.forecast_id.eq("demo-1")].copy()
        copy["forecast_id"] = "another-version"
        choices = previous_issuances(pd.concat([data.forecasts, copy]), "demo-2")
        self.assertEqual(set(choices.forecast_id), {"demo-1", "another-version"})

    def test_missing_actual_is_gap_and_never_other_turbines_value(self):
        data = self.load()
        selected = self.selected(data)
        missing_time = selected.valid_time.iloc[5]
        actual = data.actuals[~(data.actuals.turbine_id.eq("turbine_1") & data.actuals.valid_time.eq(missing_time))]
        aligned = aligned_actuals(selected, actual, "turbine_1")
        self.assertEqual(len(aligned), 48)
        self.assertTrue(aligned.loc[aligned.valid_time.eq(missing_time), "power_normalized"].isna().all())
        chart = forecast_chart(selected, synthetic=True, actuals=aligned)
        self.assertFalse(chart.data[-1].connectgaps)
        self.assertIsNone(chart.data[-1].y[5])

    def test_empty_optional_content_is_valid(self):
        for name in ("actuals.csv", "metrics.csv"):
            path = self.directory / name
            path.write_text(path.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
        (self.directory / "events.jsonl").write_text("", encoding="utf-8")
        data = self.load()
        self.assertTrue(data.actuals.empty and data.metrics.empty and data.events.empty)
        with patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": str(self.directory)}):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
        self.assertFalse(app.exception)
        messages = " ".join(x.value for x in app.info)
        for phrase in ("Actuals unavailable", "No evaluation metrics", "No tool events"):
            self.assertIn(phrase, messages)

    def test_events_sort_chronologically_and_plain_message_preserved(self):
        path = self.directory / "events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line]
        records[0]["message"] = '<script>alert("example")</script>'
        path.write_text("\n".join(json.dumps(row) for row in reversed(records)), encoding="utf-8")
        events = self.load().events
        self.assertTrue(events.timestamp.is_monotonic_increasing)
        self.assertEqual(events.iloc[0].message, '<script>alert("example")</script>')

    def test_malformed_files_report_filename(self):
        cases = [("manifest.json", '{"schema_version":NaN}'), ("events.jsonl", '{"oops":1}'),
                 ("actuals.csv", "valid_time,turbine_id\n"),
                 ("metrics.csv", "model,turbine_id,horizon_bucket,mae,rmse,n_samples,period_start,period_end\nwrong,row,width\n")]
        for filename, content in cases:
            with self.subTest(filename=filename):
                path = self.directory / filename
                saved = path.read_bytes()
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(ArtifactError, filename.replace('.', r'\.')):
                    self.load()
                path.write_bytes(saved)

    def test_invalid_temporal_and_numeric_forecasts_are_rejected(self):
        path = self.directory / "forecasts.csv"
        original = path.read_bytes()
        for column, value in [("weather_available_at", "2026-02-01T00:00:00Z"), ("power_normalized", "1.2"),
                              ("horizon_hours", "48"), ("valid_time", "2026-01-10T00:00:00+00:00")]:
            with self.subTest(column=column):
                frame = pd.read_csv(io.BytesIO(original), dtype=str)
                frame.loc[0, column] = value
                frame.to_csv(path, index=False)
                with self.assertRaisesRegex(ArtifactError, "forecasts.csv"):
                    self.load()
        path.write_bytes(original)

    def test_duplicate_keys_are_rejected(self):
        for filename in ("forecasts.csv", "actuals.csv"):
            with self.subTest(filename=filename):
                path = self.directory / filename
                original = path.read_bytes()
                frame = pd.read_csv(path)
                pd.concat([frame, frame.iloc[:1]]).to_csv(path, index=False)
                with self.assertRaisesRegex(ArtifactError, "duplicate"):
                    self.load()
                path.write_bytes(original)

    def test_overrides_do_not_depend_on_caller_directory_or_fallback(self):
        previous = Path.cwd()
        try:
            os.chdir(self.temp.name)
            with patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": "docs/fixtures/dashboard"}):
                self.assertEqual(output_directory(), FIXTURE.resolve())
            with patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": str(self.directory)}):
                self.assertEqual(output_directory(), self.directory.resolve())
            with patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": ""}):
                with self.assertRaises(ArtifactError):
                    output_directory()
            with patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": str(self.directory / "missing")}):
                app = AppTest.from_file(str(ROOT / "app.py")).run()
                self.assertFalse(app.exception)
                self.assertIn("manifest.json", app.error[0].value)
                self.assertEqual(len(app.selectbox), 0)
        finally:
            os.chdir(previous)

    def test_selectors_and_refresh_read_changed_files(self):
        with patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": str(self.directory)}):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="forecast_id").value, "demo-2")
            self.assertEqual(next(item.value for item in app.metric if item.label == "Forecast revision"), "0.0400")
            app.selectbox(key="turbine").select("turbine_2").run()
            app.selectbox(key="forecast_id").select("demo-1").run()
            self.assertFalse(app.exception)
            self.assertIn("No previous overlapping forecast", [item.value for item in app.info])
            manifest_path = self.directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["warnings"].append("New published input warning")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            app.button[0].click().run()
            self.assertIn("New published input warning", [item.value for item in app.warning])
            self.assertEqual(app.selectbox(key="forecast_id").value, "demo-1")


if __name__ == "__main__":
    unittest.main()
