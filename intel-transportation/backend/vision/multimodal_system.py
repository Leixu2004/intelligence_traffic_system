"""多模态视频分析主程序（课件提交物 multimodal_system.py 的 VideoAnalysisSystem）。

流水线与课件第 7 页一致：
    抽帧 → 关键帧筛选 → 车辆计数(YOLO) + Qwen-VL 场景描述/事故分析 → 融合解说 → 告警

与课件示例的两处有意差异（README 里也写了）：
1. 模型不写死 Qwen2VLForConditionalGeneration，而是注入 VLAnalyzer，remote 端点、本机
   transformers 与 Mock 端点都能跑；
2. 任一外部能力缺失（无 OpenCV / 无密钥 / YOLO 未装 / 输出不可解析）时只降级并标注
   verified=false，不会用模板文本冒充模型输出，也不会把「没数」写成 0 辆。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import PROMPT_NAMES, VisionSettings, load_vision_settings
from .prompt_store import PromptStore
from .video_processor import (
    Frame,
    VideoDecodeUnavailable,
    extract_frames,
    frames_from_directory,
    load_image,
    opencv_available,
    select_keyframes,
    summarize_extraction,
)
from .vl_analyzer import AccidentAnalysis, VLAnalyzer, VLUnavailableError

LOGGER = logging.getLogger(__name__)

COCO_VEHICLE_CLASSES = (2, 3, 5, 7)  # car, motorcycle, bus, truck
DEFAULT_CONFIDENCE = 0.35


class VehicleCounter:
    """YOLOv8 车辆计数（课件第 7 页的检测支路）。

    ultralytics 是可选依赖，缺失时 count 返回 (None, "unavailable")：0 会被读成
    「画面里没有车」，所以未检测必须留空而不是补零。
    """

    def __init__(self, model_path: str | Path | None = None):
        self.model_path = str(model_path) if model_path else "yolov8n.pt"
        self._model = None
        self._error = ""
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            self._error = f"ultralytics 未安装：{exc}"
            return
        try:
            self._model = YOLO(self.model_path)
        except Exception as exc:  # noqa: BLE001 - 权重缺失/损坏都要能读出来
            self._error = f"加载 {self.model_path} 失败：{type(exc).__name__}: {exc}"

    @property
    def available(self) -> bool:
        return self._model is not None

    @property
    def error(self) -> str:
        return self._error

    def count(self, frame: Frame) -> tuple[int | None, str]:
        if self._model is None:
            return None, "unavailable"
        try:
            results = self._model.predict(frame.resized(1024), verbose=False, conf=DEFAULT_CONFIDENCE)
        except Exception as exc:  # noqa: BLE001 - 单帧推理失败不应中断整段视频
            LOGGER.warning("YOLO 推理失败: %s", exc)
            return None, "error"
        boxes = getattr(results[0], "boxes", None)
        if boxes is None:
            return None, "error"
        vehicles = sum(1 for cls in boxes.cls.tolist() if int(cls) in COCO_VEHICLE_CLASSES)
        return vehicles, "yolov8"


@dataclass
class FrameFinding:
    """单帧分析结果，对应课件第 10 页时间线的一行。"""

    index: int
    time_label: str
    timestamp_seconds: float
    vehicles: int | None = None
    vehicle_source: str = "unavailable"
    description: str = ""
    analysis: AccidentAnalysis | None = None
    error: str = ""

    def _status(self) -> str:
        if self.analysis is None:
            return "unknown"
        return self.analysis.alert_level

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "time": self.time_label,
            "timestamp_seconds": self.timestamp_seconds,
            "vehicles": self.vehicles,
            "vehicle_source": self.vehicle_source,
            "description": self.description,
            "status": self._status(),
            "accident": self.analysis.as_dict() if self.analysis else None,
            "error": self.error,
        }

    def timeline_fact(self) -> str:
        """喂给解说 Prompt 的一行客观事实。"""
        parts = [f"[{self.time_label}]"]
        parts.append(f"车辆:{self.vehicles}辆" if self.vehicles is not None else "车辆:未检测")
        if self.analysis is not None and self.analysis.accident:
            label = self.analysis.accident_type_text or self.analysis.event_type
            parts.append(f"事故:{label}/{self.analysis.severity}")
        elif self.analysis is not None:
            parts.append("状态:正常通行")
        if self.description:
            parts.append(f"画面:{self.description[:80]}")
        if self.error:
            parts.append(f"降级:{self.error[:60]}")
        return " | ".join(parts)


@dataclass
class VideoReport:
    source: str
    extraction: dict[str, Any] = field(default_factory=dict)
    findings: list[FrameFinding] = field(default_factory=list)
    narration: str = ""
    alerts: list[dict[str, Any]] = field(default_factory=list)
    degradations: list[str] = field(default_factory=list)
    model: str = ""
    elapsed_ms: int = 0
    verified: bool = False

    def accident_found(self) -> bool:
        return any(f.analysis is not None and f.analysis.accident for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model": self.model,
            "extraction": self.extraction,
            "timeline": [f.as_dict() for f in self.findings],
            "narration": self.narration,
            "alerts": self.alerts,
            "degradations": self.degradations,
            "accident": self.accident_found(),
            "verified": self.verified,
            "elapsed_ms": self.elapsed_ms,
        }


class VideoAnalysisSystem:
    """课件第 9 页要求的系统主程序：视频 → 分析 → 解说/告警。"""

    def __init__(
        self,
        settings: VisionSettings | None = None,
        analyzer: VLAnalyzer | None = None,
        counter: VehicleCounter | None = None,
    ):
        self.settings = settings or load_vision_settings()
        self.prompts = PromptStore(self.settings.prompts_dir)
        self._analyzer = analyzer
        self.counter = counter if counter is not None else VehicleCounter()
        self._subscribers: list[Any] = []

    @property
    def analyzer(self) -> VLAnalyzer:
        if self._analyzer is None:
            self._analyzer = VLAnalyzer(self.settings, self.prompts)
        return self._analyzer

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.settings.backend,
            "model": self.settings.model,
            "vl_configured": self.settings.vl_configured,
            "opencv": opencv_available(),
            "vehicle_counter_available": self.counter.available,
            "vehicle_counter_error": self.counter.error,
            "missing_prompts": list(self.prompts.missing(PROMPT_NAMES)),
            "keyframe_threshold": self.settings.keyframe_threshold,
        }

    def analyze_image(self, path: str | Path, *, question: str = "") -> dict[str, Any]:
        """图片描述 + VQA（课件第 6 页能力）。"""
        frame = load_image(path)
        finding = self.analyze_frame(frame)
        result: dict[str, Any] = finding.as_dict()
        result["source"] = str(path)
        if question:
            try:
                result["answer"] = self.analyzer.answer_question(frame, question)
            except VLUnavailableError as exc:
                result["answer"] = ""
                result["error"] = str(exc)
        return result

    def analyze_frame(self, frame: Frame) -> FrameFinding:
        vehicles, vehicle_source = self.counter.count(frame)
        finding = FrameFinding(
            index=frame.index,
            time_label=frame.time_label,
            timestamp_seconds=frame.timestamp_seconds,
            vehicles=vehicles,
            vehicle_source=vehicle_source,
        )
        errors: list[str] = []
        try:
            finding.description = self.analyzer.describe_scene(frame)
        except VLUnavailableError as exc:
            errors.append(f"场景描述降级：{exc}")
        try:
            finding.analysis = self.analyzer.analyze_accident(frame)
        except VLUnavailableError as exc:
            errors.append(f"事故分析降级：{exc}")
        finding.error = "；".join(errors)
        return finding

    def analyze_video(self, video_path: str | Path) -> VideoReport:
        started = time.perf_counter()
        report = VideoReport(source=str(video_path), model=self.analyzer.model_label)
        frames, error = self._load_frames(video_path)
        if error:
            report.degradations.append(error)
            report.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return report
        keyframes, dropped = select_keyframes(frames, self.settings.keyframe_threshold)
        report.extraction = summarize_extraction(
            source=str(video_path),
            all_frames=frames,
            keyframes=keyframes,
            interval=self.settings.frame_interval_seconds,
            keyframe_threshold=self.settings.keyframe_threshold,
        )
        report.extraction["analyzed_frames"] = len(keyframes)
        for frame in keyframes:
            finding = self.analyze_frame(frame)
            report.findings.append(finding)
            self.publish(finding)
        if dropped:
            report.degradations.append(
                f"关键帧筛选跳过 {len(dropped)} 帧（变化占比低于 {self.settings.keyframe_threshold}）"
            )
        self._finish(report, anchor=keyframes[-1] if keyframes else None)
        report.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return report

    def run(self, video_path: str | Path) -> dict[str, Any]:
        """课件 run(video_path) 的等价入口，返回可直接 JSON 化的字典。"""
        return self.analyze_video(video_path).as_dict()

    def _load_frames(self, video_path: str | Path) -> tuple[list[Frame], str]:
        """视频优先；传入图片目录或没有 OpenCV 时退化为图片序列，链路不中断。"""
        path = Path(video_path)
        try:
            if path.is_dir():
                return frames_from_directory(path, max_frames=self.settings.max_frames_per_video), ""
            frames = extract_frames(
                path,
                interval=self.settings.frame_interval_seconds,
                max_frames=self.settings.max_frames_per_video,
            )
            return frames, ""
        except VideoDecodeUnavailable as exc:
            return [], f"抽帧降级：{exc}"

    def _finish(self, report: VideoReport, *, anchor: Frame | None) -> None:
        report.verified = bool(report.findings) and all(
            f.analysis is not None and f.analysis.verified for f in report.findings
        )
        if anchor is not None:
            try:
                report.narration = self.analyzer.narrate(self._facts(report.findings), anchor)
            except VLUnavailableError as exc:
                report.degradations.append(f"智能解说降级：{exc}")
            self._collect_alerts(report, anchor)
        self._write_runs(report)

    def _collect_alerts(self, report: VideoReport, anchor: Frame) -> None:
        for finding in report.findings:
            if finding.analysis is None or not finding.analysis.accident:
                continue
            alert = self.analyzer.build_alert(finding.analysis, anchor)
            alert.update(
                {
                    "source": report.source,
                    "time": finding.time_label,
                    "timestamp_seconds": finding.timestamp_seconds,
                    "frame_index": finding.index,
                    "vehicles": finding.vehicles,
                    "vehicle_source": finding.vehicle_source,
                    "model": report.model,
                }
            )
            report.alerts.append(alert)
            self.send_alert(alert)

    def _facts(self, findings: Iterable[FrameFinding]) -> str:
        return "\n".join(finding.timeline_fact() for finding in findings)

    # ---------- 输出侧：落盘 + 订阅者（WebSocket 推送用） ----------

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """用 == 而不是 is 比较：list.append 这类绑定方法每次都生成新对象。"""
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    def publish(self, finding: FrameFinding) -> None:
        """把单帧结果推给订阅者；订阅者异常不能中断整段分析。"""
        payload = finding.as_dict()
        for callback in list(self._subscribers):
            try:
                callback(payload)
            except Exception as exc:  # noqa: BLE001 - 推送失败只记日志
                LOGGER.warning("实时推送失败: %s", exc)

    def send_alert(self, alert: dict[str, Any]) -> Path:
        """追加写 alerts.jsonl，供大屏/告警面板消费（课件的「告警已推送」）。"""
        path = self.settings.alerts_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(alert, ensure_ascii=False) + "\n")
        return path

    def _write_runs(self, report: VideoReport) -> None:
        path = self.settings.runs_path
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = {
            "source": report.source,
            "model": report.model,
            "extraction": report.extraction,
            "frames_analyzed": len(report.findings),
            "accident": report.accident_found(),
            "degradations": report.degradations,
            "verified": report.verified,
            "elapsed_ms": report.elapsed_ms,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, ensure_ascii=False) + "\n")


def save_frames(frames: Iterable[Frame], directory: Path, *, quality: int = 85) -> list[Path]:
    """把关键帧存成图片，供 README 截图与无 OpenCV 复跑使用。"""
    directory.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for frame in frames:
        path = directory / f"frame_{frame.index:05d}_{frame.time_label.replace(':', 'm')}.jpg"
        path.write_bytes(frame.to_jpeg(quality=quality))
        saved.append(path)
    return saved
