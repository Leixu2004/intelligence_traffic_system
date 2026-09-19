import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.crew.config import DEFAULT_CONFIG_PATH, load_crew_settings, load_yaml_fallbacks


class CrewConfigTests(unittest.TestCase):
    def test_shipped_config_yaml_is_loaded_as_defaults(self):
        self.assertTrue(DEFAULT_CONFIG_PATH.exists())
        fallbacks = load_yaml_fallbacks(DEFAULT_CONFIG_PATH)
        self.assertEqual(fallbacks["TRAFFIC_CREW_LLM_MODEL"], "deepseek-v4-flash-0731")
        self.assertEqual(fallbacks["TRAFFIC_CREW_PROCESS"], "hierarchical")

    def test_disabled_without_key_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_crew_settings()
        self.assertFalse(settings.enabled)
        self.assertFalse(settings.configured)
        self.assertEqual(settings.api_key, "")

    def test_environment_overrides_config_yaml(self):
        with patch.dict(
            os.environ,
            {
                "TRAFFIC_CREW_ENABLED": "true",
                "TRAFFIC_CREW_LLM_API_KEY": "crew-secret",
                "TRAFFIC_CREW_LLM_MODEL": "env-model",
                "TRAFFIC_CREW_PROCESS": "sequential",
                "TRAFFIC_CREW_VERBOSE": "on",
                "TRAFFIC_CREW_TEMPERATURE": "9",
                "TRAFFIC_CREW_MAX_ITER": "999",
            },
            clear=True,
        ):
            settings = load_crew_settings()
        self.assertTrue(settings.configured)
        self.assertEqual(settings.model, "env-model")
        self.assertEqual(settings.process, "sequential")
        self.assertTrue(settings.verbose)
        # 越界值必须被夹住，不能把 9.0 温度交给模型。
        self.assertEqual(settings.temperature, 1.0)
        self.assertEqual(settings.max_iter, 20)

    def test_bailian_provider_supplies_base_url_model_and_key_fallback(self):
        with patch.dict(
            os.environ,
            {"TRAFFIC_CREW_ENABLED": "true", "TRAFFIC_CREW_PROVIDER": "DashScope", "DASHSCOPE_API_KEY": "dash-secret"},
            clear=True,
        ):
            settings = load_crew_settings()
        self.assertEqual(settings.provider, "dashscope")
        self.assertEqual(settings.model, "deepseek-v4-flash-0731")
        self.assertEqual(settings.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.assertEqual(settings.api_key, "dash-secret")

    def test_generic_provider_does_not_read_dashscope_key(self):
        with patch.dict(
            os.environ,
            {"TRAFFIC_CREW_PROVIDER": "openai-compatible", "DASHSCOPE_API_KEY": "dash-secret", "OPENAI_API_KEY": "oa"},
            clear=True,
        ):
            settings = load_crew_settings()
        self.assertEqual(settings.api_key, "oa")
        self.assertIsNone(settings.base_url)

    def test_invalid_process_and_route_mode_fall_back_to_safe_defaults(self):
        with patch.dict(
            os.environ,
            {"TRAFFIC_CREW_PROCESS": "consensus", "TRAFFIC_CREW_ROUTE_MODE": "google"},
            clear=True,
        ):
            settings = load_crew_settings()
        self.assertEqual(settings.process, "hierarchical")
        self.assertEqual(settings.route_mode, "auto")

    def test_missing_yaml_file_does_not_break_loading(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"TRAFFIC_CREW_CONFIG_PATH": str(Path(tmp) / "absent.yaml")}, clear=True):
                settings = load_crew_settings()
        self.assertEqual(settings.model, "gpt-4o-mini")
        self.assertEqual(settings.process, "hierarchical")


if __name__ == "__main__":
    unittest.main()
