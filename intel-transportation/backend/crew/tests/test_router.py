"""路由行为：未初始化 503、健康探针、API Key 校验、协作降级映射为 502。"""

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.crew.contracts import CrewRunResult
from backend.crew.router import router

from .support import SAMPLE_EVENT, make_service


def body() -> dict:
    return SAMPLE_EVENT.model_dump(mode="json")


def _client(service=None, api_key: str = "") -> TestClient:
    app = FastAPI()
    if service is not None:
        app.state.crew_service = service
    app.state.api_key = api_key
    app.include_router(router)
    return TestClient(app)


class CrewRouterTests(unittest.TestCase):
    def test_missing_service_returns_503_on_both_endpoints(self):
        client = _client()
        self.assertEqual(client.get("/api/v1/crew/health").status_code, 503)
        self.assertEqual(client.post("/api/v1/crew/emergency/response", json=body()).status_code, 503)

    def test_health_reports_degradation_without_failing(self):
        with TemporaryDirectory() as tmp:
            client = _client(make_service(tmp, TRAFFIC_CREW_ENABLED="false"))
            response = client.get("/api/v1/crew/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["code"], 200)
        self.assertFalse(payload["data"]["available"])
        self.assertIn("TRAFFIC_CREW_ENABLED", payload["data"]["error"])

    def test_query_requires_the_api_key_while_health_stays_open(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp, TRAFFIC_CREW_ENABLED="false")
            client = _client(service, api_key="secret")
            self.assertEqual(client.post("/api/v1/crew/emergency/response", json=body()).status_code, 401)
            self.assertEqual(
                client.post("/api/v1/crew/emergency/response", json=body(), headers={"X-API-Key": "wrong"}).status_code,
                401,
            )
            self.assertEqual(client.get("/api/v1/crew/health", headers={"X-API-Key": "wrong"}).status_code, 200)

    def test_disabled_crew_returns_degraded_result_not_server_error(self):
        with TemporaryDirectory() as tmp:
            client = _client(make_service(tmp, TRAFFIC_CREW_ENABLED="false"))
            response = client.post("/api/v1/crew/emergency/response", json=body())
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertFalse(data["ok"])
        self.assertTrue(data["simulation"])
        self.assertIn("TRAFFIC_CREW_ENABLED", data["degradation"])
        self.assertEqual(data["event"]["event_id"], "EV-TEST-1")

    def test_available_crew_that_fails_mid_run_maps_to_502(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp)
            degraded = CrewRunResult(
                ok=False,
                process=service.settings.process,
                event=SAMPLE_EVENT,
                degradation="多 Agent 协作失败：RuntimeError",
                simulation=True,
            )
            with patch.object(type(service), "respond", return_value=degraded):
                response = _client(service).post("/api/v1/crew/emergency/response", json=body())
        self.assertEqual(response.status_code, 502)
        self.assertIn("协作失败", response.json()["detail"])

    def test_invalid_event_payload_is_rejected_before_the_crew_runs(self):
        with TemporaryDirectory() as tmp:
            client = _client(make_service(tmp))
            payload = body()
            payload["event_id"] = ""
            response = client.post("/api/v1/crew/emergency/response", json=payload)
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
