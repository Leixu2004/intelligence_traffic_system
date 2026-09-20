"""配置与 Prompt 资产测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.vision.config import (
    DEFAULT_LOCAL_MODEL,
    DEFAULT_VL_MODEL,
    PROMPT_NAMES,
    VisionSettings,
    load_vision_settings,
)
from backend.vision.prompt_store import PromptStore, PromptTemplateError


def _settings(**overrides: object) -> VisionSettings:
    base = load_vision_settings()
    values = {
        **{field: getattr(base, field) for field in base.__dataclass_fields__},
        **overrides,
    }
    return VisionSettings(**values)


class LoadVisionSettingsTest(unittest.TestCase):
    def test_defaults_are_disabled_and_remote(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            settings = load_vision_settings()
        self.assertFalse(settings.enabled)
        self.assertEqual("remote", settings.backend)
        self.assertEqual(DEFAULT_VL_MODEL, settings.model)
        self.assertFalse(settings.vl_configured)  # base_url 有默认值，但没有密钥

    def test_unknown_backend_falls_back_to_remote(self) -> None:
        with patch.dict("os.environ", {"TRAFFIC_VL_BACKEND": "ollama"}, clear=True):
            self.assertEqual("remote", load_vision_settings().backend)

    def test_local_backend_switches_model(self) -> None:
        with patch.dict("os.environ", {"TRAFFIC_VL_BACKEND": "local-transformers"}, clear=True):
            settings = load_vision_settings()
        self.assertEqual(DEFAULT_LOCAL_MODEL, settings.model)
        self.assertEqual(DEFAULT_LOCAL_MODEL, settings.local_model_path)

    def test_api_key_falls_back_to_shared_keys(self) -> None:
        with patch.dict("os.environ", {"DASHSCOPE_API_KEY": "sk-shared"}, clear=True):
            self.assertEqual("sk-shared", load_vision_settings().api_key)

    def test_explicit_base_url_wins(self) -> None:
        env = {"TRAFFIC_VL_BASE_URL": "https://example.test/v1", "TRAFFIC_VL_MODEL": "glm-4v"}
        with patch.dict("os.environ", env, clear=True):
            settings = load_vision_settings()
        self.assertEqual("https://example.test/v1", settings.base_url)
        self.assertEqual("glm-4v", settings.model)

    def test_numbers_are_clamped(self) -> None:
        env = {
            "TRAFFIC_VL_TIMEOUT_SECONDS": "9999",
            "TRAFFIC_VISION_MAX_FRAMES": "0",
            "TRAFFIC_VISION_FRAME_INTERVAL": "abc",
        }
        with patch.dict("os.environ", env, clear=True):
            settings = load_vision_settings()
        self.assertEqual(300.0, settings.timeout_seconds)
        self.assertEqual(1, settings.max_frames_per_video)
        self.assertEqual(2.0, settings.frame_interval_seconds)  # 非法值回默认

    def test_describe_never_leaks_key(self) -> None:
        settings = _settings(api_key="sk-secret-value")
        payload = settings.describe()
        self.assertTrue(payload["api_key_present"])
        self.assertNotIn("sk-secret-value", repr(payload))

    def test_keyframe_filtering_flag(self) -> None:
        self.assertTrue(_settings(keyframe_threshold=0.02).keyframe_filtering)
        self.assertFalse(_settings(keyframe_threshold=0.0).keyframe_filtering)


class PromptStoreTest(unittest.TestCase):
    def test_shipped_prompts_are_complete(self) -> None:
        store = PromptStore(Path(__file__).resolve().parents[1] / "prompts")
        self.assertEqual((), store.missing(PROMPT_NAMES))

    def test_each_prompt_covers_courseware_capability(self) -> None:
        directory = Path(__file__).resolve().parents[1] / "prompts"
        self.assertIn("JSON", (directory / "accident_analysis.txt").read_text(encoding="utf-8"))
        narration = (directory / "narration.txt").read_text(encoding="utf-8")
        self.assertIn("$facts", narration)
        self.assertIn("不要新增", narration)
        self.assertIn("$analysis", (directory / "alert.txt").read_text(encoding="utf-8"))

    def test_render_substitutes_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "greeting.txt"
            path.write_text("你好 $name，事实：\n$facts", encoding="utf-8")
            rendered = PromptStore(Path(tmp)).render("greeting", name="CAM-01", facts="- 00:03 拥堵")
        self.assertIn("CAM-01", rendered)
        self.assertIn("00:03 拥堵", rendered)

    def test_missing_template_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PromptStore(Path(tmp))
            self.assertEqual(("absent",), store.missing(("absent",)))
            with self.assertRaises(PromptTemplateError):
                store.render("absent")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
