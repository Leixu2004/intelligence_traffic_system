"""任务 2：指挥决策（指挥官），context 依赖事件分析任务。"""

from __future__ import annotations

from crewai import Agent, Task

from ..profile import CrewProfile
from ..prompts import COMMAND_DESCRIPTION


def build_command_task(agents: dict[str, Agent], profile: CrewProfile, analysis_task: Task) -> Task:
    return Task(
        description=COMMAND_DESCRIPTION,
        expected_output=profile.task_outputs["command"],
        agent=agents["commander"],
        context=[analysis_task],
    )
