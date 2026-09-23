"""The dashboard runs only on request and selects validated generated artifacts."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from streamlit.testing.v1 import AppTest

from ui.data import ROOT, load_dashboard
from ui.runner import CycleDisplayError, DEFAULT_ISSUED_AT, OFFLINE_MODE

FIXTURE = ROOT / "docs/fixtures/dashboard"


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output_root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": str(FIXTURE)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root_patch = patch("ui.runner.ROOT", self.output_root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def app(self):
        return AppTest.from_file(str(ROOT / "app.py")).run()

    def publish(self, **kwargs):
        output = kwargs["output"]
        existed = output.exists()
        snapshot = output / f"snapshot-{uuid4().hex}"
        shutil.copytree(FIXTURE, snapshot)
        for name in ("forecasts.csv", "events.jsonl"):
            path = snapshot / name
            path.write_text(path.read_text(encoding="utf-8").replace("demo-", "generated-"), encoding="utf-8")
        kwargs["on_event"]({
            "issued_at": kwargs["issued_at"], "tool": "publish_forecast",
            "status": "completed", "message": "Already published" if existed else "Published both forecasts",
        })
        return {
            "output_dir": str(snapshot.resolve()), "forecast_id": "generated-2",
            "forecast_ids": ["generated-1", "generated-2"],
            "results": [{"status": "deduplicated" if existed else "published"}],
        }

    def test_loading_refreshing_and_selecting_never_runs_backend(self):
        with patch("ui.runner.invoke_cycle") as cycle:
            app = self.app()
            app.button[0].click().run()
            app.selectbox(key="turbine").select("turbine_2").run()
            self.assertFalse(app.exception)
            cycle.assert_not_called()
            self.assertEqual(app.button[0].label, "Refresh")
            self.assertFalse((self.output_root / "outputs").exists())

    def test_success_loads_generated_output_and_repeat_reuses_session_directory(self):
        with patch("ui.runner.invoke_cycle", side_effect=self.publish) as cycle:
            app = self.app()
            app.button(key="run_forecast_cycle").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="forecast_id").value, "generated-2")
            kwargs = cycle.call_args.kwargs
            self.assertEqual(kwargs["issued_at"], DEFAULT_ISSUED_AT)
            self.assertEqual(kwargs["controller"], "agent")
            self.assertFalse(kwargs["offline"])
            self.assertTrue(kwargs["include_update"])
            directory = kwargs["output"]
            self.assertEqual(directory.parent, self.output_root / "outputs/dashboard-runs")
            self.assertNotEqual(directory, FIXTURE)
            self.assertTrue(any("Published both forecasts" in item.value for item in app.text))
            snapshot = Path(app.session_state["cycle_display_directory"])
            self.assertEqual(snapshot.parent, directory.resolve())
            self.assertTrue(snapshot.name.startswith("snapshot-"))
            self.assertTrue(any(str(snapshot) == item.value for item in app.code))
            app.button[0].click().run()
            self.assertEqual(cycle.call_count, 1)
            self.assertEqual(app.selectbox(key="forecast_id").value, "generated-2")
            app.button(key="run_forecast_cycle").click().run()
            self.assertEqual(cycle.call_args.kwargs["output"], directory)
            latest_snapshot = Path(app.session_state["cycle_display_directory"])
            self.assertNotEqual(latest_snapshot, snapshot)
            self.assertEqual(latest_snapshot.parent, directory.resolve())
            self.assertEqual(len(load_dashboard(latest_snapshot).forecasts), 192)
            self.assertEqual(len(load_dashboard(snapshot).forecasts), 192)
            self.assertTrue(any("Already published" in item.value for item in app.text))
            another = self.app()
            another.button(key="run_forecast_cycle").click().run()
            self.assertNotEqual(cycle.call_args.kwargs["output"], directory)

    def test_failed_run_preserves_last_successful_chart_and_allows_retry(self):
        with patch("ui.runner.invoke_cycle", side_effect=self.publish) as cycle:
            app = self.app()
            app.button(key="run_forecast_cycle").click().run()
            directory = app.session_state["cycle_display_directory"]
            cycle.side_effect = CycleDisplayError("Weather service unavailable; retry the request.")
            app.button(key="run_forecast_cycle").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["cycle_display_directory"], directory)
            self.assertEqual(app.selectbox(key="forecast_id").value, "generated-2")
            self.assertTrue(any("Weather service unavailable" in item.value for item in app.error))
            cycle.side_effect = self.publish
            app.button(key="run_forecast_cycle").click().run()
            self.assertTrue(app.success)

    def test_offline_option_is_explicit_and_passes_deterministic_controller(self):
        with patch("ui.runner.invoke_cycle", side_effect=self.publish) as cycle:
            app = self.app()
            app.radio(key="cycle_mode").set_value(OFFLINE_MODE).run()
            app.checkbox(key="cycle_include_update").uncheck().run()
            app.button(key="run_forecast_cycle").click().run()
            self.assertFalse(app.exception)
            kwargs = cycle.call_args.kwargs
            self.assertEqual(kwargs["controller"], "deterministic")
            self.assertTrue(kwargs["offline"])
            self.assertFalse(kwargs["include_update"])

    def test_missing_backend_dependencies_and_unexpected_errors_are_safe(self):
        for error in (ImportError("missing backend"), RuntimeError("secret-request-details")):
            with self.subTest(error=type(error).__name__):
                with patch("ui.runner.invoke_cycle", side_effect=error):
                    app = self.app()
                    app.button(key="run_forecast_cycle").click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.selectbox(key="forecast_id").value, "demo-2")
                    messages = " ".join(item.value for item in app.error)
                    self.assertNotIn("secret-request-details", messages)
                    if isinstance(error, ImportError):
                        self.assertIn("requirements-backend.txt", messages)

    def test_invalid_generated_artifacts_do_not_replace_saved_view(self):
        def invalid(**kwargs):
            result = self.publish(**kwargs)
            (Path(result["output_dir"]) / "manifest.json").write_text("{}", encoding="utf-8")
            return result

        with patch("ui.runner.invoke_cycle", side_effect=invalid):
            app = self.app()
            app.button(key="run_forecast_cycle").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="forecast_id").value, "demo-2")
            self.assertTrue(any("manifest.json" in item.value for item in app.error))

    def test_unrelated_output_directory_is_rejected_without_changing_view(self):
        def unrelated(**kwargs):
            return {"output_dir": str(FIXTURE), "forecast_id": "demo-2"}

        with patch("ui.runner.invoke_cycle", side_effect=unrelated):
            app = self.app()
            app.button(key="run_forecast_cycle").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="forecast_id").value, "demo-2")
            self.assertTrue(any("unexpected output directory" in item.value for item in app.error))


if __name__ == "__main__":
    unittest.main()
