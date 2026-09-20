"""多模态理解子系统（9/18 课件）：Qwen-VL 图文理解 + 监控视频智能解说。

链路：视频抽帧 → 关键帧筛选 → Qwen-VL 场景描述/事故分析 → 结构化标签 + 自然语言解说 + 告警。
与 backend/crew、backend/rag 一致：全部由环境变量开关控制，缺依赖或缺密钥时诚实降级，
不伪造模型输出。
"""

from __future__ import annotations

from typing import Any

from .config import PROMPT_NAMES, VisionSettings, load_vision_settings

__all__ = [
    "PROMPT_NAMES",
    "VisionSettings",
    "load_vision_settings",
    "VideoAnalysisSystem",
    "VisionService",
    "vision_router",
]


def __getattr__(name: str) -> Any:
    """惰性导出，避免导入本包即加载 FastAPI / cv2 / ultralytics 等重依赖。"""
    if name == "VideoAnalysisSystem":
        from .multimodal_system import VideoAnalysisSystem

        return VideoAnalysisSystem
    if name == "VisionService":
        from .service import VisionService

        return VisionService
    if name == "vision_router":
        from .router import router

        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
