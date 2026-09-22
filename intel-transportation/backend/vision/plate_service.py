"""单图车牌取证服务：YOLO 车牌检测 + 透视校正 + PaddleOCR + 号牌校验。

与 core/pipeline.PlateRecognition 的关系：本模块只负责"一张上传图 → 结构化号牌结果"，
并把重依赖（cv2 / ultralytics / PaddleOCR）延迟到首次调用，缺依赖或缺权重时如实降级，
不在进程启动期加载模型，也不把降级结果当成识别成功。

可信度口径：verified=True 仅表示"真实模型链路跑完并给出了框与文本"，
不代表识别正确率——整牌准确率仍需要人工真值集才能核验（见 backend/vision/README.md）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

DEFAULT_MAX_BYTES = 8 * 1024 * 1024
MODEL_HINT = "models/exp-7.pt（车牌检测权重）与 data/paddlex_cache（OCR 模型）"


def _flag(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _decode_image(payload: bytes) -> Any:
    import cv2
    import numpy as np

    buffer = np.frombuffer(payload, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise ValueError("图片无法解码（格式不受支持或文件损坏）")
    return image


def _build_pipeline() -> Any:
    from core.pipeline import PlateRecognition

    return PlateRecognition()


class PlateService:
    def __init__(
        self,
        *,
        enabled: bool = True,
        max_bytes: int = DEFAULT_MAX_BYTES,
        pipeline_factory: Callable[[], Any] | None = None,
        decoder: Callable[[bytes], Any] | None = None,
    ):
        self.enabled = enabled
        self.max_bytes = max_bytes
        self._pipeline_factory = pipeline_factory or _build_pipeline
        self._decode = decoder or _decode_image
        self._pipeline: Any | None = None
        self._load_error: str | None = None if enabled else "车牌取证未启用（TRAFFIC_PLATE_ENABLED=false）"
        self._last_latency_ms = 0.0
        self._call_count = 0

    @classmethod
    def build(cls) -> PlateService:
        return cls(enabled=_flag("TRAFFIC_PLATE_ENABLED", True))

    @property
    def available(self) -> bool:
        return self._load_error is None

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "model_loaded": self._pipeline is not None,
            "error": self._load_error,
            "max_bytes": self.max_bytes,
            "call_count": self._call_count,
            "last_latency_ms": self._last_latency_ms,
            "model_hint": MODEL_HINT,
        }

    def _ensure_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        try:
            self._pipeline = self._pipeline_factory()
        except Exception as exc:  # 缺依赖 / 缺权重 / 模型初始化失败
            self._load_error = f"车牌识别链路初始化失败: {type(exc).__name__}: {exc}"
            raise RuntimeError(self._load_error) from exc
        return self._pipeline

    @staticmethod
    def _strip(plate: dict[str, Any]) -> dict[str, Any]:
        return {
            "text": plate.get("text", ""),
            "ocr_conf": plate.get("conf", 0.0),
            "det_conf": plate.get("det_conf", 0.0),
            "is_valid": bool(plate.get("is_valid")),
            "plate_type": plate.get("plate_type", ""),
            "box": [int(v) for v in (plate.get("box") or [])],
        }

    def recognize(self, filename: str, payload: bytes) -> dict[str, Any]:
        """一张图 → 结构化号牌列表；无牌或解码失败都以 ok + 明确原因返回。"""
        started = time.perf_counter()
        if not self.available:
            raise RuntimeError(self._load_error or "车牌取证不可用")
        if not payload:
            raise ValueError("上传内容为空")
        if len(payload) > self.max_bytes:
            raise ValueError(f"图片超过 {self.max_bytes // (1024 * 1024)} MB 上限")
        try:
            image = self._decode(payload)
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"图片解码失败: {type(exc).__name__}: {exc}") from exc
        plates = [self._strip(item) for item in self._ensure_pipeline().recognize(image)]
        detected = [item for item in plates if item["text"]]
        self._call_count += 1
        self._last_latency_ms = round((time.perf_counter() - started) * 1000, 3)
        height, width = (image.shape[0], image.shape[1]) if hasattr(image, "shape") else (0, 0)
        return {
            "ok": True,
            "filename": Path(filename or "image.jpg").name,
            "image_width": int(width),
            "image_height": int(height),
            "plate_count": len(detected),
            "detected_plate_count": len(plates),
            "plates": plates,
            "source": "core.pipeline.PlateRecognition",
            "verified": True,
            "limitations": [
                "整牌准确率未经人工真值集核验",
                "OCR 与检测置信度不代表识别正确",
            ],
            "elapsed_ms": self._last_latency_ms,
        }


__all__ = ["MODEL_HINT", "PlateService"]
