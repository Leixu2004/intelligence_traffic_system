import tempfile
import unittest
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch

from backend.prediction.lstm import LSTMForecastModel


class LSTMForecastModelTests(unittest.TestCase):
    def test_legacy_constructor_keeps_current_plus_future_contract(self):
        model = LSTMForecastModel(8, 2, 3).eval()
        features = torch.arange(10, dtype=torch.float32).reshape(2, 5, 1)

        output = model(features)

        self.assertEqual(tuple(output.shape), (2, 4))
        torch.testing.assert_close(output[:, 0], features[:, -1, 0])

    def test_teacher_compat_uses_independent_hidden_sizes(self):
        model = LSTMForecastModel.teacher_compat().eval()
        features = torch.randn(3, 6, 4)

        output = model(features)

        self.assertEqual(tuple(output.shape), (3, 1))
        self.assertEqual(len(model.lstm_layers), 2)
        self.assertEqual(model.lstm_layers[0].input_size, 4)
        self.assertEqual(model.lstm_layers[0].hidden_size, 64)
        self.assertEqual(model.lstm_layers[1].input_size, 64)
        self.assertEqual(model.lstm_layers[1].hidden_size, 32)
        self.assertEqual(model.head[0].in_features, 32)
        self.assertEqual(model.head[0].out_features, 16)
        self.assertAlmostEqual(model.output_dropout.p, 0.2)

    def test_teacher_compat_exports_to_onnx(self):
        model = LSTMForecastModel.teacher_compat().eval()
        features = torch.randn(1, 6, 4)

        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "teacher_lstm.onnx"
            torch.onnx.export(
                model,
                features,
                output_path,
                input_names=["features"],
                output_names=["forecast"],
                dynamic_axes={"features": {0: "batch"}, "forecast": {0: "batch"}},
                opset_version=17,
                dynamo=False,
            )

            self.assertGreater(output_path.stat().st_size, 0)
            onnx.checker.check_model(onnx.load(output_path))
            session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
            result = session.run(None, {"features": np.zeros((2, 6, 4), dtype=np.float32)})[0]
            self.assertEqual(tuple(result.shape), (2, 1))

    def test_rejects_wrong_feature_width(self):
        model = LSTMForecastModel.teacher_compat()

        with self.assertRaisesRegex(ValueError, r"\[batch, time, 4\]"):
            model(torch.randn(2, 6, 1))


if __name__ == "__main__":
    unittest.main()
