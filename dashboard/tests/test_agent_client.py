import unittest
from unittest.mock import patch

from dashboard.agent_client import get_agent_health, query_assistant


class _Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self.payload


class AgentClientTests(unittest.TestCase):
    @patch("dashboard.agent_client.requests.get")
    def test_health_parses_degraded_state(self, get):
        get.return_value = _Response(
            {"data": {"available": False, "enabled": False, "configured": False}}
        )
        health = get_agent_health("http://api")
        self.assertFalse(health.available)

    @patch("dashboard.agent_client.API_KEY", "secret")
    @patch("dashboard.agent_client.requests.post")
    def test_query_sends_thread_and_auth(self, post):
        post.return_value = _Response(
            {
                "data": {
                    "answer": "回答",
                    "trace_id": "trace-1",
                    "provider": "fake",
                    "model": "fake-model",
                    "tool_evidence": [],
                    "limitations": [],
                }
            }
        )
        reply = query_assistant(
            question="CP-1 流量？",
            thread_id="thread-1",
            checkpoint_id="CP-1",
            api_url="http://api",
        )
        self.assertEqual(reply.answer, "回答")
        self.assertEqual(post.call_args.kwargs["headers"], {"X-API-Key": "secret"})
        self.assertEqual(post.call_args.kwargs["json"]["thread_id"], "thread-1")

    @patch("dashboard.agent_client.requests.post")
    def test_query_surfaces_api_error(self, post):
        post.return_value = _Response({"detail": "Agent 尚未配置"}, status_code=503)
        with self.assertRaisesRegex(RuntimeError, "尚未配置"):
            query_assistant(
                question="流量？",
                thread_id="thread-1",
                api_url="http://api",
            )


if __name__ == "__main__":
    unittest.main()
