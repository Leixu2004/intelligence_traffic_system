"""任务 1：事件分析（分析师）。"""

from __future__ import annotations

from crewai import Agent, Task

from ..contracts import EmergencyEvent
from ..profile import CrewProfile
from ..prompts import ANALYSIS_DESCRIPTION, EVENT_TEMPLATE


def build_analysis_task(agents: dict[str, Agent], profile: CrewProfile, event: EmergencyEvent) -> Task:
    event_block = EVENT_TEMPLATE.format(
        event_id=event.event_id,
        title=event.title,
        occurred_at=event.occurred_at or "未提供",
        location_text=event.location_text or "未提供",
        checkpoint_id=event.checkpoint_id or "未关联卡口",
        description=event.description or "无补充说明",
        lanes_blocked=event.lanes_blocked,
        severity_hint=event.severity_hint or "未预判",
    )
    return Task(
        description=ANALYSIS_DESCRIPTION.format(event=event_block),
        expected_output=profile.task_outputs["analysis"],
        agent=agents["analyst"],
    )
