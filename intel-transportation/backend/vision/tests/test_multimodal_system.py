"""VideoAnalysisSystem 端到端测试（假视觉客户端 + 合成短片，无密钥无网络）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from backend.vision.config import VisionSettings, load_vision_settings
from backend.vision.demo_clip import generate_demo_video
from backend.vision.multimodal_system import FrameFinding, VehicleCounter, VideoAnalysisSystem
from backend.vision.prompt_store import PromptStore
from backend.vision.video_processor import Frame
from backend.vision.vl_analyzer import VLAnalyzer, VLUnavailableError

PROMPTS = PromptStore(Path(__file__).resolve().parents[1] / "prompts")
COLLISION_AT = 18.0


def _accident_payload(hit: bool) -> str:
    if not hit:
        return json.dumps(
            {"accident": False, "accident_type": "无", "severity": "无", "evidence": "车辆正常行驶"},
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "accident": True,
            "accident_type": "追尾",
            "vehicle_count": 3,
            "severity": "中等",
            "lane": "超车道",
            "actions": ["封闭超车道", "调度拖车"],
            "evidence": "两车首尾接触并停止",
        },
        ensure_ascii=False,
    )


class ScriptedClient:
    """按时间线决定「这一帧有没有事故」，用来验证告警只在异常帧触发。"""

    name = "scripted"

    def __init__(self, *, collision_after: float = COLLISION_AT, fail: bool = False):
        self.collision_after = collision_after
        self.fail = fail
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, prompt: str, frame: Any) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if self.fail:
            raise VLUnavailableError("模拟端点不可用")
        if prompt.startswith("你是交通监控分析师"):
            return _accident_payload(frame.timestamp_seconds >= self.collision_after)
        if "只回答这个问题" in prompt:  # VQA：问题必须随 Prompt 一起送进模型
            return "模拟回答：4 辆"
        if prompt.startswith("你是交通监控视频解说员"):
            return "模拟解说：视频前段正常通行，之后出现异常。"
        if prompt.startswith("你是交通监控告警生成器"):
            return json.dumps(
                {
                    "title": "追尾告警",
                    "level": "alert",
                    "summary": "超车道受阻",
                    "actions": ["派交警"],
                    "push_text": "请绕行",
                },
                ensure_ascii=False,
            )
        return f"模拟场景描述 {frame.time_label}"


class CountingCounter(VehicleCounter):
    def __init__(self) -> None:
        super().__init__()
        self._model = None

    def count(self, frame: Frame) -> tuple[int | None, str]:
        return (4 if frame.timestamp_seconds >= COLLISION_AT else 10), "stub"


def settings_for(directory: Path, **overrides: Any) -> VisionSettings:
    base = load_vision_settings()
    values: dict[str, Any] = {
        **{field: getattr(base, field) for field in base.__dataclass_fields__},
        "enabled": True,
        "api_key": "sk-test",
        "output_dir": directory,
        "frames_dir": directory / "frames",
        "runs_path": directory / "runs.jsonl",
        "alerts_path": directory / "alerts.jsonl",
        **overrides,
    }
    return VisionSettings(**values)


def system_for(directory: Path, client: Any, **overrides: Any) -> VideoAnalysisSystem:
    settings = settings_for(directory, **overrides)
    return VideoAnalysisSystem(settings, analyzer=VLAnalyzer(settings, PROMPTS, client), counter=CountingCounter())


def make_clip(root: Path, seconds: int = 40) -> Path:
    clip = root / "road.mp4"
    generate_demo_video(clip, seconds=seconds, fps=10)
    return clip


class VideoAnalysisTest(unittest.TestCase):
    def test_full_pipeline_on_synthetic_clip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = make_clip(root)
            report = system_for(root, ScriptedClient()).analyze_video(clip).as_dict()

            self.assertGreater(report["extraction"]["frames_extracted"], 0)
            self.assertEqual(report["extraction"]["analyzed_frames"], len(report["timeline"]))
            self.assertTrue(report["accident"])
            self.assertTrue(report["verified"])
            self.assertIn("模拟解说", report["narration"])
            self.assertTrue(report["alerts"])
            for alert in report["alerts"]:  # 告警只应出现在事故时间窗之后
                self.assertGreaterEqual(alert["timestamp_seconds"], COLLISION_AT)
                self.assertEqual("alert", alert["level"])
            self.assertEqual(10, report["timeline"][0]["vehicles"])  # 车辆数来自检测支路
            self.assertEqual("stub", report["timeline"][0]["vehicle_source"])

            runs = (root / "runs.jsonl").read_text(encoding="utf-8").splitlines()
            alerts = (root / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(runs))
            self.assertEqual(len(report["alerts"]), len(alerts))
            self.assertTrue(json.loads(runs[0])["accident"])

    def test_clear_video_produces_no_alert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = make_clip(root, seconds=20)
            report = system_for(root, ScriptedClient(collision_after=9999.0)).analyze_video(clip).as_dict()
            self.assertFalse(report["accident"])
            self.assertEqual([], report["alerts"])
            self.assertFalse((root / "alerts.jsonl").exists())
            self.assertTrue(report["verified"])

    def test_model_failure_degrades_instead_of_faking_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = make_clip(root, seconds=20)
            report = system_for(root, ScriptedClient(fail=True)).analyze_video(clip).as_dict()
            self.assertFalse(report["verified"])
            self.assertFalse(report["accident"])
            self.assertEqual("", report["narration"])
            self.assertTrue(any("模拟端点不可用" in item for item in report["degradations"]))
            for line in report["timeline"]:
                self.assertIsNone(line["accident"])
                self.assertIn("降级", line["error"])

    def test_keyframe_filtering_reduces_model_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = make_clip(root)
            client = ScriptedClient()
            report = system_for(root, client, keyframe_threshold=0.35).analyze_video(clip).as_dict()
            self.assertLess(report["extraction"]["analyzed_frames"], report["extraction"]["frames_extracted"])
            self.assertTrue(any("关键帧筛选跳过" in item for item in report["degradations"]))
            # 每帧两次模型调用（描述 + 事故分析），外加一次解说和每条告警一次
            expected = report["extraction"]["analyzed_frames"] * 2 + 1 + len(report["alerts"])
            self.assertEqual(expected, client.calls)

    def test_directory_of_images_is_analyzed_as_a_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames_dir = root / "frames"
            frames_dir.mkdir()
            for position in range(3):
                array = np.full((40, 60, 3), position * 60, dtype="uint8")
                Image.fromarray(array).save(frames_dir / f"f{position}.jpg")
            report = system_for(root, ScriptedClient(collision_after=99)).analyze_video(frames_dir).as_dict()
            self.assertEqual(3, report["extraction"]["frames_extracted"])
            self.assertTrue(report["verified"])

    def test_missing_video_degrades_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = system_for(root, ScriptedClient()).analyze_video(root / "nope.mp4").as_dict()
            self.assertFalse(report["verified"])
            self.assertEqual([], report["timeline"])
            self.assertTrue(any("抽帧降级" in item for item in report["degradations"]))

    def test_run_returns_json_ready_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = make_clip(root, seconds=12)
            payload = system_for(root, ScriptedClient(collision_after=9999)).run(clip)
            self.assertEqual(str(clip), payload["source"])
            self.assertIn("timeline", payload)

    def test_analyze_image_supports_vqa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "scene.jpg"
            Image.fromarray(np.zeros((40, 60, 3), dtype="uint8")).save(image)
            client = ScriptedClient(collision_after=99)
            result = system_for(root, client).analyze_image(image, question="有几辆车？")
            self.assertEqual(str(image), result["source"])
            self.assertIn("00:00", result["description"])
            self.assertEqual("模拟回答：4 辆", result["answer"])
            self.assertIn("有几辆车？", client.prompts[-1])  # 问题确实进了 Prompt

    def test_subscribers_receive_frames_and_survive_callback_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clip = make_clip(root, seconds=12)
            system = system_for(root, ScriptedClient(collision_after=9999))
            received: list[dict[str, Any]] = []

            def broken(_payload: dict[str, Any]) -> None:
                raise RuntimeError("订阅者炸了")

            system.subscribe(broken)
            system.subscribe(received.append)
            report = system.analyze_video(clip).as_dict()
            self.assertEqual(report["extraction"]["analyzed_frames"], len(received))
            self.assertEqual(report["timeline"][0]["time"], received[0]["time"])

            system.unsubscribe(received.append)
            system.analyze_video(clip)
            self.assertEqual(report["extraction"]["analyzed_frames"], len(received))  # 退订后不再推送


class FrameFindingTest(unittest.TestCase):
    def test_timeline_fact_marks_missing_vehicle_count_as_unknown(self) -> None:
        finding = FrameFinding(index=0, time_label="00:00", timestamp_seconds=0.0, vehicles=None)
        self.assertIn("车辆:未检测", finding.timeline_fact())

    def test_unknown_status_when_no_analysis(self) -> None:
        finding = FrameFinding(index=0, time_label="00:00", timestamp_seconds=0.0)
        self.assertEqual("unknown", finding.as_dict()["status"])


class HealthTest(unittest.TestCase):
    def test_health_reports_dependencies_not_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = system_for(Path(tmp), ScriptedClient()).health()
        self.assertEqual("qwen-vl-max", payload["model"])
        self.assertIn("opencv", payload)
        self.assertEqual([], payload["missing_prompts"])
        self.assertNotIn("sk-test", json.dumps(payload, ensure_ascii=False))

