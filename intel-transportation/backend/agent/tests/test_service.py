import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.agent.config import AgentSettings
from backend.agent.service import AgentUnavailableError, TrafficAgentService
from backend.agent.tools import TrafficToolGateway


class _Message:
    content = "基于项目数据的测试回答"


class _FakeAgent:
    def invoke(self, payload, config):
        return {"messages": [_Message()]}


class TrafficAgentServiceTests(unittest.TestCase):
    def _gateway(self):
        return TrafficToolGateway(
            traffic_records=lambda checkpoint_id, limit: ([], "TimescaleDB", ""),
            detection_records=lambda checkpoint_id, limit: ([], "TimescaleDB", ""),
            predict_checkpoint=lambda checkpoint_id, steps: {},
        )

    def test_disabled_agent_is_degraded_not_crashed(self):
        with TemporaryDirectory() as directory:
            service = TrafficAgentService(
                AgentSettings(
                    enabled=False,
                    provider="test",
                    model="test-model",
                    api_key="",
                    base_url=None,
                    temperature=0.0,
                    timeout_seconds=3.0,
                    max_retries=0,
                    max_iterations=2,
                    audit_path=Path(directory) / "audit.jsonl",
                ),
                self._gateway(),
            )
            self.assertFalse(service.health()["available"])
            with self.assertRaises(AgentUnavailableError):
                service.query(question="流量？", thread_id="thread-1")

    def test_successful_query_returns_trace_and_audits_without_reasoning(self):
        with TemporaryDirectory() as directory:
            audit_path = Path(directory) / "audit.jsonl"
            service = TrafficAgentService(
                AgentSettings(
                    enabled=False,
                    provider="fake",
                    model="fake-model",
                    api_key="",
                    base_url=None,
                    temperature=0.0,
                    timeout_seconds=3.0,
                    max_retries=0,
                    max_iterations=2,
                    audit_path=audit_path,
                ),
                self._gateway(),
            )
            service._agent = _FakeAgent()
            result = service.query(
                question="查询 CP-1",
                thread_id="thread-1",
                checkpoint_id="CP-1",
            )
            self.assertEqual(result.answer, "基于项目数据的测试回答")
            self.assertTrue(result.trace_id)
            audit = audit_path.read_text(encoding="utf-8")
            self.assertIn('"status":"success"', audit)
            self.assertNotIn("reasoning", audit.lower())


if __name__ == "__main__":
    unittest.main()
