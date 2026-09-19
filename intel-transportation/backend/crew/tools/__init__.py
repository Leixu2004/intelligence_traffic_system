"""CrewAI 工具装配：按课件第 8/9 页的角色-工具绑定关系分发。"""

from __future__ import annotations

from typing import Any

from .notify import PublishPublicNoticeTool
from .route import PlanDetourRouteTool
from .traffic import (
    PredictFlowTool,
    QueryCheckpointFlowTool,
    QueryPeakPeriodTool,
    SearchTrafficLawTool,
)


def build_crew_tools(
    *,
    toolbox: Any,
    planner: Any,
    sink: Any,
    event_id: str = "",
    default_origin: tuple[float, float] | None = None,
) -> dict[str, list[Any]]:
    """返回 {角色: 工具列表}，同一工具实例在角色间共享，避免重复构造。"""
    query_flow = QueryCheckpointFlowTool(toolbox=toolbox)
    query_peak = QueryPeakPeriodTool(toolbox=toolbox)
    predict = PredictFlowTool(toolbox=toolbox)
    law = SearchTrafficLawTool(toolbox=toolbox)
    route = PlanDetourRouteTool(planner=planner, default_origin=default_origin)
    notice = PublishPublicNoticeTool(sink=sink, default_event_id=event_id)
    return {
        "commander": [query_flow, query_peak, notice],
        "analyst": [query_flow, query_peak, predict, law],
        "dispatcher": [route, notice],
    }
