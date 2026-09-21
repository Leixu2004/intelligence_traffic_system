"""系统集成主程序（9/22 课件交付物 `system_integration.py`）：Flink + 预测 + vLLM + Agent + 大屏。

用法：
    python -m backend.integration.system_integration                       # 自动取最近最严重的一路
    python -m backend.integration.system_integration --checkpoint CP-SOUTH-02
    python -m backend.integration.system_integration --json out.json --report integration_report.md

装配策略（与 backend/crew/crew_system.py 同一套「能装就装、装不上就降级」）：
  * 窗口读取：配了 `TIMESCALEDB_DSN` 且装了 psycopg2 → 读 `speed_stats`/`traffic_alerts`；
    否则用 `backend/flink/test_data.json` 过本地参考实现，来源标 `simulation`；
  * 预测段：延迟导入 dashboard 的 `PredictionService` 与卡口特征装配，缺 onnx/模型即记为不可用；
  * 建议段：`VllmClient` 指向 `TRAFFIC_VLLM_BASE_URL`，端点不可达时回落规则模板并写明 origin。

报告里的每个数字都来自本次运行；课件第 9 页的 GPU 指标（1.2s / QPS 62 等）本机没有对应环境，
因此报告只写「未测」而不是引用课件值 —— 见 README「性能数字的边界」。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .agent_pipeline import ForecastResult
from .config import IntegrationSettings, load_integration_settings
from .flink_source import default_window_reader
from .service import IntegrationService, forecast_from_mapping
from .vllm_client import VllmClient

MODEL_PLACEHOLDER_NOTE = "未测（本机无 GPU/权重，未压测）"


class PredictionInputMissing(RuntimeError):
    """预测段没有可用输入（卡口历史流量取不到）。

    单独起一个类型名是为了让它出现在 degradations 里时可读：`prediction_failed:PredictionInputMissing`，
    同时不把底层异常（可能含 DSN）原样带进响应。
    """


# 已知降级的补齐方式，写进报告的「补齐方式」一节；匹配前缀，不匹配的不臆造。
REMEDIES = {
    "prediction_failed:PredictionInputMissing": "起 TimescaleDB 并配 `TIMESCALEDB_DSN`（预测要读卡口历史流量）",
    "vllm_unreachable": "起 vLLM：`docker compose --profile vllm up -d vllm`，或把 `TRAFFIC_VLLM_BASE_URL` 指向已有的 OpenAI 兼容端点",
    "recommendation=rule_fallback": "vLLM 端点可达后重跑，建议段即由模型生成（当前是规则模板）",
    "window_source=simulation": "起 Flink 集群并提交 9/21 两个作业，让 `speed_stats`/`traffic_alerts` 有数据",
    "query_source=simulation": "同上：窗口结果来自库侧时 `query.source` 才是 `timescaledb`",
    "screen_push_failed": "确认 `data/integration/` 可写，或改 `TRAFFIC_INTEGRATION_OUTPUT_DIR`",
}


def build_service(
    settings: IntegrationSettings | None = None,
    *,
    with_prediction: bool = True,
    push_path: Path | None = None,
) -> tuple[IntegrationService, list[str]]:
    """自建装配，返回 (服务, 装配期降级列表)。"""
    settings = settings or load_integration_settings()
    notes: list[str] = []

    predict = None
    if with_prediction:
        predict, note = _prediction_adapter()
        if predict is None:
            notes.append(note)

    vllm = VllmClient(settings)
    if not settings.vllm_configured:
        notes.append("vllm_not_configured（TRAFFIC_VLLM_BASE_URL/TRAFFIC_VLLM_MODEL 为空）")
    else:
        health = vllm.health()
        if not health.get("reachable"):
            notes.append(f"vllm_unreachable:{health.get('reason')}（{settings.vllm_base_url}）")

    service = IntegrationService(
        settings,
        windows=default_window_reader(settings),
        predict=predict,
        vllm=vllm,
        push_path=push_path,
    )
    if service.health()["window_source"] != "timescaledb":
        notes.append("window_source=simulation（未连库侧 Flink 结果表）")
    return service, notes


def _prediction_adapter() -> tuple[Any, str]:
    """返回 (PredictFn | None, 不可用原因)。延迟导入是为了不在无 onnx 环境里直接 ImportError。"""

    def predict(checkpoint_id: str, steps: int) -> ForecastResult:
        from ..dashboard_api import MODEL_PATH, _agent_checkpoint_prediction
        from ..prediction.service import PredictionService

        service = PredictionService(MODEL_PATH)
        service.load()
        try:
            payload = _agent_checkpoint_prediction(service, checkpoint_id, steps)
        except ValueError as exc:  # dashboard 侧「没有可用于预测的流量记录」
            raise PredictionInputMissing(str(exc)) from exc
        finally:
            service.close()
        return forecast_from_mapping(checkpoint_id, payload)

    try:  # 先探测依赖，探测不过就整段标记不可用，而不是等第一次调用抛栈
        import importlib

        importlib.import_module("backend.prediction.service")
    except Exception as exc:  # noqa: BLE001 - 依赖缺失的种类很多，统一报成一段降级
        return None, f"prediction_unavailable:{type(exc).__name__}:{exc}"
    return predict, ""


def render_report(run: dict[str, Any], *, assembly_notes: list[str], settings: IntegrationSettings) -> str:
    agent = run.get("agent") or {}
    query = run.get("query") or {}
    recommendation = agent.get("recommendation") or {}
    forecast = agent.get("forecast") or {}
    forecast_line = (
        f"预测 {len(forecast.get('forecast') or [])} 步，模型 `{forecast.get('model', '')}`，"
        f"后端 `{forecast.get('backend', '')}`，耗时 {forecast.get('latency_ms', 0)} ms，"
        f"verified=`{str(forecast.get('verified')).lower()}`"
        if forecast
        else "本次未产出（见下降级明细）"
    )
    lines: list[str] = ["# 车路云一体化集成报告", ""]
    lines += [
        f"- 生成时间：{run.get('started_at', '')}",
        f"- 集成开关：`TRAFFIC_INTEGRATION_ENABLED={str(settings.enabled).lower()}`",
        f"- vLLM 端点：`{settings.vllm_base_url}` / 服务模型名 `{settings.vllm_model}`",
        f"- 结果是否 verified：`{str(run.get('verified')).lower()}`（false 表示存在降级，见下）",
        "",
        "## 五段链路",
        "",
        "| # | 段 | 状态 | 耗时(ms) | 说明 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, stage in enumerate(run.get("stages") or [], start=1):
        lines.append(
            f"| {index} | {stage.get('name', '')} | {stage.get('status', '')} | {stage.get('latency_ms', 0)} | {stage.get('detail', '')} |"
        )
    lines += [
        "",
        "## 数据流转",
        "",
        f"1. Flink 窗口：相机 `{query.get('camera_id', '')}` / 卡口 `{query.get('checkpoint_id', '')}` @ `{query.get('window_start', '')}`，"
        f"均速 {query.get('avg_speed', '')} km/h，车辆数 {query.get('vehicle_count', '')}，级别 `{query.get('alert_level', '')}`（来源 `{query.get('source', '')}`）",
        f"2. 预测：{forecast_line}",
        f"3. 大模型：vLLM 端点 `/chat/completions`，建议来源 `{recommendation.get('origin', '')}`，模型 `{recommendation.get('model', '')}`",
        f"4. Agent：{' → '.join(agent.get('steps') or [])}",
        f"5. 大屏：`{run.get('push', {}).get('path', '')}`（写入 `{str(run.get('push', {}).get('written')).lower()}`），轮询 `/api/v1/integration/latest`",
        "",
        "### 处置建议",
        "",
        recommendation.get("text", "（无）"),
        "",
        "## 降级与边界",
        "",
    ]
    degradations = list(run.get("degradations") or []) + list(assembly_notes)
    if degradations:
        lines += [f"- {item}" for item in dict.fromkeys(degradations)]
    else:
        lines.append("- 本次运行无降级（五段全部来自真实端点）。")
    lines += [
        "",
        "### 补齐方式",
        "",
    ]
    remedies = _remedies(degradations)
    if remedies:
        lines += [f"- `{item}` → {REMEDIES[key]}" for item, key in remedies]
    else:
        lines.append("- 无（本次没有可补齐的已知降级项）。")
    lines += [
        "",
        "### 未测项",
        "",
        f"- vLLM 吞吐/QPS/首 token 延迟：{MODEL_PLACEHOLDER_NOTE}",
        "- 端到端 320ms 推送时延：需要集群 + 真模型 + 大屏三方同时在线，本次未测。",
        "- 车路云协同（RSU/V2X 上行）：本项目无路侧设备，只做到「路侧数据 → 云端 → 应用」这一段。",
        "",
        "> 报告中的每个数字都来自本次运行的真实调用或本仓库的本地复算；"
        "标 `simulation` 的来源不表示集群已跑通，也不构成准确率或性能结论。",
        "",
    ]
    return "\n".join(lines)


def _remedies(degradations: list[str]) -> list[tuple[str, str]]:
    """把降级条目对上已知补齐方式；前缀匹配，匹配不上的原样留在「降级与边界」里。"""
    found: list[tuple[str, str]] = []
    for item in dict.fromkeys(degradations):
        for key in REMEDIES:
            if item.startswith(key) or item == key:
                found.append((item, key))
                break
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="车路云一体化集成主程序（Flink + 预测 + vLLM + Agent + 大屏）")
    parser.add_argument("--checkpoint", default="", help="卡口 ID，留空则取最近窗口里最严重的一路")
    parser.add_argument("--json", dest="json_path", default="", help="把本次结果另存为 JSON")
    parser.add_argument("--report", dest="report_path", default="", help="把本次报告写成 Markdown（默认写 config.report_path）")
    parser.add_argument("--no-prediction", action="store_true", help="跳过预测段，只验证其余四段")
    parser.add_argument("--quiet", action="store_true", help="不打印结果 JSON，只打印结论行")
    args = parser.parse_args(argv)

    settings = load_integration_settings()
    service, notes = build_service(settings, with_prediction=not args.no_prediction)
    run = service.run(args.checkpoint.strip() or None)
    payload = run.as_dict()

    report_text = render_report(payload, assembly_notes=notes, settings=settings)
    report_path = Path(args.report_path) if args.report_path else settings.report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"报告: {report_path}")
    stage_summary = " | ".join("{}={}".format(stage["name"], stage["status"]) for stage in payload["stages"])
    print(f"五段: {stage_summary}")
    print(f"verified={payload['verified']} degradations={len(payload['degradations'])}")
    for note in notes:
        print(f"装配降级: {note}")
    if payload["degradations"]:
        print("提示：以上均为本次运行的真实缺口，补齐后重跑本命令即可，报告不会替它们判 PASS。")
    return 0 if run.query is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
