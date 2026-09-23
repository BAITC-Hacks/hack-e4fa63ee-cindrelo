"""Verify that different navigation models preserve the forecast context."""
import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from ui.data import ROOT
from ui.designs import DESIGNS


class DesignTests(unittest.TestCase):
    def test_design_switch_preserves_selection_and_disclosures(self):
        with patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": "docs/fixtures/dashboard"}):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
            app.selectbox(key="turbine").select("turbine_2").run()
            for design in DESIGNS:
                with self.subTest(design=design):
                    app.radio(key="design").set_value(design).run()
                    self.assertFalse(app.exception)
                    self.assertEqual(app.selectbox(key="turbine").value, "turbine_2")
                    self.assertEqual(app.selectbox(key="forecast_id").value, "demo-2")
                    self.assertTrue(any("SYNTHETIC DEMO DATA" in item.value for item in app.warning))
                    self.assertTrue(any(item.label == "Forecast revision" for item in app.metric))
            app.radio(key="design").set_value("Control room").run()
            for section in ("Model comparison", "Tool trace", "Forecast"):
                app.radio(key="workspace").set_value(section).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.selectbox(key="turbine").value, "turbine_2")
            app.selectbox(key="forecast_id").select("demo-1").run()
            self.assertIn("No previous overlapping forecast", [item.value for item in app.info])
