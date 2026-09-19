"""Crew 角色画像与任务文案加载：读 backend/crew/config.yaml，环境变量再覆盖。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_ROLES: dict[str, dict[str, str]] = {
    "commander": {
        "role": "应急处置指挥官",
        "goal": "快速评估事件严重级，分配任务并整合决策",
        "backstory": "你是有 20 年现场经验的交通应急指挥专家，擅长事件分级与跨部门资源协调。",
    },
    "analyst": {
        "role": "交通数据分析师",
        "goal": "分析事故影响范围，预测流量变化趋势",
        "backstory": "你是交通数据分析专家，精通历史流量查询、LSTM 预测与法规检索。",
    },
    "dispatcher": {
        "role": "资源调度员",
        "goal": "调度救援资源并规划绕行路线",
        "backstory": "你熟悉城市路网与路径规划 API，负责把指令落成可执行方案。",
    },
}

DEFAULT_TASK_OUTPUTS: dict[str, str] = {
    "analysis": "事件分析报告（含影响评估与数据来源标注）",
    "command": "处置指令清单",
    "dispatch": "调度方案（资源、路线、通告）",
}


@dataclass(frozen=True)
class CrewProfile:
    roles: dict[str, dict[str, str]]
    task_outputs: dict[str, str]
    route_corridors: list[dict[str, Any]]
    raw: dict[str, Any]

    @property
    def llm_section(self) -> dict[str, Any]:
        return dict(self.raw.get("llm") or {})

    @property
    def crew_section(self) -> dict[str, Any]:
        return dict(self.raw.get("crew") or {})

    @property
    def route_fallback_source(self) -> str:
        return str((self.raw.get("route_fallback") or {}).get("source") or "demonstration_topology")


def load_profile(path: Path) -> CrewProfile:
    raw: dict[str, Any] = {}
    try:
        import yaml
    except ImportError:
        LOGGER.warning("PyYAML unavailable; using built-in crew role profiles")
        yaml = None  # type: ignore[assignment]

    if yaml is not None and path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                raw = loaded
        except Exception as exc:
            LOGGER.warning("Crew config.yaml unreadable (%s); using defaults", type(exc).__name__)

    roles = {key: {**defaults, **(raw.get("roles") or {}).get(key, {})} for key, defaults in DEFAULT_ROLES.items()}
    task_cfg = raw.get("tasks") or {}
    task_outputs = {
        key: str((task_cfg.get(key) or {}).get("expected_output") or DEFAULT_TASK_OUTPUTS[key]) for key in DEFAULT_TASK_OUTPUTS
    }
    corridors = [item for item in (raw.get("route_fallback") or {}).get("corridors") or [] if isinstance(item, dict)]
    return CrewProfile(roles=roles, task_outputs=task_outputs, route_corridors=corridors, raw=raw)
