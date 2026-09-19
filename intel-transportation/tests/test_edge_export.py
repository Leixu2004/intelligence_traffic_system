import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edge.export_models import calibration_images, export_yolo


class _FakeYOLO:
    instances = []

    def __init__(self, model_path):
        self.model_path = Path(model_path)
        self.options = None
        type(self).instances.append(self)

    def export(self, **options):
        self.options = options
        output = self.model_path.with_suffix(f".{options['format']}")
        output.write_bytes(b"fake exported artifact")
        return str(output)


class EdgeExportTests(unittest.TestCase):
    def setUp(self):
        _FakeYOLO.instances.clear()
        self.yolo_patch = patch("edge.export_models._get_yolo_class", return_value=_FakeYOLO)
        self.yolo_patch.start()

    def tearDown(self):
        self.yolo_patch.stop()

    def _model(self, directory: str) -> Path:
        model = Path(directory) / "model.pt"
        model.write_bytes(b"placeholder")
        return model

    def test_int8_requires_calibration_data(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "dataset.yaml"):
                export_yolo(self._model(directory), "engine", device="0", int8=True)

    def test_calibration_directory_must_contain_images(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "没有可用图片"):
                calibration_images(directory)

    def test_onnx_fp16_cpu_is_forwarded_as_quantize_16(self):
        with tempfile.TemporaryDirectory() as directory:
            result = export_yolo(self._model(directory), " ONNX ", half=True, device=" CPU ", image_size=320)

            self.assertEqual(result.suffix, ".onnx")
            options = _FakeYOLO.instances[-1].options
            self.assertEqual(options["format"], "onnx")
            self.assertEqual(options["device"], "cpu")
            self.assertEqual(options["imgsz"], 320)
            self.assertTrue(options["dynamic"])
            self.assertEqual(options["quantize"], 16)

    def test_engine_rejects_cpu_even_when_device_is_uppercase(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "TensorRT engine"):
                export_yolo(self._model(directory), "engine", device="CPU")

    def test_int8_yaml_is_forwarded_to_ultralytics(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self._model(directory)
            dataset = Path(directory) / "dataset.YAML"
            dataset.write_text("path: .\ntrain: images\nval: images\nnames: [vehicle]\n", encoding="utf-8")

            result = export_yolo(model, "engine", device="0", int8=True, calibration_data=dataset)

            self.assertEqual(result.suffix, ".engine")
            options = _FakeYOLO.instances[-1].options
            self.assertEqual(options["quantize"], 8)
            self.assertEqual(options["data"], str(dataset.resolve()))
            self.assertFalse(options["dynamic"])

    def test_int8_image_directory_requires_dataset_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            calibration_dir = Path(directory) / "calibration"
            calibration_dir.mkdir()
            (calibration_dir / "frame.JPG").write_bytes(b"not a real image")

            with self.assertRaisesRegex(ValueError, "dataset.yaml"):
                export_yolo(self._model(directory), "engine", device="0", int8=True, calibration_data=calibration_dir)

    def test_calibration_data_without_int8_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "dataset.yaml"
            dataset.write_text("path: .\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "只能与 --int8"):
                export_yolo(self._model(directory), "onnx", calibration_data=dataset)

    def test_simplify_is_rejected_for_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "仅适用于 ONNX"):
                export_yolo(self._model(directory), "engine", device="0", simplify=True)

    def test_invalid_image_size_is_rejected_before_export(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "正整数"):
                export_yolo(self._model(directory), "onnx", image_size=0)

            self.assertEqual(_FakeYOLO.instances, [])

    def test_export_result_must_be_expected_file(self):
        class BadYOLO(_FakeYOLO):
            def export(self, **options):
                self.options = options
                output = self.model_path.with_suffix(".bin")
                output.write_bytes(b"wrong artifact")
                return str(output)

        with tempfile.TemporaryDirectory() as directory:
            with patch("edge.export_models._get_yolo_class", return_value=BadYOLO):
                with self.assertRaisesRegex(RuntimeError, "扩展名错误"):
                    export_yolo(self._model(directory), "onnx")


if __name__ == "__main__":
    unittest.main()
