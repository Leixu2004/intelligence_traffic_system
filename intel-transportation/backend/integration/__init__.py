"""车路云一体化集成（9/22 课件）：vLLM 模型服务化 + Flink→预测→大模型→Agent→大屏 五段链路。

与 backend/agent、backend/vision、backend/crew 同一套约定：全部由 `TRAFFIC_*` 环境变量驱动，
默认 `enabled=False`，缺依赖缺服务时诚实降级（写进 degradations、标 verified=False），
不伪造模型输出，也不拿 Mock 端点的耗时去充当课件里的 GPU 指标。
"""

from __future__ import annotations

from typing import Any

from .config import (
    DEFAULT_VLLM_BASE_URL,
    DEFAULT_VLLM_MODEL,
    IntegrationSettings,
    load_integration_settings,
)

__all__ = [
    "DEFAULT_VLLM_BASE_URL",
    "DEFAULT_VLLM_MODEL",
    "IntegrationSettings",
    "load_integration_settings",
    "STAGES",
    "IntegrationService",
    "VllmClient",
    "integration_router",
    "system_integration",
]


def __getattr__(name: str) -> Any:
    """惰性导出：导入本包不即加载 FastAPI / httpx 客户端与数据库驱动。"""
    if name == "STAGES":
        from .service import STAGES

        return STAGES
    if name == "IntegrationService":
        from .service import IntegrationService

        return IntegrationService
    if name == "VllmClient":
        from .vllm_client import VllmClient

        return VllmClient
    if name == "integration_router":
        from .router import router

        return router
    if name == "system_integration":
        from . import system_integration as module

        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
