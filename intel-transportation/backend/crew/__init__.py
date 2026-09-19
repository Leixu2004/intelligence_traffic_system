"""多 Agent 应急处置子系统（CrewAI）。

与 backend/agent、backend/rag 平级，默认关闭（TRAFFIC_CREW_ENABLED=false），
未启用或依赖缺失时只降级、不影响预测主链与流量大屏。
"""

from __future__ import annotations

from typing import Any

from .config import CrewSettings, load_crew_settings

__all__ = ["CrewSettings", "load_crew_settings", "CrewService", "EmergencyEvent", "crew_router"]


def __getattr__(name: str) -> Any:
    """惰性导出，避免导入本包即加载 CrewAI 重依赖。"""
    if name == "CrewService":
        from .service import CrewService

        return CrewService
    if name == "EmergencyEvent":
        from .contracts import EmergencyEvent

        return EmergencyEvent
    if name == "crew_router":
        from .router import router

        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
