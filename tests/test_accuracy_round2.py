import numpy as np
import pandas as pd
import pytest

from src.accuracy_round2 import weighted_quantile, smooth_curve


def test_weighted_quantile_handles_unsorted_values_and_rejects_invalid_weights():
    assert weighted_quantile([1., 0.], [1., 9.]) == 0.
    for weights in [[0., 0.], [-1., 2.], [np.nan, 1.]]:
        with pytest.raises(ValueError):
            weighted_quantile([0., 1.], weights)


def test_kernel_prediction_preserves_row_order_and_turbine_identity():
    train = pd.DataFrame({"turbine_id": ["a"]*4+["b"]*4,
                          "wind_speed_100m": [2., 4., 6., 8.]*2,
                          "power_normalized": [.1]*4+[.9]*4,
                          "issued_at": pd.to_datetime(["2025-09-01T00:00Z"]*8)})
    test = pd.DataFrame({"turbine_id": ["b", "a", "b"], "wind_speed_100m": [5., 5., 30.]})
    np.testing.assert_allclose(smooth_curve(train, test, 1.), [.9, .1, .9])


def test_evaluator_passes_complete_local_month(monkeypatch):
    from src import accuracy_round2
    captured = {}
    def fake_predictions(hourly, weather, start, end):
        captured.update(start=start, end=end)
        return pd.DataFrame({"actual": [.2]}), {"curve": np.array([.3])}, {}
    monkeypatch.setattr(accuracy_round2, "predictions", fake_predictions)
    accuracy_round2.evaluate(None, None, "2026-01-01")
    assert captured["start"] == pd.Timestamp("2025-12-31T19:00Z")
    assert captured["end"] == pd.Timestamp("2026-01-31T19:00Z")
