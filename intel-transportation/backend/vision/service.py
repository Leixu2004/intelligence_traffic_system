"""多模态服务生命周期：装配依赖、执行分析、失败只降级不抛出。

上层（dashboard_api 与 api_server）只依赖本类，不直接 new VideoAnalysisSystem，
因此密钥缺失、OpenCV 缺失、Prompt 目录不完整都表现为 health() 里的一条可读原因，
而不是大屏 500。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from .config import PROMPT_NAMES, VisionSettings, load_vision_settings
from .multimodal_system import VideoAnalysisSystem
from .prompt_store import PromptStore
from .video_processor import IMAGE_SUFFIXES, VIDEO_SUFFIXES
from .vl_analyzer import VLUnavailableError

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
DEFAULT_RESULT_LIMIT = 50
MAX_RESULT_LIMIT = 500


class VisionService:
    def __init__(self, settings: VisionSettings, system: VideoAnalysisSystem | None = None):
        self.settings = settings
        self._system = system
        self._load_error: str | None = None
        if not settings.enabled:
            self._load_error = "多模态分析未启用（TRAFFIC_VISION_ENABLED=false）"
        elif not settings.vl_configured:
            self._load_error = "缺少视觉模型配置（TRAFFIC_VL_API_KEY / TRAFFIC_VL_BASE_URL）"
        else:
            missing = PromptStore(settings.prompts_dir).missing(PROMPT_NAMES)
            if missing:
                self._load_error = f"Prompt 模板缺失: {', '.join(missing)}"

    @classmethod
    def build(cls, settings: VisionSettings | None = None) -> VisionService:
        resolved = settings or load_vision_settings()
        return cls(resolved)

    @property
    def available(self) -> bool:
        return self._load_error is None

    @property
    def system(self) -> VideoAnalysisSystem:
        if self._system is None:
            self._system = VideoAnalysisSystem(self.settings)
        return self._system

    def health(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "enabled": self.settings.enabled,
            "available": self.available,
            "error": self._load_error,
            **self.settings.describe(),
        }
        if self._system is not None or self.available:
            try:
                summary["runtime"] = self.system.health()
            except VLUnavailableError as exc:  # pragma: no cover - 只有本机推理路径会抛
                summary["runtime_error"] = str(exc)
        return summary

    def analyze_upload(self, filename: str, payload: bytes) -> dict[str, Any]:
        """接收上传的图片/视频：落盘后按类型分派。"""
        self._check_payload(filename, payload)
        target = self._stage(filename, payload)
        if target.suffix.lower() in IMAGE_SUFFIXES:
            return self._single(self.system.analyze_image(target))
        return self.system.analyze_video(target).as_dict()

    def analyze_source(self, source: str | Path) -> dict[str, Any]:
        """分析服务器本地已有的视频/图片目录（演示与验收、WebSocket 推流用）。"""
        path = Path(source)
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return self._single(self.system.analyze_image(path))
        return self.system.analyze_video(path).as_dict()

    def answer(self, filename: str, payload: bytes, question: str) -> dict[str, Any]:
        self._check_payload(filename, payload)
        return self._single(self.system.analyze_image(self._stage(filename, payload), question=question))

    def _single(self, result: dict[str, Any]) -> dict[str, Any]:
        """把单图结果整形成与视频报告同构的响应，前端只需认一种结构。"""
        error = str(result.get("error") or "")
        return {
            "source": str(result.get("source") or ""),
            "model": self.system.analyzer.model_label,
            "extraction": {"frames_extracted": 1, "keyframes": 1, "analyzed_frames": 1},
            "timeline": [result],
            "narration": "",
            "alerts": [],
            "degradations": [error] if error else [],
            "accident": bool((result.get("accident") or {}).get("accident")),
            "verified": bool((result.get("accident") or {}).get("verified")),
            "elapsed_ms": int(result.get("elapsed_ms") or 0),
        }

    def results(self, *, limit: int = DEFAULT_RESULT_LIMIT, alerts_only: bool = False) -> dict[str, Any]:
        """读回 alerts.jsonl / runs.jsonl，作为「历史分析结果」查询口。"""
        path = self.settings.alerts_path if alerts_only else self.settings.runs_path
        return {
            "source": str(path),
            "exists": path.is_file(),
            "items": _tail_jsonl(path, limit),
            "limit": min(max(limit, 1), MAX_RESULT_LIMIT),
        }

    def _check_payload(self, filename: str, payload: bytes) -> None:
        suffix = Path(filename or "").suffix.lower()
        allowed = set(IMAGE_SUFFIXES) | set(VIDEO_SUFFIXES)
        if suffix not in allowed:
            raise ValueError(f"不支持的文件类型: {suffix or '(无扩展名)'}")
        if not payload:
            raise ValueError("上传内容为空")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValueError(f"文件过大（上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB）")

    def _stage(self, filename: str, payload: bytes) -> Path:
        """写到 output_dir/uploads，文件名只保留安全字符，避免路径穿越。"""
        directory = self.settings.output_dir / "uploads"
        directory.mkdir(parents=True, exist_ok=True)
        safe = Path(filename).name.replace("\\", "_").replace("/", "_")
        target = directory / f"{int(time.time() * 1000)}_{safe}"
        target.write_bytes(payload)
        return target


def _tail_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tail: Iterable[str] = lines[-min(max(limit, 1), MAX_RESULT_LIMIT) :]
    items: list[dict[str, Any]] = []
    for line in tail:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            items.append({"unparseable": line[:200]})
    return items
