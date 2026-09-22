"""PlateService 与 /api/v1/vision/plate 端点：不加载真实模型也能验证行为口径。"""

import unittest

from backend.vision.api_server import create_app
from backend.vision.plate_service import PlateService

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - fastapi 缺失时整目录都跑不了
    TestClient = None


class _Image:
    shape = (480, 640, 3)


class _Pipeline:
    def __init__(self, plates=None, error=None):
        self.plates = plates if plates is not None else []
        self.error = error
        self.seen = []

    def recognize(self, image, apply_warp=True):
        self.seen.append(image)
        if self.error:
            raise self.error
        return [
            {
                "box": [10, 20, 110, 60],
                "det_conf": 0.91,
                "roi": object(),
                "text": item.get("text", ""),
                "conf": item.get("conf", 0.0),
                "is_valid": item.get("is_valid", False),
                "plate_type": item.get("plate_type", "普通车牌"),
            }
            for item in self.plates
        ]


def _service(pipeline=None, **overrides):
    options = {
        "pipeline_factory": lambda: pipeline or _Pipeline(),
        "decoder": lambda payload: _Image(),
    }
    options.update(overrides)
    return PlateService(**options)


class PlateServiceTests(unittest.TestCase):
    def test_disabled_service_reports_reason_without_loading_models(self):
        service = _service(enabled=False)
        self.assertFalse(service.available)
        self.assertIn("未启用", service.health()["error"])
        with self.assertRaises(RuntimeError):
            service.recognize("car.jpg", b"x")

    def test_recognize_strips_roi_and_marks_verified(self):
        pipeline = _Pipeline([{"text": "粤A12345", "conf": 0.87, "is_valid": True}])
        result = _service(pipeline).recognize("car.jpg", b"fake-jpeg")
        self.assertTrue(result["ok"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["plate_count"], 1)
        self.assertEqual(result["plates"][0]["text"], "粤A12345")
        self.assertNotIn("roi", result["plates"][0])
        self.assertEqual(result["image_width"], 640)
        self.assertTrue(result["limitations"])

    def test_empty_and_oversized_payloads_are_rejected_before_model_call(self):
        pipeline = _Pipeline()
        service = _service(pipeline)
        with self.assertRaises(ValueError):
            service.recognize("car.jpg", b"")
        with self.assertRaises(ValueError):
            service.recognize("car.jpg", b"0" * (service.max_bytes + 1))
        self.assertEqual(pipeline.seen, [])

    def test_pipeline_failure_is_reported_as_initialisation_error(self):
        service = _service(
            pipeline_factory=lambda: (_ for _ in ()).throw(ImportError("no ultralytics"))
        )
        with self.assertRaises(RuntimeError) as raised:
            service.recognize("car.jpg", b"fake-jpeg")
        self.assertIn("初始化失败", str(raised.exception))
        self.assertFalse(service.available)


@unittest.skipIf(TestClient is None, "fastapi/httpx 未安装")
class PlateEndpointTests(unittest.TestCase):
    def test_plate_endpoint_returns_structured_result(self):
        service = _service(_Pipeline([{"text": "粤B67890", "conf": 0.79, "is_valid": True}]))
        with TestClient(create_app(plate=service)) as client:
            response = client.post(
                "/api/v1/vision/plate",
                files={"file": ("car.jpg", b"fake-jpeg", "image/jpeg")},
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["plates"][0]["text"], "粤B67890")
        self.assertTrue(data["verified"])

    def test_plate_endpoint_degrades_to_503_when_unavailable(self):
        with TestClient(create_app(plate=_service(enabled=False))) as client:
            health = client.get("/api/v1/vision/plate/health").json()
            response = client.post(
                "/api/v1/vision/plate",
                files={"file": ("car.jpg", b"fake-jpeg", "image/jpeg")},
            )
        self.assertFalse(health["data"]["available"])
        self.assertEqual(response.status_code, 503)
        self.assertIn("未启用", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
