import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from backend.prediction.evaluate import (
    baseline_forecast,
    compute_regression_metrics,
    evaluate_baseline,
    evaluate_predictions,
)


class RegressionMetricTests(unittest.TestCase):
    def test_metrics_use_original_scale(self):
        metrics = compute_regression_metrics([10, 20, 30], [8, 22, 30])
        self.assertAlmostEqual(metrics["mae"], 4 / 3)
        self.assertAlmostEqual(metrics["rmse"], np.sqrt(8 / 3))
        self.assertAlmostEqual(metrics["wape"], 100 * 4 / 60)
        self.assertAlmostEqual(metrics["r2"], 0.96)

    def test_zero_denominator_constant_target_and_small_sample(self):
        zero_metrics = compute_regression_metrics([0, 0], [1, 1])
        self.assertIsNone(zero_metrics["wape"])
        self.assertEqual(zero_metrics["r2"], 0.0)
        self.assertEqual(compute_regression_metrics([5, 5], [5, 5])["r2"], 1.0)
        self.assertIsNone(compute_regression_metrics([5], [4])["r2"])

    def test_non_finite_pairs_are_excluded(self):
        metrics = compute_regression_metrics([1, np.nan, 3, 4], [2, 2, np.inf, 4])
        self.assertEqual(metrics["valid_count"], 2)
        self.assertEqual(metrics["excluded_count"], 2)
        self.assertAlmostEqual(metrics["mae"], 0.5)


class BaselineForecastTests(unittest.TestCase):
    def test_all_baseline_methods(self):
        windows = np.array([[1, 2, 3], [3, np.nan, 9]], dtype=float)
        np.testing.assert_allclose(baseline_forecast(windows, 2, "last_value"), [[3, 3], [9, 9]])
        np.testing.assert_allclose(
            baseline_forecast(windows, 2, "moving_average", moving_average_window=2),
            [[2.5, 2.5], [6, 6]],
        )
        np.testing.assert_allclose(baseline_forecast([[1, 2, 3]], 2, "linear_trend"), [[4, 5]])

    def test_empty_finite_history_produces_nan(self):
        forecast = baseline_forecast([[np.nan, np.inf]], 2, "last_value")
        self.assertTrue(np.isnan(forecast).all())


class EvaluationArtifactTests(unittest.TestCase):
    def test_evaluate_predictions_writes_complete_artifact_set(self):
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_predictions(
                [[3, 4], [np.nan, 2]],
                [[2, 4], [1, np.inf]],
                directory,
                windows=[[1, 2], [3, 4]],
                model_name="tiny_lstm",
                sample_ids=["a", "b"],
                series_ids=["s1", "s2"],
            )

            paths = {name: Path(path) for name, path in result["paths"].items()}
            self.assertTrue(all(path.exists() for path in paths.values()))
            self.assertEqual(pd.read_csv(paths["predictions"]).shape[0], 4)
            self.assertEqual(pd.read_csv(paths["residuals"]).shape[0], 2)
            self.assertGreater(paths["residual_distribution"].stat().st_size, 1000)

            metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
            self.assertEqual(metrics["valid_count"], 2)
            self.assertEqual(metrics["excluded_count"], 2)
            self.assertEqual(metrics["forecast_steps"], 2)
            self.assertNotIn("NaN", paths["metrics"].read_text(encoding="utf-8"))

    def test_evaluate_baseline_accepts_single_sample_multi_step_target(self):
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_baseline(
                [[1, 2, 3]],
                [4, 5],
                directory,
                method="linear_trend",
            )
            self.assertAlmostEqual(result["metrics"]["mae"], 0.0)
            self.assertEqual(result["metrics"]["forecast_steps"], 2)


if __name__ == "__main__":
    unittest.main()
