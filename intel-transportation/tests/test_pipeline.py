import unittest

import numpy as np

from core.pipeline import PlateRecognition


class _Box:
    xyxy = np.array([[10, 5, 90, 35]])
    conf = np.array([0.92])


class _Result:
    boxes = [_Box()]


class _Detector:
    def detect(self, image, conf=None):
        self.conf = conf
        return [_Result()]


class _OCR:
    def read_plate(self, image):
        return "粤B12345", 0.98


class _Validator:
    def is_valid(self, text):
        return text == "粤B12345"


class PlatePipelineTests(unittest.TestCase):
    def test_recognize_runs_full_facade(self):
        detector = _Detector()
        pipeline = PlateRecognition(detector=detector, ocr=_OCR(), validator=_Validator())
        image = np.zeros((40, 100, 3), dtype=np.uint8)

        result = pipeline.recognize(image, apply_warp=False)

        self.assertEqual(detector.conf, 0.5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "粤B12345")
        self.assertTrue(result[0]["is_valid"])
        self.assertEqual(result[0]["plate_type"], "普通车牌")
        self.assertEqual(result[0]["box"], [10, 5, 90, 35])

    def test_recognize_rejects_empty_input(self):
        pipeline = PlateRecognition(detector=_Detector(), ocr=_OCR(), validator=_Validator())
        self.assertEqual(pipeline.recognize(np.array([])), [])


if __name__ == "__main__":
    unittest.main()
