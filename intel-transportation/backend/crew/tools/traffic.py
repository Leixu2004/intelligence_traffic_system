"""把现有 TrafficToolbox 包成 CrewAI 工具，不重写任何 SQL 或推理逻辑。

工具名必须是 ASCII 小写下划线（CrewAI 会转成 OpenAI function name，中文会被清空并抛
ValueError），因此标识符用英文、中文名写进描述开头。

每个工具都吞掉异常并返回 ``ok=false`` 的 JSON，满足课件验收第 7 条“工具失败有 fallback”。"""

from __future__ import annotations

import json
from typing import Any

from crewai.tools import BaseTool


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _invalid(tool: str, message: str) -> str:
    return _json({"ok": False, "tool": tool, "message": message, "source": "input_invalid"})


def _fail(exc: Exception, tool: str) -> str:
    return _json({"ok": False, "tool": tool, "message": f"{type(exc).__name__}: {exc}", "source": "tool_error"})


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


class _BoxTool(BaseTool):
    """共享 toolbox 句柄的基类。"""

    toolbox: Any = None


class QueryCheckpointFlowTool(_BoxTool):
    name: str = "query_checkpoint_flow"
    description: str = (
        "查询卡口历史流量：按卡口编号读取 TimescaleDB 中的实际过车记录。输入：checkpoint_id。"
        "返回 JSON，含 records 与 source（实测数据）。"
    )

    def _run(self, checkpoint_id: str = "", limit: int = 20) -> str:
        target = _text(checkpoint_id)
        if not target:
            return _invalid(self.name, "缺少卡口编号，无法定位流量记录")
        try:
            return self.toolbox.query_checkpoint_flow(target, int(limit))
        except Exception as exc:  # noqa: BLE001 - 工具层必须把失败转成可继续推理的数据
            return _fail(exc, self.name)


class QueryPeakPeriodTool(_BoxTool):
    name: str = "query_peak_period"
    description: str = (
        "查询卡口峰值时段：统计某卡口回溯时段内的流量峰值。输入：checkpoint_id、lookback_hours。"
    )

    def _run(self, checkpoint_id: str = "", lookback_hours: int = 24) -> str:
        target = _text(checkpoint_id)
        if not target:
            return _invalid(self.name, "缺少卡口编号，无法统计峰值时段")
        try:
            return self.toolbox.query_peak_period(target, int(lookback_hours))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, self.name)


class PredictFlowTool(_BoxTool):
    name: str = "predict_checkpoint_flow"
    description: str = (
        "预测卡口流量：调用 LSTM/ONNX 模型预测未来 N 个时间步。输入：checkpoint_id、future_steps。"
        "返回值为预测，不是实测。"
    )

    def _run(self, checkpoint_id: str = "", future_steps: int = 3) -> str:
        target = _text(checkpoint_id)
        if not target:
            return _invalid(self.name, "缺少卡口编号，无法定位预测目标")
        try:
            return self.toolbox.predict_checkpoint_flow(target, int(future_steps))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, self.name)


class SearchTrafficLawTool(_BoxTool):
    name: str = "search_traffic_law"
    description: str = (
        "检索交通法规：在法规知识库中检索条款依据。输入：question（自然语言问题）、top_k。"
        "返回 citations 含条款号与版本；无依据时会拒答。"
    )

    def _run(self, question: str = "", top_k: int = 3) -> str:
        target = _text(question)
        if not target:
            return _invalid(self.name, "法规检索问题为空")
        try:
            return self.toolbox.search_traffic_law(target, int(top_k))
        except Exception as exc:  # noqa: BLE001
            return _fail(exc, self.name)


def build_data_tools(toolbox: Any) -> list[BaseTool]:
    return [
        QueryCheckpointFlowTool(toolbox=toolbox),
        QueryPeakPeriodTool(toolbox=toolbox),
        PredictFlowTool(toolbox=toolbox),
        SearchTrafficLawTool(toolbox=toolbox),
    ]
