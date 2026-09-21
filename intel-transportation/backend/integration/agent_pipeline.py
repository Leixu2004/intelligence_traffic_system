"""Agent 闭环管线：查询 → 预测 → 建议（9/22 课件交付物 `agent_pipeline.py`）。

三段都通过构造参数注入，因此同一份代码既能在真机（TimescaleDB + ONNX + vLLM/Agent）上跑，
也能在 e2e 测试里换成 Mock 跑通「全流程无报错」这项验收 —— 而不会因为缺依赖而没法测。

诚实约定：任何一段退化都写进 `degradations`，并把整次运行标 `verified=False`；
建议文本标明来源（agent / vllm / rule_fallback），不把规则兜底文案伪装成大模型产出。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

QUERY_STEP = "query_traffic"
PREDICT_STEP = "predict_flow"
ADVISE_STEP = "generate_plan"
STEP_ORDER = (QUERY_STEP, PREDICT_STEP, ADVISE_STEP)

# 建议段可用的兜底：模型不可用时按课件第 8 页的处置动作给模板文案，来源标 rule_fallback
RULE_FALLBACK = {
    "PURPLE": "紫级：封闭/分流并行 —— 联动交警现场管控，上游匝道管控，推送绕行方案，通知应急资源到位。",
    "RED": "红级：上游分流 + 信号配时调整，诱导车辆绕行，缩短下游放行周期，持续观测 10 分钟。",
    "AMBER": "黄级：加强监控与车速诱导，暂停该路段占道作业，准备分流预案。",
    "GREEN": "绿级：通行正常，维持现有配时，例行巡检。",
}
LEVEL_ORDER = ("GREEN", "AMBER", "RED", "PURPLE")


class PipelineError(RuntimeError):
    """某一段彻底无法执行（上层会返回 502，表示管线没跑出结果）。"""


@dataclass(frozen=True)
class QueryResult:
    checkpoint_id: str
    camera_id: str
    window_start: str
    avg_speed: float
    max_speed: float
    vehicle_count: int
    alert_level: str
    source: str  # timescaledb / simulation / live_stream

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


@dataclass(frozen=True)
class ForecastResult:
    checkpoint_id: str
    current_flow: float | None
    forecast: Sequence[float] = field(default_factory=tuple)
    unit: str = ""
    model: str = ""
    backend: str = ""
    latency_ms: int = 0
    verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = self.__dict__.copy()
        payload["forecast"] = list(self.forecast)
        return payload


@dataclass(frozen=True)
class Recommendation:
    text: str
    origin: str  # agent / vllm / rule_fallback
    model: str = ""
    latency_ms: int = 0
    evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = self.__dict__.copy()
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True)
class AgentRun:
    query: QueryResult
    forecast: ForecastResult | None
    recommendation: Recommendation
    degradations: tuple[str, ...] = ()
    verified: bool = False
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.as_dict(),
            "forecast": self.forecast.as_dict() if self.forecast else None,
            "recommendation": self.recommendation.as_dict(),
            "degradations": list(self.degradations),
            "verified": self.verified,
            "latency_ms": self.latency_ms,
            "steps": list(STEP_ORDER),
        }


# 注入点：三段各自的取数/推理函数
QueryFn = Callable[[str], QueryResult]
PredictFn = Callable[[str, int], ForecastResult]
AdviseFn = Callable[[QueryResult, ForecastResult | None], Recommendation]


class AgentPipeline:
    """查询 → 预测 → 建议。三段依赖注入，便于真机与测试共用同一条管线。"""

    def __init__(
        self,
        *,
        query: QueryFn,
        predict: PredictFn | None = None,
        advise: AdviseFn,
        forecast_steps: int = 5,
    ) -> None:
        self._query = query
        self._predict = predict
        self._advise = advise
        self.forecast_steps = forecast_steps

    def run(self, checkpoint_id: str) -> AgentRun:
        started = time.perf_counter()
        degradations: list[str] = []

        query = self._query(checkpoint_id)  # 1) 查询实时路况
        if query.source != "timescaledb":
            degradations.append(f"query_source={query.source}（非库侧真实数据）")

        forecast: ForecastResult | None = None
        if self._predict is None:
            degradations.append("prediction_unavailable（未注入预测段，跳过 LSTM）")
        else:
            try:
                forecast = self._predict(checkpoint_id, self.forecast_steps)
                if not forecast.verified:
                    degradations.append("forecast_verified=False（模型/数据未达验收口径）")
            except Exception as exc:  # 预测段挂了不能拖垮整条管线
                degradations.append(f"prediction_failed:{type(exc).__name__}")

        try:
            recommendation = self._advise(query, forecast)  # 3) 生成建议
        except Exception as exc:
            degradations.append(f"advise_failed:{type(exc).__name__}，回落规则模板")
            recommendation = Recommendation(
                text=RULE_FALLBACK.get(query.alert_level, RULE_FALLBACK["GREEN"]),
                origin="rule_fallback",
            )
        if recommendation.origin == "rule_fallback":
            degradations.append("recommendation=rule_fallback（模板文案，非模型生成）")

        return AgentRun(
            query=query,
            forecast=forecast,
            recommendation=recommendation,
            degradations=tuple(degradations),
            verified=not degradations,
            latency_ms=max(1, round((time.perf_counter() - started) * 1000)),
        )


def worst_level(levels: Sequence[str]) -> str:
    """取更严重的一级（LEVEL_ORDER 顺序即严重程度）。"""
    rank = max((LEVEL_ORDER.index(level) for level in levels if level in LEVEL_ORDER), default=0)
    return LEVEL_ORDER[rank]
