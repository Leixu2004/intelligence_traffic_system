"""三个角色的构造。LLM 走 crewai.LLM 的 OpenAI 兼容入口，不依赖 litellm。"""

from __future__ import annotations

from typing import Any

from crewai import LLM, Agent

from .config import CrewSettings
from .profile import CrewProfile


class CrewUnavailableError(RuntimeError):
    """CrewAI 不可用（未启用、缺密钥或依赖缺失）。"""


def build_llm(settings: CrewSettings, model: str | None = None) -> LLM:
    if not settings.api_key:
        raise CrewUnavailableError("缺少 LLM 密钥：请设置 TRAFFIC_CREW_LLM_API_KEY 或 DASHSCOPE_API_KEY")
    return LLM(
        model=(model or settings.model).strip(),
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=settings.temperature,
        timeout=int(settings.timeout_seconds),
    )


def build_agents(
    settings: CrewSettings,
    profile: CrewProfile,
    tool_map: dict[str, list[Any]],
    *,
    llm: LLM | None = None,
) -> dict[str, Agent]:
    """返回 {角色键: Agent}。commander 允许委派，符合课件第 8 页的 allow_delegation=True。"""
    model = llm or build_llm(settings)
    agents: dict[str, Agent] = {}
    for key in ("commander", "analyst", "dispatcher"):
        persona = profile.roles[key]
        agents[key] = Agent(
            role=persona["role"],
            goal=persona["goal"],
            backstory=persona["backstory"],
            tools=tool_map.get(key, []),
            llm=model,
            allow_delegation=(key == "commander"),
            verbose=settings.verbose,
            max_iter=settings.max_iter,
        )
    return agents
