"""任务 3：资源调度（调度员），同时依赖指挥决策与原始分析结论。"""

from __future__ import annotations

from crewai import Agent, Task

from ..profile import CrewProfile
from ..prompts import DISPATCH_DESCRIPTION


def build_dispatch_task(
    agents: dict[str, Agent],
    profile: CrewProfile,
    command_task: Task,
    analysis_task: Task,
) -> Task:
    return Task(
        description=DISPATCH_DESCRIPTION,
        expected_output=profile.task_outputs["dispatch"],
        agent=agents["dispatcher"],
        # CrewAI 的 context 不做传递展开，调度员需要指挥的指令，也需要分析师的原始流量数字。
        context=[command_task, analysis_task],
    )
