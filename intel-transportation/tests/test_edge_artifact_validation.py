import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from edge.validate_artifacts import (
    create_manifest,
    main,
    normalize_providers,
    sha256_file,
    validate_artifact,
    validate_artifacts,
)


class EdgeArtifactValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import onnx
            from onnx import TensorProto, helper
        except ImportError:
            cls.model = None
            return
        node = helper.make_node("Identity", ["input"], ["output"])
        graph = helper.make_graph(
            [node],
            "identity",
            [helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, 4, 4])],
            [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 3, 4, 4])],
        )
        cls.model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])

    def _write_model(self, directory):
        if self.model is None:
            self.skipTest("onnx 未安装")
        import onnx

        path = Path(directory) / "identity.onnx"
        onnx.save(self.model, path)
        return path

    def test_normalize_provider_aliases(self):
        self.assertEqual(normalize_providers(["cpu", "CUDAExecutionProvider", "cpu"]), ["CPUExecutionProvider", "CUDAExecutionProvider"])

    def test_validate_model_reports_contract_hash_and_opset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_model(directory)
            report = validate_artifact(path, providers=["CPUExecutionProvider"])
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["opset"]["default"], 18)
            self.assertEqual(report["inputs"][0]["dtype"], "float")
            self.assertEqual(report["inputs"][0]["shape"], [1, 3, 4, 4])
            self.assertEqual(report["sha256"], sha256_file(path))
            self.assertFalse(report["quantization"]["detected"])

    def test_manifest_hash_mismatch_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_model(directory)
            manifest = {"artifacts": [{"path": path.name, "sha256": "0" * 64, "opset": 18}]}
            report = validate_artifact(path, manifest=manifest, providers=["CPUExecutionProvider"])
            self.assertFalse(report["ok"])
            self.assertTrue(any("SHA-256" in error for error in report["errors"]))

    def test_manifest_io_mismatch_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_model(directory)
            manifest = {"artifacts": [{"path": path.name, "inputs": [{"name": "input", "dtype": "float", "shape": [1, 3, 8, 8]}]}]}
            report = validate_artifact(path, manifest=manifest, providers=["CPUExecutionProvider"])
            self.assertFalse(report["ok"])
            self.assertTrue(any("shape 不匹配" in error for error in report["errors"]))

    def test_manifest_generation_and_batch_report_are_json_serializable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_model(directory)
            report = validate_artifacts([path], providers=["CPUExecutionProvider"])
            manifest = create_manifest([path], report["artifacts"])
            encoded = json.dumps(manifest, ensure_ascii=False)
            self.assertIn("manifest_version", encoded)
            self.assertEqual(report["summary"]["total"], 1)

    def test_required_unavailable_provider_fails_without_silent_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_model(directory)
            fake = mock.Mock()
            fake.get_available_providers.return_value = ["CPUExecutionProvider"]
            with mock.patch.dict("sys.modules", {"onnxruntime": fake}):
                report = validate_artifact(path, providers=["CUDAExecutionProvider"], require_provider=True)
            self.assertFalse(report["ok"])
            self.assertTrue(report["provider"]["fallback"])
            self.assertIn("不可用", report["provider"]["error"])

    def test_cli_writes_json_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_model(directory)
            output = Path(directory) / "report.json"
            manifest = Path(directory) / "manifest.json"
            with mock.patch("builtins.print"):
                exit_code = main([str(path), "--json-output", str(output), "--write-manifest", str(manifest), "--pretty"])
            self.assertEqual(exit_code, 0)
            self.assertTrue(output.is_file())
            self.assertTrue(manifest.is_file())
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["ok"])


if __name__ == "__main__":
    unittest.main()
