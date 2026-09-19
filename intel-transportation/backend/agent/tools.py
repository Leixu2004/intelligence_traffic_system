"""Project-owned read-only tools exposed to the LangChain agent."""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from .contracts import ToolEvidence

TrafficRecordsLoader = Callable[
    [str | None, int], tuple[list[dict[str, Any]], str, str]
]
CheckpointPredictor = Callable[[str, int], dict[str, Any]]
LawSearch = Callable[[str, int], dict[str, Any]]


@dataclass(frozen=True)
class TrafficToolGateway:
    traffic_records: TrafficRecordsLoader
    detection_records: TrafficRecordsLoader
    predict_checkpoint: CheckpointPredictor
    law_search: LawSearch | None = None


_TRACE: ContextVar[list[ToolEvidence] | None] = ContextVar(
    "traffic_agent_tool_trace", default=None
)


def start_tool_trace() -> Token:
    return _TRACE.set([])


def finish_tool_trace(token: Token) -> list[ToolEvidence]:
    evidence = list(_TRACE.get() or [])
    _TRACE.reset(token)
    return evidence


def record_tool_evidence(
    *,
    name: str,
    arguments: dict[str, Any],
    summary: str,
    source: str,
    started: float,
    ok: bool = True,
) -> None:
    """写入同一条工具证据链，供不经过 TrafficToolbox 的工具复用（如 Crew 路线与通告工具）。"""
    TrafficToolbox._record(
        name=name,
        arguments=arguments,
        summary=summary,
        source=source,
        started=started,
        ok=ok,
    )


class TrafficToolbox:
    def __init__(self, gateway: TrafficToolGateway):
        self.gateway = gateway

    @staticmethod
    def _json(data: dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, default=str)

    @staticmethod
    def _checkpoint(value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", value):
            raise ValueError("checkpoint_id 格式无效")
        return value

    @staticmethod
    def _record(
        *,
        name: str,
        arguments: dict[str, Any],
        summary: str,
        source: str,
        started: float,
        ok: bool = True,
    ) -> None:
        trace = _TRACE.get()
        if trace is None:
            return
        trace.append(
            ToolEvidence(
                name=name,
                arguments=arguments,
                summary=summary[:1000],
                source=source,
                ok=ok,
                elapsed_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        )

    def query_checkpoint_flow(self, checkpoint_id: str, limit: int = 20) -> str:
        """查询指定卡口最近的流量与平均速度，不执行任意 SQL。"""
        started = time.perf_counter()
        limit = min(100, max(1, int(limit)))
        try:
            checkpoint_id = self._checkpoint(checkpoint_id)
        except ValueError as exc:
            return self._json({"ok": False, "message": str(exc), "source": "validation"})
        selected, source, warning = self.gateway.traffic_records(checkpoint_id, limit)
        if not selected:
            summary = f"卡口 {checkpoint_id} 没有可用流量记录"
            self._record(
                name="query_checkpoint_flow",
                arguments={"checkpoint_id": checkpoint_id, "limit": limit},
                summary=summary,
                source=source,
                started=started,
                ok=False,
            )
            return self._json(
                {"ok": False, "message": summary, "source": source, "warning": warning}
            )
        payload = []
        for record in selected:
            payload.append(
                {
                    "time": record.get("time"),
                    "vehicle_count": record.get("vehicle_count", 1),
                    "average_speed_kmh": record.get(
                        "average_speed", record.get("speed_kmh")
                    ),
                }
            )
        summary = f"卡口 {checkpoint_id} 返回 {len(payload)} 条最近流量记录"
        self._record(
            name="query_checkpoint_flow",
            arguments={"checkpoint_id": checkpoint_id, "limit": limit},
            summary=summary,
            source=source,
            started=started,
        )
        return self._json(
            {
                "ok": True,
                "checkpoint_id": checkpoint_id,
                "records": payload,
                "source": source,
                "warning": warning,
            }
        )

    def query_peak_period(self, checkpoint_id: str, lookback_hours: int = 24) -> str:
        """查询指定卡口在已有数据中的峰值时间桶。"""
        started = time.perf_counter()
        lookback_hours = min(168, max(1, int(lookback_hours)))
        try:
            checkpoint_id = self._checkpoint(checkpoint_id)
        except ValueError as exc:
            return self._json({"ok": False, "message": str(exc), "source": "validation"})
        records, source, warning = self.gateway.traffic_records(checkpoint_id, 1000)
        frame = pd.DataFrame(records)
        if frame.empty or "time" not in frame.columns:
            summary = f"卡口 {checkpoint_id} 没有可用于高峰分析的数据"
            self._record(
                name="query_peak_period",
                arguments={"checkpoint_id": checkpoint_id, "lookback_hours": lookback_hours},
                summary=summary,
                source=source,
                started=started,
                ok=False,
            )
            return self._json({"ok": False, "message": summary, "source": source})
        frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
        if "vehicle_count" not in frame.columns:
            frame["vehicle_count"] = 1
        frame["vehicle_count"] = pd.to_numeric(
            frame["vehicle_count"], errors="coerce"
        ).fillna(0)
        frame = frame.dropna(subset=["time"])
        if frame.empty:
            return self._json({"ok": False, "message": "没有有效时间戳", "source": source})
        cutoff = frame["time"].max() - pd.Timedelta(hours=lookback_hours)
        frame = frame.loc[frame["time"].ge(cutoff)]
        peak = frame.loc[frame["vehicle_count"].idxmax()]
        summary = (
            f"卡口 {checkpoint_id} 峰值时间为 {peak['time']}，"
            f"流量 {float(peak['vehicle_count']):g}"
        )
        self._record(
            name="query_peak_period",
            arguments={"checkpoint_id": checkpoint_id, "lookback_hours": lookback_hours},
            summary=summary,
            source=source,
            started=started,
        )
        return self._json(
            {
                "ok": True,
                "checkpoint_id": checkpoint_id,
                "peak_time": peak["time"],
                "vehicle_count": float(peak["vehicle_count"]),
                "lookback_hours": lookback_hours,
                "source": source,
                "warning": warning,
            }
        )

    def query_vehicle_type_distribution(
        self, checkpoint_id: str | None = None, limit: int = 300
    ) -> str:
        """统计最近识别记录中的车型分布。"""
        started = time.perf_counter()
        limit = min(1000, max(1, int(limit)))
        if checkpoint_id:
            try:
                checkpoint_id = self._checkpoint(checkpoint_id)
            except ValueError as exc:
                return self._json(
                    {"ok": False, "message": str(exc), "source": "validation"}
                )
        selected, source, warning = self.gateway.detection_records(
            checkpoint_id, limit
        )
        counts = Counter(
            str(item.get("vehicle_type") or "UNKNOWN") for item in selected
        )
        total = sum(counts.values())
        if not total:
            summary = "没有可用于车型统计的识别记录"
            self._record(
                name="query_vehicle_type_distribution",
                arguments={"checkpoint_id": checkpoint_id, "limit": limit},
                summary=summary,
                source=source,
                started=started,
                ok=False,
            )
            return self._json({"ok": False, "message": summary, "source": source})
        distribution = [
            {
                "vehicle_type": name,
                "count": count,
                "ratio": round(count / total, 4),
            }
            for name, count in counts.most_common()
        ]
        summary = f"统计 {total} 条识别记录，共 {len(distribution)} 类车型"
        self._record(
            name="query_vehicle_type_distribution",
            arguments={"checkpoint_id": checkpoint_id, "limit": limit},
            summary=summary,
            source=source,
            started=started,
        )
        return self._json(
            {
                "ok": True,
                "checkpoint_id": checkpoint_id,
                "sample_count": total,
                "distribution": distribution,
                "source": source,
                "warning": warning,
            }
        )

    def predict_checkpoint_flow(self, checkpoint_id: str, future_steps: int = 1) -> str:
        """调用项目现有 ONNX 预测服务计算指定卡口的未来流量。"""
        started = time.perf_counter()
        future_steps = min(12, max(1, int(future_steps)))
        try:
            checkpoint_id = self._checkpoint(checkpoint_id)
            result = self.gateway.predict_checkpoint(checkpoint_id, future_steps)
        except (RuntimeError, ValueError) as exc:
            summary = f"卡口 {checkpoint_id} 预测不可用: {exc}"
            self._record(
                name="predict_checkpoint_flow",
                arguments={"checkpoint_id": checkpoint_id, "future_steps": future_steps},
                summary=summary,
                source="PredictionService",
                started=started,
                ok=False,
            )
            return self._json({"ok": False, "message": summary, "source": "PredictionService"})
        forecasts = result.get("forecast") or []
        if any(not math.isfinite(float(value)) for value in forecasts):
            raise ValueError("prediction contains non-finite values")
        summary = (
            f"卡口 {checkpoint_id} 返回 {len(forecasts)} 个预测点，"
            f"模型 {result.get('model', 'unknown')}"
        )
        self._record(
            name="predict_checkpoint_flow",
            arguments={"checkpoint_id": checkpoint_id, "future_steps": future_steps},
            summary=summary,
            source="PredictionService",
            started=started,
        )
        return self._json({"ok": True, **result, "checkpoint_id": checkpoint_id})

    def search_traffic_law(self, question: str, top_k: int = 3) -> str:
        """检索法规知识库，返回带条号、来源与版本的可核验条文片段。"""
        started = time.perf_counter()
        question = (question or "").strip()
        if not question or len(question) > 200:
            summary = "法规问题为空或超过200字"
            self._record(
                name="search_traffic_law",
                arguments={"question": question[:64], "top_k": top_k},
                summary=summary,
                source="LawRAG",
                started=started,
                ok=False,
            )
            return self._json({"ok": False, "message": summary, "source": "LawRAG"})
        top_k = min(8, max(1, int(top_k)))
        if self.gateway.law_search is None:
            summary = "法规知识库未接入"
            self._record(
                name="search_traffic_law",
                arguments={"question": question[:64], "top_k": top_k},
                summary=summary,
                source="LawRAG",
                started=started,
                ok=False,
            )
            return self._json({"ok": False, "message": summary, "source": "LawRAG"})
        try:
            result = self.gateway.law_search(question, top_k)
        except Exception as exc:
            summary = f"法规知识库调用失败: {type(exc).__name__}"
            self._record(
                name="search_traffic_law",
                arguments={"question": question[:64], "top_k": top_k},
                summary=summary,
                source="LawRAG",
                started=started,
                ok=False,
            )
            return self._json({"ok": False, "message": summary, "source": "LawRAG"})
        found = bool(result.get("ok")) and bool(result.get("found"))
        citations = result.get("citations", []) or []
        top_article = str(citations[0].get("law_articles", "")) if citations else ""
        summary = (
            f"法规知识库返回 {len(citations)} 条可核验条文（{top_article}）"
            if found
            else str(result.get("message", "法规知识库无相关条文"))
        )
        self._record(
            name="search_traffic_law",
            arguments={"question": question[:64], "top_k": top_k},
            summary=summary,
            source="LawRAG",
            started=started,
            ok=bool(result.get("ok")),
        )
        return self._json(result)

    def as_langchain_tools(self) -> list[Any]:
        try:
            from langchain_core.tools import StructuredTool
        except ImportError as exc:  # pragma: no cover - exercised in deployment health
            raise RuntimeError("langchain-core is not installed") from exc
        tools = [
            StructuredTool.from_function(
                func=self.query_checkpoint_flow,
                name="query_checkpoint_flow",
                description="查询指定卡口最近的流量和平均速度。需要 checkpoint_id。",
            ),
            StructuredTool.from_function(
                func=self.query_peak_period,
                name="query_peak_period",
                description="查询指定卡口已有数据中的峰值时间桶。需要 checkpoint_id。",
            ),
            StructuredTool.from_function(
                func=self.query_vehicle_type_distribution,
                name="query_vehicle_type_distribution",
                description="统计最近车牌或违章识别记录中的车型分布，可选 checkpoint_id。",
            ),
            StructuredTool.from_function(
                func=self.predict_checkpoint_flow,
                name="predict_checkpoint_flow",
                description="调用现有 ONNX 模型预测指定卡口未来流量。需要 checkpoint_id。",
            ),
        ]
        tools.append(
            StructuredTool.from_function(
                func=self.search_traffic_law,
                name="search_traffic_law",
                description=(
                    "检索交通法规知识库（道路交通安全法处罚标准等），返回条文原文、"
                    "条号、来源与版本。用于酒驾、超速、闯红灯等法规处罚类问题。"
                ),
            )
        )
        return tools
