"""Router behaviour tests: disabled-state health, 503 query, API key enforcement."""

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app(service, api_key: str = "") -> TestClient:
    from backend.rag.router import router

    app = FastAPI()
    app.state.rag_service = service
    app.state.api_key = api_key
    app.include_router(router)
    return TestClient(app)


class RagRouterDisabledStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(
            os.environ,
            {
                "TRAFFIC_RAG_ENABLED": "false",
                "TRAFFIC_RAG_MILVUS_URI": os.path.join(self._tmp.name, "unused.db"),
                "TRAFFIC_RAG_REGISTRY_PATH": os.path.join(self._tmp.name, "registry.json"),
                "TRAFFIC_RAG_AUDIT_PATH": os.path.join(self._tmp.name, "audit.jsonl"),
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        from backend.rag import RagService, load_rag_settings

        self.service = RagService(load_rag_settings())
        self.addCleanup(self.service.close)
        self.client = _build_app(self.service)

    def test_health_ok_when_disabled(self):
        response = self.client.get("/api/v1/rag/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["code"], 200)
        self.assertFalse(body["data"]["available"])
        self.assertIn("TRAFFIC_RAG_ENABLED", body["data"]["error"])

    def test_query_returns_503_when_disabled(self):
        response = self.client.post(
            "/api/v1/rag/query", json={"question": "酒驾怎么处罚"}
        )
        self.assertEqual(response.status_code, 503)

    def test_rebuild_requires_api_key_when_configured(self):
        secured = _build_app(self.service, api_key="secret-key")
        response = secured.post("/api/v1/rag/rebuild", json={})
        self.assertEqual(response.status_code, 401)
        authorized = secured.post(
            "/api/v1/rag/rebuild", json={}, headers={"X-API-Key": "secret-key"}
        )
        self.assertEqual(authorized.status_code, 503)


if __name__ == "__main__":
    unittest.main()
