"""Verify Horizon navigation, forecast selection and disclosures."""
import os
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from ui.data import ROOT


class DesignTests(unittest.TestCase):
    def test_horizon_selection_and_disclosures(self):
        with patch.dict(os.environ, {"CINDRELO_OUTPUT_DIR": "docs/fixtures/dashboard"}):
            app = AppTest.from_file(str(ROOT / "app.py")).run()
            app.selectbox(key="turbine").select("turbine_2").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="turbine").value, "turbine_2")
            self.assertEqual(app.selectbox(key="forecast_id").value, "demo-2")
            self.assertEqual([tab.label for tab in app.tabs], ["Forecast", "Model comparison", "Tool trace"])
            self.assertFalse(any(radio.label in ("Design version", "Workspace") for radio in app.radio))
            self.assertTrue(any("SYNTHETIC DEMO DATA" in item.value for item in app.warning))
            self.assertTrue(any(item.label == "Forecast revision" for item in app.metric))
            app.selectbox(key="forecast_id").select("demo-1").run()
            self.assertIn("No previous overlapping forecast", [item.value for item in app.info])
