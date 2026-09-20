"""视觉模型客户端与分析结果测试（验收 #1/#2/#3/#6 的离线可验证部分）。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

import httpx

from backend.vision.config import load_vision_settings
from backend.vision.prompt_store import PromptStore
from backend.vision.tests.test_video_processor import solid_frame
from backend.vision.vl_analyzer import (
    AccidentAnalysis,
    RemoteVisionClient,
    VLAnalyzer,
    VLUnavailableError,
    normalise_event_type,
    normalise_severity,
    parse_model_json,
)

PROMPTS = PromptStore(Path(__file__).resolve().parents[1] / "prompts")

ACCIDENT_JSON = {
    "accident": True,
    "accident_type": "多车追尾",
    "vehicle_count": 3,
    "severity": "中等",
    "location": "高速超车道",
    "lane": "超车道",
    "actions": ["封闭超车道", "调度拖车"],
    "evidence": "三车首尾相接并停在行车道上",
}


class StubClient:
    """按 Prompt 首行区分任务类型，返回课件第 10 页那种结论。"""

    name = "stub"

    def __init__(self, replies: dict[str, str] | None = None, error: Exception | None = None):
        self.replies = replies or {}
        self.error = error
        self.prompts: list[str] = []

    def complete(self, prompt: str, frame: Any) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        for key, reply in self.replies.items():
            if key in prompt:
                return reply
        return f"模拟描述 {frame.time_label}"


class NormaliseTest(unittest.TestCase):
    def test_severity_aliases(self) -> None:
        self.assertEqual("severe", normalise_severity("严重"))
        self.assertEqual("minor", normalise_severity("Minor"))
        self.assertEqual("none", normalise_severity(""))
        self.assertEqual("medium", normalise_severity("说不准"))

    def test_event_type_aliases(self) -> None:
        self.assertEqual("vehicle_collision", normalise_event_type("三车追尾", True))
        self.assertEqual("vehicle_rollover", normalise_event_type("侧翻", True))
        self.assertEqual("pedestrian_involved", normalise_event_type("碰撞行人", True))
        self.assertEqual("unknown", normalise_event_type("不明物体", True))
        self.assertEqual("none", normalise_event_type("", False))


class ParseModelJsonTest(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual({"a": 1}, parse_model_json('{"a": 1}'))

    def test_markdown_fence_and_trailing_text(self) -> None:
        text = "```json\n{\"accident\": false}\n```\n以上为结论。"
        self.assertEqual({"accident": False}, parse_model_json(text))

    def test_regex_recovery_when_model_uses_prose(self) -> None:
        text = "事故: 是\n事故类型: 追尾\n涉及车辆数: 3\n严重程度: 中等\n位置: 超车道\n建议: 封闭车道、调度拖车"
        payload = parse_model_json(text)
        self.assertTrue(payload["_recovered_by_regex"])
        self.assertEqual("追尾", payload["accident_type"])
        self.assertEqual("3", payload["vehicle_count"])

    def test_garbage_returns_empty(self) -> None:
        self.assertEqual({}, parse_model_json("模型什么都没说"))


class AccidentAnalysisTest(unittest.TestCase):
    def _build(self, payload: dict[str, Any]) -> AccidentAnalysis:
        return AccidentAnalysis.from_model_text(
            json.dumps(payload, ensure_ascii=False), model="stub", latency_ms=12
        )

    def test_structured_fields(self) -> None:
        analysis = self._build(ACCIDENT_JSON)
        self.assertTrue(analysis.accident)
        self.assertEqual("vehicle_collision", analysis.event_type)
        self.assertEqual("medium", analysis.severity)
        self.assertEqual(3, analysis.vehicle_count)
        self.assertEqual(("封闭超车道", "调度拖车"), analysis.actions)
        self.assertTrue(analysis.verified)
        self.assertEqual("alert", analysis.alert_level)

    def test_no_accident_forces_severity_none(self) -> None:
        analysis = self._build({**ACCIDENT_JSON, "accident": False, "severity": "严重"})
        self.assertFalse(analysis.accident)
        self.assertEqual("none", analysis.severity)
        self.assertEqual("ok", analysis.alert_level)

    def test_string_booleans_and_split_actions(self) -> None:
        analysis = self._build({"accident": "是", "accident_type": "剐蹭", "actions": "报警、派拖车"})
        self.assertTrue(analysis.accident)
        self.assertEqual(("报警", "派拖车"), analysis.actions)

    def test_unparseable_output_raises(self) -> None:
        with self.assertRaises(VLUnavailableError):
            AccidentAnalysis.from_model_text("抱歉，我无法判断", model="stub", latency_ms=1)

    def test_unknown_vehicle_count_is_flagged_not_guessed(self) -> None:
        analysis = self._build({**ACCIDENT_JSON, "vehicle_count": "约三辆"})
        self.assertIsNone(analysis.vehicle_count)
        self.assertTrue(any("vehicle_count" in note for note in analysis.notes))


class VLAnalyzerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_vision_settings()
        self.frame = solid_frame((30, 30, 30), index=180, timestamp=18.0)

    def _analyzer(self, **kwargs: Any) -> VLAnalyzer:
        return VLAnalyzer(self.settings, PROMPTS, StubClient(**kwargs))

    def test_describe_scene_uses_scene_prompt(self) -> None:
        analyzer = self._analyzer()
        self.assertIn("00:18", analyzer.describe_scene(self.frame))
        self.assertIn("监控画面", analyzer.prompts.render("scene_description"))

    def test_vqa_question_is_appended(self) -> None:
        stub = StubClient()
        analyzer = VLAnalyzer(self.settings, PROMPTS, stub)
        analyzer.answer_question(self.frame, "画面里有几辆车？")
        self.assertIn("画面里有几辆车？", stub.prompts[-1])

    def test_accident_analysis_from_stub(self) -> None:
        stub = StubClient(replies={"交通监控分析师": json.dumps(ACCIDENT_JSON, ensure_ascii=False)})
        analyzer = VLAnalyzer(self.settings, PROMPTS, stub)
        analysis = analyzer.analyze_accident(self.frame)
        self.assertEqual("vehicle_collision", analysis.event_type)
        self.assertGreaterEqual(analysis.latency_ms, 0)

    def test_narrate_requires_anchor_frame(self) -> None:
        stub = StubClient(replies={"解说员": "视频前段正常，随后出现追尾。"})
        analyzer = VLAnalyzer(self.settings, PROMPTS, stub)
        text = analyzer.narrate("[00:18] 事故:追尾/中等", self.frame)
        self.assertIn("追尾", text)
        self.assertIn("[00:18]", stub.prompts[-1])  # 事实必须进 Prompt
        self.assertIn("00:18", stub.prompts[-1])  # 锚点帧时间也进 Prompt

    def test_alert_uses_analysis_fields(self) -> None:
        reply = json.dumps(
            {"title": "追尾告警", "level": "alert", "summary": "超车道被占", "actions": ["派交警"], "push_text": "请绕行"},
            ensure_ascii=False,
        )
        analyzer = self._analyzer(replies={"告警生成器": reply})
        analysis = AccidentAnalysis.from_model_text(
            json.dumps(ACCIDENT_JSON, ensure_ascii=False), model="stub", latency_ms=1
        )
        alert = analyzer.build_alert(analysis, self.frame)
        self.assertEqual("追尾告警", alert["title"])
        self.assertTrue(alert["verified"])

    def test_alert_falls_back_when_model_fails(self) -> None:
        analyzer = self._analyzer(error=VLUnavailableError("端点超时"))
        analysis = AccidentAnalysis.from_model_text(
            json.dumps(ACCIDENT_JSON, ensure_ascii=False), model="stub", latency_ms=1
        )
        alert = analyzer.build_alert(analysis, self.frame)
        self.assertFalse(alert["verified"])
        self.assertEqual("alert", alert["level"])
        self.assertIn("端点超时", alert["reason"])
        self.assertIn("封闭超车道", alert["actions"])

    def test_client_error_is_recorded_for_health(self) -> None:
        analyzer = VLAnalyzer(self.settings, PROMPTS, client=None)
        with self.assertRaises(VLUnavailableError):
            _ = analyzer.client  # 无密钥 → 构造远程客户端失败
        self.assertIn("API_KEY", analyzer.health()["last_error"])


class RemoteVisionClientTest(unittest.TestCase):
    """用 httpx MockTransport 验证请求体形状，不联网。"""

    def _client(self, handler) -> RemoteVisionClient:
        settings = load_vision_settings()
        values = {
            **{field: getattr(settings, field) for field in settings.__dataclass_fields__},
            "api_key": "sk-test",
            "base_url": "https://example.test/v1",
            "model": "qwen-vl-max",
            "max_retries": 1,
        }
        client = RemoteVisionClient(type(settings)(**values))
        # 只换传输层（离线），认证头仍由被测代码自己装配
        client._http = httpx.Client(
            base_url=client._http.base_url,
            transport=httpx.MockTransport(handler),
            headers=dict(client._http.headers),
        )
        return client

    def test_request_carries_image_and_prompt(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content.decode("utf-8")))
            seen["auth"] = request.headers.get("authorization")
            seen["path"] = request.url.path
            return httpx.Response(200, json={"choices": [{"message": {"content": "路面畅通"}}]})

        client = self._client(handler)
        text = client.complete("描述这张图", solid_frame((1, 2, 3)))
        self.assertEqual("路面畅通", text)
        self.assertEqual("/v1/chat/completions", seen["path"])  # base_url 的 /v1 前缀不能被丢掉
        self.assertEqual("Bearer sk-test", seen["auth"])
        self.assertEqual("qwen-vl-max", seen["model"])
        kinds = [part["type"] for part in seen["messages"][0]["content"]]
        self.assertEqual(["text", "image_url"], kinds)
        self.assertTrue(seen["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_split_content_lists_are_joined(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": [{"text": "车辆"}, {"text": "拥堵"}]}}]}
            )

        self.assertEqual("车辆拥堵", self._client(handler).complete("p", solid_frame((0, 0, 0))))

    def test_empty_choices_raise_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        with self.assertRaises(VLUnavailableError):
            self._client(handler).complete("p", solid_frame((0, 0, 0)))

    def test_retries_then_reports_failure(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(500, json={"error": "boom"})

        with self.assertRaises(VLUnavailableError) as ctx:
            self._client(handler).complete("p", solid_frame((0, 0, 0)))
        self.assertEqual(2, len(attempts))  # 首次 + 1 次重试
        self.assertIn("500", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
