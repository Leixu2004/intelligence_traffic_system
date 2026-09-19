"""Crew 组装与启动：三 Agent + 三任务 + Sequential/Hierarchical 编排。

对应课件提交物 crew_system.py。独立运行：
    python -m backend.crew.crew_system --process hierarchical --verbose
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from crewai import LLM, Crew, Process

from ..agent.tools import finish_tool_trace, start_tool_trace
from .agents import build_agents, build_llm
from .config import CrewSettings
from .contracts import CrewRunResult, EmergencyEvent
from .prompts import LIMITATIONS
from .service import CrewService
from .tasks import build_tasks

PROCESS_MAP = {"hierarchical": Process.hierarchical, "sequential": Process.sequential}

# 课件示例事件（第 11 页）
SAMPLE_EVENT = {
    "event_id": "EV-20260823-1435",
    "title": "高速 K128 处多车追尾",
    "description": "3 车追尾，道路阻断",
    "occurred_at": "2026-08-23 14:35",
    "location_text": "G2 京沪高速 K128+200",
    "checkpoint_id": "CP-NORTH-01",
    "lanes_blocked": 2,
}


def assemble_crew(service: CrewService, event: EmergencyEvent) -> Crew:
    settings = service.settings
    llm = build_llm(settings)
    tool_map = build_crew_tool_map(service, event)
    agents = build_agents(settings, service.profile, tool_map, llm=llm)
    tasks = build_tasks(agents, service.profile, event)
    kwargs: dict[str, Any] = {
        "agents": [agents["commander"], agents["analyst"], agents["dispatcher"]],
        "tasks": tasks,
        "process": PROCESS_MAP[settings.process],
        "verbose": settings.verbose,
        "memory": settings.memory,
    }
    if settings.process == "hierarchical":
        # 层级模式必须给 manager_llm；温度低于执行 Agent，减少分配抖动。
        kwargs["manager_llm"] = LLM(
            model=settings.effective_manager_model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            temperature=min(0.2, settings.temperature),
        )
    return Crew(**kwargs)


def build_crew_tool_map(service: CrewService, event: EmergencyEvent) -> dict[str, list[Any]]:
    from .tools import build_crew_tools

    return build_crew_tools(
        toolbox=service.toolbox,
        planner=service.planner,
        sink=service.sink,
        event_id=event.event_id,
        default_origin=event.origin_gps or _default_origin(),
    )


def run_crew(event: EmergencyEvent, *, service: CrewService) -> CrewRunResult:
    """执行一次端到端应急处置协作。

    CrewAI 输出由模型生成，且通告与备选路线均带模拟标记，因此结果恒为 simulation=true。
    """
    settings: CrewSettings = service.settings
    started = time.perf_counter()
    token = start_tool_trace()
    try:
        crew = assemble_crew(service, event)
        output = crew.kickoff()
    except Exception:
        finish_tool_trace(token)
        raise
    evidence = [_as_dict(item) for item in finish_tool_trace(token)]
    report = getattr(output, "raw", None) or str(output)
    return CrewRunResult(
        ok=True,
        process=settings.process,
        event=event,
        report=str(report),
        tool_evidence=evidence,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        simulation=True,
        limitations=LIMITATIONS,
    )


def _default_origin() -> tuple[float, float] | None:
    from .service import _checkpoint_origin

    return _checkpoint_origin()


def _as_dict(evidence: Any) -> dict[str, Any]:
    dump = getattr(evidence, "model_dump", None)
    return dict(dump()) if callable(dump) else dict(vars(evidence))


def _load_local_env() -> None:
    """独立命令行运行时读取项目 .env；.vscode/launch.json 已经用 envFile 注入，这里只是补齐裸命令场景。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    from .config import INNER_ROOT

    load_dotenv(INNER_ROOT / ".env")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser(description="CrewAI 应急处置多 Agent 系统")
    parser.add_argument("--event-json", help="事件 JSON 文件；缺省使用课件第 11 页示例事件")
    parser.add_argument("--process", choices=("hierarchical", "sequential"), help="覆盖流程模式")
    parser.add_argument("--verbose", action="store_true", help="打印 Agent 协作过程")
    parser.add_argument("--output", help="把处置方案 JSON 写入指定文件")
    args = parser.parse_args(argv)

    _load_local_env()
    if args.process:
        os.environ["TRAFFIC_CREW_PROCESS"] = args.process
    if args.verbose:
        os.environ["TRAFFIC_CREW_VERBOSE"] = "true"

    raw = Path(args.event_json).read_text(encoding="utf-8") if args.event_json else json.dumps(SAMPLE_EVENT)
    event = EmergencyEvent.model_validate_json(raw)
    result = _service_for_cli().respond(event)
    text = json.dumps(result.model_dump(), ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0 if result.ok else 1


def _service_for_cli() -> CrewService:
    """独立运行时的自建装配。

    延迟导入 dashboard_api 是为了复用既有的卡口预测特征装配，同时避免与它形成导入环。
    """
    import os

    from ..agent.repository import TrafficReadRepository
    from ..agent.tools import TrafficToolGateway
    from ..dashboard_api import MODEL_PATH, TIMESCALEDB_DSN, _agent_checkpoint_prediction
    from ..prediction.service import PredictionService
    from .config import load_crew_settings

    prediction = PredictionService(MODEL_PATH)
    prediction.load()
    repository = TrafficReadRepository(TIMESCALEDB_DSN)
    gateway = TrafficToolGateway(
        traffic_records=repository.traffic_records,
        detection_records=repository.detection_records,
        predict_checkpoint=lambda checkpoint_id, future_steps: _agent_checkpoint_prediction(
            prediction, checkpoint_id, future_steps
        ),
    )
    settings = load_crew_settings()
    os.environ.setdefault("CHECKPOINT_ID", "CP-NORTH-01")
    return CrewService.build(settings, gateway=gateway)


if __name__ == "__main__":
    raise SystemExit(main())
