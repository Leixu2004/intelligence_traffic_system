import os
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent.config import load_agent_settings


class AgentConfigTests(unittest.TestCase):
    def test_agent_is_disabled_without_key_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_agent_settings()
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.configured)

    def test_limits_are_clamped(self):
        with patch.dict(
            os.environ,
            {
                "TRAFFIC_AGENT_ENABLED": "true",
                "TRAFFIC_AGENT_API_KEY": "secret",
                "TRAFFIC_AGENT_MAX_ITERATIONS": "999",
                "TRAFFIC_AGENT_TIMEOUT_SECONDS": "1",
                "TRAFFIC_AGENT_AUDIT_PATH": "agent-test.jsonl",
            },
            clear=True,
        ):
            settings = load_agent_settings()
        self.assertTrue(settings.configured)
        self.assertEqual(settings.max_iterations, 12)
        self.assertEqual(settings.timeout_seconds, 3.0)
        self.assertEqual(settings.audit_path, Path("agent-test.jsonl").resolve())

    def test_bailian_uses_dashscope_key_and_safe_defaults(self):
        with patch.dict(
            os.environ,
            {
                "TRAFFIC_AGENT_ENABLED": "true",
                "TRAFFIC_AGENT_PROVIDER": "aliyun-bailian",
                "DASHSCOPE_API_KEY": "dashscope-secret",
            },
            clear=True,
        ):
            settings = load_agent_settings()
        self.assertTrue(settings.configured)
        self.assertEqual(settings.provider, "aliyun-bailian")
        self.assertEqual(settings.model, "deepseek-v4-flash-0731")
        self.assertEqual(
            settings.base_url,
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        self.assertEqual(settings.api_key, "dashscope-secret")


if __name__ == "__main__":
    unittest.main()
