"""任务装配：按课件第 5 页的依赖顺序串起分析 → 指挥 → 调度。"""

from __future__ import annotations

from crewai import Agent, Task

from ..contracts import EmergencyEvent
from ..profile import CrewProfile
from .analysis import build_analysis_task
from .command import build_command_task
from .dispatch import build_dispatch_task

__all__ = ["build_tasks", "build_analysis_task", "build_command_task", "build_dispatch_task"]


def build_tasks(agents: dict[str, Agent], profile: CrewProfile, event: EmergencyEvent) -> list[Task]:
    analysis = build_analysis_task(agents, profile, event)
    command = build_command_task(agents, profile, analysis)
    dispatch = build_dispatch_task(agents, profile, command, analysis)
    return [analysis, command, dispatch]
