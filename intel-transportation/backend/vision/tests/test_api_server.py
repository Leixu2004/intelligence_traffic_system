"""HTTP/WebSocket 接口测试（验收 #7：RESTful + WebSocket 可用）。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from backend.vision.api_server import create_app
from backend.vision.multimodal_system import VideoAnalysisSystem
from backend.vision.prompt_store import PromptStore
from backend.vision.service import VisionService
from backend.vision.tests.test_multimodal_system import (
    COLLISION_AT,
    CountingCounter,
    ScriptedClient,
    make_clip,
    settings_for,
)
from backend.vision.vl_analyzer import VLAnalyzer

PROMPTS = PromptStore(Path(__file__).resolve().parents[1] / "prompts")


def jpeg_bytes(color: int = 40) -> bytes:
    array = np.full((60, 80, 3), color, dtype="uint8")
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="JPEG")
    return buffer.getvalue()


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.clip = make_clip(cls.root, seconds=24)
        cls.image = cls.root / "scene.jpg"
        cls.image.write_bytes(jpeg_bytes())

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _client(self, *, enabled: bool = True) -> TestClient:
        settings = settings_for(self.root, enabled=enabled, api_key="sk-test" if enabled else "")
        service: VisionService | None = None
        if enabled:  # 注入假视觉端点，测试不打网络（真密钥走 vision_smoke.py --live）
            analyzer = VLAnalyzer(settings, PROMPTS, ScriptedClient(collision_after=COLLISION_AT))
            system = VideoAnalysisSystem(settings, analyzer, CountingCounter())
            service = VisionService(settings, system)
        return TestClient(create_app(settings, service))

    def test_health_reports_availability(self) -> None:
        with self._client() as client:
            payload: dict[str, Any] = client.get("/api/v1/vision/health").json()["data"]
        self.assertTrue(payload["available"])
        self.assertTrue(payload["runtime"]["opencv"])

    def test_disabled_service_returns_503_with_reason(self) -> None:
        with self._client(enabled=False) as client:
            response = client.post("/api/v1/vision/analyze", files={"file": ("scene.jpg", jpeg_bytes(), "image/jpeg")})
        self.assertEqual(503, response.status_code)
        self.assertIn("未启用", response.json()["detail"])

    def test_analyze_image_returns_description_and_tags(self) -> None:
        with self._client() as client:
            response = client.post(
                "/api/v1/vision/analyze",
                files={"file": ("scene.jpg", jpeg_bytes(), "image/jpeg")},
            )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(200, body["code"])
        data = body["data"]
        self.assertEqual(1, len(data["timeline"]))
        self.assertIn("模拟场景描述", data["timeline"][0]["description"])
        self.assertFalse(data["accident"])
        self.assertTrue(body["timestamp"] > 0)

    def test_analyze_image_with_question(self) -> None:
        with self._client() as client:
            data = client.post(
                "/api/v1/vision/analyze",
                files={"file": ("scene.jpg", jpeg_bytes(), "image/jpeg")},
                data={"question": "有几辆车？"},
            ).json()["data"]
        self.assertEqual("模拟回答：4 辆", data["timeline"][0]["answer"])

    def test_rejects_unsupported_file_type(self) -> None:
        with self._client() as client:
            response = client.post(
                "/api/v1/vision/video",
                files={"file": ("notes.txt", b"hello", "text/plain")},
            )
        self.assertEqual(400, response.status_code)
        self.assertIn("不支持", response.json()["detail"])

    def test_video_endpoint_streams_timeline_and_alerts(self) -> None:
        with self._client() as client:
            data = client.post(
                "/api/v1/vision/video",
                files={"file": ("road.mp4", self.clip.read_bytes(), "video/mp4")},
            ).json()["data"]
        self.assertTrue(data["accident"])
        self.assertGreater(len(data["timeline"]), 1)
        self.assertIn("模拟解说", data["narration"])
        self.assertTrue(data["alerts"])
        self.assertEqual("stub", data["timeline"][0]["vehicle_source"])

    def test_results_reads_history_files(self) -> None:
        with self._client() as client:
            client.post(
                "/api/v1/vision/video",
                files={"file": ("road.mp4", self.clip.read_bytes(), "video/mp4")},
            )
            runs = client.get("/api/v1/vision/results", params={"limit": 5}).json()["data"]
            alerts = client.get("/api/v1/vision/results", params={"alerts_only": True}).json()["data"]
        self.assertTrue(runs["exists"])
        self.assertTrue(runs["items"])
        self.assertIn("accident", runs["items"][-1])
        self.assertTrue(alerts["items"])
        self.assertEqual("alert", alerts["items"][-1]["level"])

    def test_websocket_pushes_frames_then_result(self) -> None:
        with self._client() as client:
            with client.websocket_connect("/api/v1/vision/stream") as socket:
                socket.send_json({"source": str(self.clip)})
                messages: list[dict[str, Any]] = []
                while True:
                    message = socket.receive_json()
                    messages.append(message)
                    if message["type"] in {"result", "error"}:
                        break
        kinds = [message["type"] for message in messages]
        self.assertEqual("start", kinds[0])
        self.assertIn("frame", kinds)
        self.assertEqual("result", kinds[-1])
        self.assertTrue(messages[-1]["data"]["accident"])

    def test_websocket_rejects_missing_source(self) -> None:
        with self._client() as client:
            with client.websocket_connect("/api/v1/vision/stream") as socket:
                socket.send_text("不是 JSON")
                message = socket.receive_json()
        self.assertEqual("error", message["type"])

    def test_health_payload_has_no_credentials(self) -> None:
        with self._client() as client:
            body = client.get("/api/v1/vision/health").json()
        self.assertNotIn("sk-test", json.dumps(body, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
