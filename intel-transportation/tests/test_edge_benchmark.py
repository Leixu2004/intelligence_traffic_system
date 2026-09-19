import math
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from edge.benchmark import (
    benchmark_session,
    build_random_inputs,
    normalize_provider_names,
    parse_shape_overrides,
    resolve_input_shape,
    run_benchmark,
    summarize_latencies,
)


class FakeMetadata:
    def __init__(self, name, shape, ort_type="tensor(float)"):
        self.name = name
        self.shape = shape
        self.type = ort_type


class FakeSession:
    def __init__(self, inputs=None):
        self._inputs = inputs or [FakeMetadata("features", ["batch", 6, 6])]
        self.run_calls = 0

    def get_inputs(self):
        return self._inputs

    def get_outputs(self):
        return [FakeMetadata("forecast", ["batch", 1])]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def get_provider_options(self):
        return {"CPUExecutionProvider": {}}

    def run(self, output_names, feeds):
        self.run_calls += 1
        return [np.zeros((next(iter(feeds.values())).shape[0], 1), dtype=np.float32)]


class SequenceClock:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class EdgeBenchmarkTests(unittest.TestCase):
    def test_provider_aliases_expand_and_preserve_priority(self):
        self.assertEqual(
            normalize_provider_names(["tensorrt", "cuda", "cpu", "CPUExecutionProvider"]),
            ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"],
        )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            normalize_provider_names([" "])

    def test_latency_summary_reports_percentiles_and_batch_throughput(self):
        metrics = summarize_latencies([0.001, 0.002, 0.003, 0.004], batch_size=8)

        self.assertEqual(metrics["iterations"], 4)
        self.assertEqual(metrics["avg_ms"], 2.5)
        self.assertEqual(metrics["p50_ms"], 2.5)
        self.assertTrue(math.isclose(metrics["p95_ms"], 3.85))
        self.assertEqual(metrics["runs_per_second"], 400.0)
        self.assertEqual(metrics["fps"], 3200.0)

    def test_benchmark_session_excludes_warmup_and_uses_injected_clock(self):
        session = FakeSession()
        clock = SequenceClock([10.0, 10.001, 20.0, 20.003])

        metrics = benchmark_session(
            session,
            {"features": np.zeros((2, 6, 6), dtype=np.float32)},
            warmup_iterations=2,
            measured_iterations=2,
            batch_size=2,
            clock=clock,
        )

        self.assertEqual(session.run_calls, 4)
        self.assertTrue(math.isclose(metrics["avg_ms"], 2.0, rel_tol=1e-9))
        self.assertTrue(math.isclose(metrics["fps"], 1000.0, rel_tol=1e-9))

    def test_dynamic_yolo_shape_uses_batch_and_image_size(self):
        shape = resolve_input_shape("images", ["batch", 3, "height", "width"], 8, image_size=320)
        self.assertEqual(shape, (8, 3, 320, 320))

    def test_unresolved_non_image_dimension_requires_override(self):
        with self.assertRaisesRegex(ValueError, "provide --shape"):
            resolve_input_shape("tokens", ["batch", "sequence"], 1)

    def test_shape_override_preserves_static_dimensions(self):
        with self.assertRaisesRegex(ValueError, "fixed at 3"):
            resolve_input_shape("images", ["batch", 3, "height", "width"], 1, [1, 1, 640, 640])
        with self.assertRaisesRegex(ValueError, "does not match batch_size"):
            resolve_input_shape("images", ["batch", 3, "height", "width"], 1, [8, 3, 640, 640])

    def test_build_random_inputs_supports_real_lstm_contract(self):
        session = FakeSession([FakeMetadata("features", ["batch", 6, 6])])
        feeds, contracts = build_random_inputs(session, batch_size=4, seed=42)

        self.assertEqual(feeds["features"].shape, (4, 6, 6))
        self.assertEqual(feeds["features"].dtype, np.float32)
        self.assertEqual(contracts[0]["benchmark_shape"], [4, 6, 6])
        np.testing.assert_array_equal(
            feeds["features"],
            build_random_inputs(session, batch_size=4, seed=42)[0]["features"],
        )

    def test_parse_shape_overrides_rejects_duplicates_and_invalid_dimensions(self):
        self.assertEqual(parse_shape_overrides(["images=8,3,640,640"]), {"images": (8, 3, 640, 640)})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_shape_overrides(["images=1,3,640,640", "images=8,3,640,640"])
        with self.assertRaisesRegex(ValueError, "positive"):
            parse_shape_overrides(["images=1,3,0,640"])

    def test_run_benchmark_builds_json_serializable_report(self):
        session = FakeSession()

        def session_factory(model_path, providers, intra_op_threads):
            self.assertEqual(list(providers), ["CPUExecutionProvider"])
            return session, "test-runtime", ["CPUExecutionProvider"]

        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.onnx"
            model.write_bytes(b"test-model")
            report = run_benchmark(
                model,
                batch_size=2,
                warmup_iterations=1,
                measured_iterations=2,
                clock=SequenceClock([1.0, 1.1, 2.0, 2.001, 3.0, 3.002]),
                session_factory=session_factory,
            )

        self.assertEqual(report["runtime"]["active_providers"], ["CPUExecutionProvider"])
        self.assertEqual(report["inputs"][0]["benchmark_shape"], [2, 6, 6])
        self.assertEqual(report["metrics"]["batch_size"], 2)
        self.assertEqual(report["scope"], "model_only_onnxruntime_session_run")
        json.dumps(report)

    def test_run_benchmark_normalizes_provider_alias_before_session_creation(self):
        session = FakeSession()

        def session_factory(model_path, providers, intra_op_threads):
            self.assertEqual(list(providers), ["CPUExecutionProvider"])
            return session, "test-runtime", ["CPUExecutionProvider"]

        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.onnx"
            model.write_bytes(b"test-model")
            report = run_benchmark(
                model,
                providers=["cpu"],
                warmup_iterations=1,
                measured_iterations=1,
                clock=SequenceClock([1.0, 1.1, 2.0, 2.001]),
                session_factory=session_factory,
            )

        self.assertEqual(report["configuration"]["requested_providers"], ["CPUExecutionProvider"])


if __name__ == "__main__":
    unittest.main()
