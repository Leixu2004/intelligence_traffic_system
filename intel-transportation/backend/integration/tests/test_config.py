"""集成层配置：默认关闭、密钥链、数值钳制与非敏感摘要。"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from backend.integration.config import (
    DEFAULT_VLLM_BASE_URL,
    DEFAULT_VLLM_MODEL,
    VLLM_PLACEHOLDER_KEY,
    IntegrationSettings,
    load_integration_settings,
)


def _settings(**overrides: object) -> IntegrationSettings:
    base = load_integration_settings()
    values = {field: getattr(base, field) for field in base.__dataclass_fields__}
    values.update(overrides)
    return IntegrationSettings(**values)


class LoadIntegrationSettingsTest(unittest.TestCase):
    def test_defaults_are_disabled_with_placeholder_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = load_integration_settings()
        self.assertFalse(settings.enabled)
        self.assertEqual(DEFAULT_VLLM_BASE_URL, settings.vllm_base_url)
        self.assertEqual(DEFAULT_VLLM_MODEL, settings.vllm_model)
        # 未配任何密钥时落到 vLLM 占位密钥，配置性判定仍为「已配置」（本地 vLLM 不校验 key）
        self.assertEqual(VLLM_PLACEHOLDER_KEY, settings.vllm_api_key)
        self.assertTrue(settings.vllm_configured)

    def test_key_chain_prefers_vllm_specific_then_agent_then_bailian(self) -> None:
        with patch.dict("os.environ", {"TRAFFIC_AGENT_API_KEY": "sk-agent", "DASHSCOPE_API_KEY": "sk-bailian"}, clear=True):
            self.assertEqual("sk-agent", load_integration_settings().vllm_api_key)
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "sk-bailian"}, clear=True):
            self.assertEqual("sk-bailian", load_integration_settings().vllm_api_key)
        with patch.dict(
            "os.environ",
            {"TRAFFIC_VLLM_API_KEY": "sk-vllm", "TRAFFIC_AGENT_API_KEY": "sk-agent", "DASHSCOPE_API_KEY": "sk-bailian"},
            clear=True,
        ):
            self.assertEqual("sk-vllm", load_integration_settings().vllm_api_key)

    def test_base_url_trims_slash_and_falls_back_when_blank(self) -> None:
        with patch.dict("os.environ", {"TRAFFIC_VLLM_BASE_URL": "http://gpu:8000/v1/"}, clear=True):
            self.assertEqual("http://gpu:8000/v1", load_integration_settings().vllm_base_url)
        with patch.dict("os.environ", {"TRAFFIC_VLLM_BASE_URL": "   "}, clear=True):
            self.assertEqual(DEFAULT_VLLM_BASE_URL, load_integration_settings().vllm_base_url)

    def test_numeric_bounds_are_clamped_not_rejected(self) -> None:
        env = {
            "TRAFFIC_VLLM_TEMPERATURE": "9",
            "TRAFFIC_VLLM_TIMEOUT_SECONDS": "0.1",
            "TRAFFIC_VLLM_MAX_TOKENS": "999999",
            "TRAFFIC_INTEGRATION_FORECAST_STEPS": "0",
            "TRAFFIC_INTEGRATION_CONGESTION_WINDOWS": "-3",
        }
        with patch.dict("os.environ", env, clear=True):
            settings = load_integration_settings()
        self.assertEqual(2.0, settings.temperature)  # 上限
        self.assertEqual(2.0, settings.timeout_seconds)  # 下限
        self.assertEqual(4096, settings.max_output_tokens)  # 上限
        self.assertEqual(1, settings.forecast_steps)  # 下限
        self.assertEqual(1, settings.congestion_windows)  # 下限

    def test_unparsable_numbers_keep_default(self) -> None:
        with patch.dict("os.environ", {"TRAFFIC_VLLM_MAX_RETRIES": "abc", "TRAFFIC_VLLM_TEMPERATURE": "abc"}, clear=True):
            settings = load_integration_settings()
        self.assertEqual(1, settings.max_retries)
        self.assertEqual(0.7, settings.temperature)

    def test_push_log_and_report_paths_follow_output_dir(self) -> None:
        with patch.dict("os.environ", {"TRAFFIC_INTEGRATION_OUTPUT_DIR": "/tmp/vllm-out"}, clear=True):
            settings = load_integration_settings()
        self.assertEqual(Path("/tmp/vllm-out/screen_push.jsonl"), settings.push_path)
        self.assertTrue(str(settings.output_dir).endswith("vllm-out"))

    def test_describe_exposes_no_secret_value(self) -> None:
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "sk-secret-do-not-leak"}, clear=True):
            described = load_integration_settings().describe()
        rendered = repr(described)
        self.assertNotIn("sk-secret-do-not-leak", rendered)
        self.assertEqual("real", described["api_key_kind"])
        self.assertEqual("http_polling", described["screen_transport"])

    def test_vllm_configured_requires_base_url_and_model(self) -> None:
        self.assertFalse(_settings(vllm_base_url="").vllm_configured)
        self.assertFalse(_settings(vllm_model="").vllm_configured)
        self.assertFalse(_settings(vllm_api_key="").vllm_configured)
        self.assertTrue(_settings().vllm_configured)


if __name__ == "__main__":
    unittest.main()
