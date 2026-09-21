"""车路云一体化集成的主服务：五段链路的编排与诚实降级（9/22 课件）。

链路按课件第 5 页的顺序串起来：

    1) flink_window   路侧/流计算：读 Flink 落库的窗口 + 预警（`flink_source.py`）
    2) forecast       云端预测：调既有 ONNX/LSTM 预测服务，输出未来 N 步交通流
    3) vllm_analysis  模型服务化：结构化路况拼成提示词，交给 vLLM 的 OpenAI 兼容端点
    4) agent_plan     决策闭环：`AgentPipeline` 查询→预测→建议，建议段优先用 vLLM，失败回落规则模板
    5) screen_push    应用侧：把一行 JSON 追加到 `screen_push.jsonl`，大屏轮询 `/api/v1/integration/latest`

每段依赖都通过构造参数注入，缺依赖时该段进 `degradations` 并把整次运行标 `verified=False`。
服务不会因为「没起 vLLM」而 500：能跑的部分照跑，跑不了的部分写清楚原因（见 README「降级矩阵」）。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

from .agent_pipeline import (
    AgentPipeline,
    AgentRun,
    ForecastResult,
    QueryResult,
    Recommendation,
)
from .config import IntegrationSettings
from .flink_source import WindowReader, most_severe, utc_now_text
from .vllm_client import VllmClient

LOGGER = logging.getLogger(__name__)

STAGES = ("flink_window", "forecast", "vllm_analysis", "agent_plan", "screen_push")
SCREEN_TIMEOUT_SECONDS = 5.0
WINDOW_ROWS_TO_SCAN = 50


@dataclass(frozen=True)
class StageResult:
    name: str
    status: str  # ok / degraded / skipped
    latency_ms: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntegrationRun:
    """一次完整集成的结果；`as_dict()` 就是 `/run` 响应与 `screen_push.jsonl` 里的记录。"""

    started_at: str
    stages: Sequence[StageResult]
    query: QueryResult | None
    agent: AgentRun | None
    push_path: str
    push_verified: bool
    degradations: tuple[str, ...] = ()
    verified: bool = False
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "pipeline": list(STAGES),
            "stages": [stage.as_dict() for stage in self.stages],
            "query": self.query.as_dict() if self.query else None,
            "agent": self.agent.as_dict() if self.agent else None,
            "push": {"path": self.push_path, "written": self.push_verified},
            "degradations": list(self.degradations),
            "verified": self.verified,
            "simulation": not self.verified,
            "latency_ms": self.latency_ms,
        }


PredictFn = Callable[[str, int], ForecastResult]


class IntegrationService:
    """五段链路的编排器：真机与离线走同一条代码路径，差别只在注入的依赖。"""

    def __init__(
        self,
        settings: IntegrationSettings,
        *,
        windows: WindowReader,
        predict: PredictFn | None = None,
        vllm: VllmClient | None = None,
        screen_url: str | None = None,
        push_path: Path | None = None,
    ) -> None:
        self.settings = settings
        self._windows = windows
        self._predict = predict
        self._vllm = vllm
        self._screen_url = (screen_url or settings.screen_url or "").strip() or None
        self._push_path = push_path or settings.push_path
        self._last: IntegrationRun | None = None

    # ---- 对外入口 -------------------------------------------------------
    @property
    def push_path(self) -> Path:
        """实际生效的推送路径（构造时可覆盖 settings.push_path，路由层只认这一个）。"""
        return self._push_path

    def health(self) -> dict[str, Any]:
        vllm_health = (
            self._vllm.health()
            if self._vllm is not None
            else {"configured": False, "reachable": False, "reason": "not_injected"}
        )
        return {
            "enabled": self.settings.enabled,
            "stages": list(STAGES),
            "window_source": self._windows.source,
            "prediction": "injected" if self._predict is not None else "unavailable",
            "forecast_steps": self.settings.forecast_steps,
            "vllm": vllm_health,
            "screen_webhook": self._screen_url or "",
            "push_path": str(self._push_path),
            "last_run": self._last.started_at if self._last else "",
        }

    def latest(self) -> IntegrationRun | None:
        """进程内最近一次；重启后回读推送日志的最后一条。"""
        return self._last or self._read_last_push()

    def run(self, checkpoint_id: str | None = None) -> IntegrationRun:
        """跑完整五段。`checkpoint_id` 为空时取最近窗口里最严重的一路。"""
        started = time.perf_counter()
        stages: list[StageResult] = []
        degradations: list[str] = []

        query, window_stage = self._query_stage(checkpoint_id)
        stages.append(window_stage)
        if window_stage.status != "ok":
            degradations.append(window_stage.detail)
        if query is None:
            return self._finish(started, stages, None, None, degradations + ["pipeline_aborted：没有可用窗口数据，后四段未执行"])

        agent_run = self._agent_stage(query, degradations)
        stages.extend(self._model_stages(agent_run))
        stages.append(self._agent_stage_result(agent_run))

        push_stage, written = self._push_stage(query, agent_run, degradations)
        stages.append(push_stage)
        return self._finish(started, stages, query, agent_run, degradations, push_verified=written)

    def _finish(
        self,
        started: float,
        stages: list[StageResult],
        query: QueryResult | None,
        agent: AgentRun | None,
        degradations: list[str],
        *,
        push_verified: bool = False,
    ) -> IntegrationRun:
        run = IntegrationRun(
            started_at=utc_now_text(),
            stages=tuple(stages),
            query=query,
            agent=agent,
            push_path=str(self._push_path),
            push_verified=push_verified,
            degradations=tuple(degradations),
            verified=not degradations,
            latency_ms=max(1, round((time.perf_counter() - started) * 1000)),
        )
        self._last = run
        return run

    # ---- 各段实现 -------------------------------------------------------
    def _query_stage(self, checkpoint_id: str | None) -> tuple[QueryResult | None, StageResult]:
        started = time.perf_counter()
        try:
            rows = self._windows.latest(limit=WINDOW_ROWS_TO_SCAN)
        except Exception as exc:  # 库侧异常不能变成 500，报成 skipped
            LOGGER.exception("读取 Flink 窗口结果失败")
            return None, StageResult("flink_window", "skipped", _elapsed(started), f"flink_window_failed:{type(exc).__name__}")
        row = most_severe(rows) if checkpoint_id is None else self._match_checkpoint(rows, checkpoint_id)
        if row is None:
            detail = (
                f"checkpoint_not_found:{checkpoint_id}"
                if checkpoint_id
                else "flink_window_empty（窗口表里没有可用行）"
            )
            return None, StageResult("flink_window", "skipped", _elapsed(started), detail)
        return (
            QueryResult(
                checkpoint_id=row.checkpoint_id,
                camera_id=row.camera_id,
                window_start=row.window_start,
                avg_speed=row.avg_speed,
                max_speed=row.max_speed,
                vehicle_count=row.vehicle_count,
                alert_level=row.alert_level,
                source=row.source,
            ),
            StageResult("flink_window", "ok", _elapsed(started), f"level={row.alert_level} source={row.source}"),
        )

    @staticmethod
    def _match_checkpoint(rows: Sequence[Any], checkpoint_id: str) -> Any | None:
        return most_severe([row for row in rows if row.checkpoint_id == checkpoint_id])

    def _agent_stage(self, query: QueryResult, degradations: list[str]) -> AgentRun | None:
        pipeline = AgentPipeline(
            query=lambda _checkpoint: query,
            predict=self._predict,
            advise=self._advise,
            forecast_steps=self.settings.forecast_steps,
        )
        try:
            run = pipeline.run(query.checkpoint_id)
        except Exception as exc:  # AgentPipeline 内部已逐段兜底，这里只兜住查询段自身的意外
            LOGGER.exception("Agent 闭环执行失败")
            degradations.append(f"agent_failed:{type(exc).__name__}")
            return None
        degradations.extend(run.degradations)
        return run

    def _model_stages(self, agent: AgentRun | None) -> list[StageResult]:
        """forecast / vllm_analysis 两段的状态从 AgentRun 里读，避免同一件事判两次。"""
        forecast = agent.forecast if agent else None
        if agent is None:
            forecast_stage = StageResult("forecast", "skipped", detail="agent_failed")
            vllm_stage = StageResult("vllm_analysis", "skipped", detail="agent_failed")
            return [forecast_stage, vllm_stage]
        if forecast is None:
            reason = next((item for item in agent.degradations if item.startswith("prediction")), "prediction_unavailable")
            forecast_stage = StageResult("forecast", "degraded", detail=reason)
        elif forecast.verified:
            forecast_stage = StageResult(
                "forecast",
                "ok",
                latency_ms=forecast.latency_ms,
                detail=f"steps={len(forecast.forecast)} model={forecast.model} backend={forecast.backend}",
            )
        else:
            forecast_stage = StageResult("forecast", "degraded", latency_ms=forecast.latency_ms, detail=f"forecast_verified=False model={forecast.model}")

        recommendation = agent.recommendation
        if recommendation.origin == "vllm":
            vllm_stage = StageResult(
                "vllm_analysis",
                "ok",
                latency_ms=recommendation.latency_ms,
                detail=f"model={recommendation.model}",
            )
        else:
            vllm_stage = StageResult("vllm_analysis", "degraded", latency_ms=recommendation.latency_ms, detail=f"origin={recommendation.origin}")
        return [forecast_stage, vllm_stage]

    def _agent_stage_result(self, agent: AgentRun | None) -> StageResult:
        if agent is None:
            return StageResult("agent_plan", "skipped", detail="agent_failed")
        if agent.degradations:
            return StageResult("agent_plan", "degraded", agent.latency_ms, "; ".join(agent.degradations))
        return StageResult("agent_plan", "ok", agent.latency_ms, f"origin={agent.recommendation.origin}")

    def _advise(self, query: QueryResult, forecast: ForecastResult | None) -> Recommendation:
        """建议段：优先 vLLM；端点不可用时抛给 `AgentPipeline` 回落规则模板。"""
        if self._vllm is None:
            raise RuntimeError("vllm_not_injected")
        completion = self._vllm.analyze(build_prompt(query, forecast))
        return Recommendation(
            text=completion.text,
            origin="vllm",
            model=completion.model,
            latency_ms=completion.latency_ms,
            evidence=self._evidence(query, forecast),
        )

    def _push_stage(
        self,
        query: QueryResult,
        agent: AgentRun | None,
        degradations: list[str],
    ) -> tuple[StageResult, bool]:
        started = time.perf_counter()
        record = {
            "time": utc_now_text(),
            "checkpoint_id": query.checkpoint_id,
            "camera_id": query.camera_id,
            "window_start": query.window_start,
            "alert_level": query.alert_level,
            "avg_speed": query.avg_speed,
            "vehicle_count": query.vehicle_count,
            "forecast": list(agent.forecast.forecast) if agent and agent.forecast else [],
            "recommendation": agent.recommendation.text if agent else "",
            "recommendation_origin": agent.recommendation.origin if agent else "none",
            "degradations": list(degradations),
            "verified": not degradations,
        }
        try:
            self._push_path.parent.mkdir(parents=True, exist_ok=True)
            with self._push_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            LOGGER.warning("大屏推送落盘失败: %s", exc)
            return StageResult("screen_push", "skipped", _elapsed(started), f"screen_push_failed:{type(exc).__name__}"), False

        detail = f"path={self._push_path}"
        if self._screen_url:
            if self._post_screen(record):
                detail += " webhook=ok"
            else:
                detail += " webhook=failed"
                degradations.append("screen_webhook_unreachable（已落盘，大屏改走轮询）")
        return StageResult("screen_push", "ok", _elapsed(started), detail), True

    def _post_screen(self, record: dict[str, Any]) -> bool:
        try:
            response = httpx.post(str(self._screen_url), json=record, timeout=SCREEN_TIMEOUT_SECONDS)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            LOGGER.warning("大屏 webhook 不可达: %s", exc)
            return False

    @staticmethod
    def _evidence(query: QueryResult, forecast: ForecastResult | None) -> tuple[str, ...]:
        items = [
            f"speed_stats@{query.window_start} avg={query.avg_speed} n={query.vehicle_count}",
            f"alert_level={query.alert_level} source={query.source}",
        ]
        if forecast and forecast.forecast:
            items.append(
                f"forecast[{len(forecast.forecast)}]={forecast.forecast[0]:g}..{forecast.forecast[-1]:g} model={forecast.model}"
            )
        return tuple(items)

    def _read_last_push(self) -> IntegrationRun | None:
        records = read_pushes(self._push_path, limit=1)
        return run_from_push(records[0]) if records else None


def read_pushes(path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    """读大屏推送流水的最后 N 条（倒序，最新的在前）。坏行跳过而不是整页 500。"""
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        LOGGER.warning("读取推送日志失败: %s", exc)
        return []
    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
        if len(records) >= limit:
            break
    return records


def build_prompt(query: QueryResult, forecast: ForecastResult | None) -> str:
    """第 3 段的提示词：把结构化路况 + 预测结果拼成模型可读的自然语言。

    预测段没结果时明确写「本次不可用」，让模型基于缺口给建议，而不是静默省略这一项信息。
    """
    lines = [
        f"相机 {query.camera_id}（卡口 {query.checkpoint_id}）在 {query.window_start} 的窗口统计：",
        f"- 平均车速 {query.avg_speed} km/h，最高车速 {query.max_speed} km/h，车辆数 {query.vehicle_count}",
        f"- 当前预警级别：{query.alert_level}",
    ]
    if forecast and forecast.forecast:
        unit = forecast.unit or "辆"
        values = "、".join(f"{value:g}" for value in forecast.forecast)
        lines.append(f"- 预测未来 {len(forecast.forecast)} 步交通流：{values}（{unit}）")
    else:
        lines.append("- 预测段本次不可用，请只依据当前窗口给建议并说明信息缺失。")
    lines.append("请给出 1) 拥堵判定与依据 2) 分级处置建议 3) 需要联动的外部系统。控制在 200 字内。")
    return "\n".join(lines)


def stage_map(run: IntegrationRun) -> dict[str, StageResult]:
    return {stage.name: stage for stage in run.stages}


def forecast_from_mapping(checkpoint_id: str, payload: dict[str, Any], *, unit: str = "") -> ForecastResult:
    """把 dashboard 的预测 dict 转成 `ForecastResult`。

    `verified` 只在真有模型给出非空预测时为真：mock/占位后端（`simulation` 或模型名 mock）一律 False。
    """
    values = [float(value) for value in payload.get("forecast") or []]
    model = str(payload.get("model") or "")
    simulated = bool(payload.get("simulation")) or "mock" in model.lower()
    return ForecastResult(
        checkpoint_id=checkpoint_id,
        current_flow=payload.get("current_flow"),
        forecast=tuple(values),
        unit=unit or str(payload.get("unit") or ""),
        model=model,
        backend=str(payload.get("backend") or ""),
        latency_ms=int(payload.get("latency_ms") or 0),
        verified=bool(values) and not simulated,
    )


def run_from_push(record: dict[str, Any]) -> IntegrationRun:
    """从 `screen_push.jsonl` 的一行还原大屏需要的字段（不谎称可重放整条链路）。"""
    query = QueryResult(
        checkpoint_id=str(record.get("checkpoint_id") or ""),
        camera_id=str(record.get("camera_id") or ""),
        window_start=str(record.get("window_start") or ""),
        avg_speed=float(record.get("avg_speed") or 0.0),
        max_speed=0.0,
        vehicle_count=int(record.get("vehicle_count") or 0),
        alert_level=str(record.get("alert_level") or "GREEN"),
        source="push_log",
    )
    return IntegrationRun(
        started_at=str(record.get("time") or ""),
        stages=tuple(StageResult(name, "ok", detail="from_push_log") for name in STAGES),
        query=query,
        agent=None,
        push_path="",
        push_verified=True,
        degradations=tuple(str(item) for item in record.get("degradations") or []),
        verified=bool(record.get("verified")),
        latency_ms=0,
    )


def _elapsed(started: float) -> int:
    return max(1, round((time.perf_counter() - started) * 1000))
