"""Crew 服务生命周期：装配依赖、执行应急处置、失败只降级不抛出。

装配方式与 backend/agent 一致——由调用方注入 TrafficToolGateway，
因此 CrewAI 复用同一套只读 SQL 与 ONNX 预测入口，不产生第二份数据访问实现。"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from ..agent.audit import JsonlAuditLog
from ..agent.tools import TrafficToolbox, TrafficToolGateway
from .config import CrewSettings, load_crew_settings
from .contracts import CrewRunResult, EmergencyEvent
from .notice import PublicNoticeSink
from .profile import CrewProfile, load_profile
from .prompts import LIMITATIONS
from .routing import RoutePlanner

LOGGER = logging.getLogger(__name__)


def _checkpoint_origin() -> tuple[float, float] | None:
    try:
        lng = float(os.getenv("CHECKPOINT_GPS_LNG", "116.4074"))
        lat = float(os.getenv("CHECKPOINT_GPS_LAT", "39.9042"))
    except ValueError:
        return None
    return (lng, lat)


class CrewService:
    def __init__(
        self,
        settings: CrewSettings,
        profile: CrewProfile,
        toolbox: TrafficToolbox,
        planner: RoutePlanner,
        sink: PublicNoticeSink,
        audit: JsonlAuditLog | None = None,
    ):
        self.settings = settings
        self.profile = profile
        self.toolbox = toolbox
        self.planner = planner
        self.sink = sink
        self.audit = audit or JsonlAuditLog(settings.audit_path)
        self._load_error: str | None = None
        if not settings.enabled:
            self._load_error = "多 Agent 应急处置未启用（TRAFFIC_CREW_ENABLED=false）"
        elif not settings.api_key:
            self._load_error = "缺少 LLM 密钥，无法启动 CrewAI 协作"
        else:
            try:
                import crewai  # noqa: F401
            except ImportError:
                self._load_error = "CrewAI 依赖尚未安装（pip install crewai）"

    @classmethod
    def build(cls, settings: CrewSettings | None = None, *, gateway: TrafficToolGateway) -> CrewService:
        resolved = settings or load_crew_settings()
        profile = load_profile(resolved.config_path)
        planner = RoutePlanner.from_config(
            profile.raw,
            mode=resolved.route_mode,
            amap_key=resolved.amap_key,
            timeout_seconds=resolved.amap_timeout_seconds,
        )
        return cls(resolved, profile, TrafficToolbox(gateway), planner, PublicNoticeSink(resolved.notify_path))

    @property
    def available(self) -> bool:
        return self._load_error is None

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.enabled,
            "available": self.available,
            "process": self.settings.process,
            "model": self.settings.model,
            "route_mode": self.settings.route_mode,
            "amap_configured": bool(self.settings.amap_key),
            "fallback_corridors": len(self.planner.corridors),
            "notify_channels": list(self.sink.channels),
            "error": self._load_error,
        }

    def respond(self, event: EmergencyEvent) -> CrewRunResult:
        started = time.perf_counter()
        if not self.available:
            return self._record(
                CrewRunResult(
                    ok=False,
                    process=self.settings.process,
                    event=event,
                    degradation=self._load_error,
                    simulation=True,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    limitations=LIMITATIONS,
                )
            )
        from .crew_system import run_crew

        try:
            result = run_crew(event, service=self)
        except Exception as exc:  # noqa: BLE001 - 协作失败必须降级为可读结果而非 500
            LOGGER.exception("CrewAI emergency run failed")
            result = CrewRunResult(
                ok=False,
                process=self.settings.process,
                event=event,
                degradation=f"多 Agent 协作失败：{type(exc).__name__}",
                simulation=True,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                limitations=LIMITATIONS,
            )
        return self._record(result)

    def _record(self, result: CrewRunResult) -> CrewRunResult:
        self.audit.append(
            {
                "event_id": result.event.event_id,
                "process": result.process,
                "ok": result.ok,
                "simulation": result.simulation,
                "degradation": result.degradation,
                "tool_calls": len(result.tool_evidence),
                "elapsed_ms": round(result.elapsed_ms, 1),
            }
        )
        return result
