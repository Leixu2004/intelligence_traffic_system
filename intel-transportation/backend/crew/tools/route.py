"""绕行路线工具：包装 RoutePlanner，向 CrewAI 暴露经纬度入参。"""

from __future__ import annotations

import json
import time
from typing import Any

from crewai.tools import BaseTool

from ...agent.tools import record_tool_evidence


def _to_point(lng: Any, lat: Any) -> tuple[float, float] | None:
    try:
        if lng in (None, "") or lat in (None, ""):
            return None
        return (float(lng), float(lat))
    except (TypeError, ValueError):
        return None


class PlanDetourRouteTool(BaseTool):
    name: str = "plan_detour_route"
    description: str = (
        "规划绕行路线：为事故点给出具体绕行方案。输入：origin_lng、origin_lat、可选 destination_lng、destination_lat。"
        "返回 JSON 含 name/distance_km/eta_minutes/waypoints，以及 source 与 verified 可信度标记。"
    )
    planner: Any = None
    default_origin: Any = None

    def _run(
        self,
        origin_lng: float | str = "",
        origin_lat: float | str = "",
        destination_lng: float | str = "",
        destination_lat: float | str = "",
    ) -> str:
        started = time.perf_counter()
        origin = _to_point(origin_lng, origin_lat) or self.default_origin
        destination = _to_point(destination_lng, destination_lat)
        arguments = {
            "origin": list(origin) if origin else None,
            "destination": list(destination) if destination else None,
        }
        try:
            plan = self.planner.plan(origin, destination)
        except Exception as exc:  # noqa: BLE001 - 路线失败不应中断调度链路
            payload = {
                "ok": False,
                "message": f"路线规划异常：{type(exc).__name__}: {exc}",
                "source": "route_error",
                "verified": False,
            }
        else:
            payload = plan.as_dict()
        record_tool_evidence(
            name=self.name,
            arguments=arguments,
            summary=json.dumps(payload, ensure_ascii=False, default=str),
            source=str(payload.get("source") or "route"),
            started=started,
            ok=bool(payload.get("ok", True)),
        )
        return json.dumps({"tool": self.name, **payload}, ensure_ascii=False)
