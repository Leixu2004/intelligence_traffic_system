# -*- coding: utf-8 -*-
"""CrewAI 应急处置演示：真实编排 + 可选 Mock 大模型。

默认 --mock：模型侧换成本机 OpenAI 兼容 Mock 端点，CrewAI 的 Agent/Task/工具执行/证据链
全部走真实代码，用于在无密钥时演示三 Agent 协作与工具闭环。
--live：用 .env 里的 DASHSCOPE_API_KEY 打真实百炼，此时报告正文才是模型生成的（验收 #3/#5）。

除 data/crew 下的审计与通告外不写任何文件，不打印密钥。
"""
import argparse
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

for _line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _key, _, _value = _line.partition("=")
        os.environ.setdefault(_key.strip(), _value.strip())

MOCK_PORT = 8099

# Mock 替身只调用本项目自有工具，绝不发起任何真实 LLM 请求。
MOCK_TOOL_ARGS = {
    "query_checkpoint_flow": {"checkpoint_id": "CP-NORTH-01", "limit": 12},
    "query_peak_period": {"checkpoint_id": "CP-NORTH-01", "lookback_hours": 24},
    "predict_checkpoint_flow": {"checkpoint_id": "CP-NORTH-01", "future_steps": 3},
    "search_traffic_law": {"question": "高速公路故障车警告与人员转移规定", "top_k": 3},
    "plan_detour_route": {},
    "publish_public_notice": {
        "content": "G2京沪高速K128+200多车追尾，双向通行中断，请按导航绕行并留出应急车道。",
        "channels": "短信,APP推送,交通广播",
    },
}


def _mock_completion(body: dict) -> dict:
    tools = body.get("tools") or []
    names = [n for n in (((t.get("function") or {}).get("name")) for t in tools) if n in MOCK_TOOL_ARGS]
    called = sum(1 for m in body.get("messages") or [] if m.get("role") == "tool")
    if names and called < len(names):
        target = names[called]
        return {
            "role": "assistant",
            "content": f"[mock] 调用 {target}（{called + 1}/{len(names)}）",
            "tool_calls": [
                {
                    "id": f"call_mock_{called}",
                    "type": "function",
                    "function": {
                        "name": target,
                        "arguments": json.dumps(MOCK_TOOL_ARGS[target], ensure_ascii=False),
                    },
                }
            ],
        }
    role = "分析师" if "predict_checkpoint_flow" in names else ("调度员" if "plan_detour_route" in names else "指挥官")
    return {
        "role": "assistant",
        "content": (
            f"【Mock-{role}·非模型推理】本轮工具返回值已按顺序消费：数据缺口如实标注，"
            "绕行路线保留 source/verified 标记，公众通告保留 simulation 标记，"
            "所有现场管制与资源调动动作需人工审批后方可执行。"
        ),
    }


class _MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 命名约定
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001 - 演示端点，坏请求直接当空体
            body = {}
        payload = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model") or "mock",
            "choices": [{"index": 0, "message": _mock_completion(body), "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):  # 静音访问日志
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CrewAI 应急处置演示")
    parser.add_argument("--live", action="store_true", help="使用真实百炼密钥（需 .env 的 DASHSCOPE_API_KEY）")
    parser.add_argument("--process", choices=("hierarchical", "sequential"), default="hierarchical")
    parser.add_argument("--quiet", action="store_true", help="关闭 CrewAI verbose 协作日志")
    args = parser.parse_args(argv)

    os.environ.update(
        {
            "PYTHONUTF8": "1",
            "TRAFFIC_CREW_ENABLED": "true",
            "TRAFFIC_CREW_PROCESS": args.process,
            "TRAFFIC_CREW_VERBOSE": "false" if args.quiet else "true",
            "TRAFFIC_CREW_MEMORY": "false",
            "TRAFFIC_CREW_MAX_ITER": "8",
        }
    )
    if not args.live:
        os.environ.update(
            {
                "TRAFFIC_CREW_LLM_API_KEY": "MOCK-KEY-OFFLINE-ONLY",
                "TRAFFIC_CREW_LLM_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/v1",
            }
        )

    from backend.crew.config import load_crew_settings
    from backend.crew.contracts import EmergencyEvent
    from backend.crew.crew_system import SAMPLE_EVENT, _service_for_cli, run_crew

    settings = load_crew_settings()
    if not args.live:
        server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), _MockHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[模式] Mock 大模型：http://127.0.0.1:{MOCK_PORT}/v1（编排与工具为真实代码，正文非模型推理）")
    else:
        server = None
        if not settings.api_key:
            print("[失败] --live 需要 .env 里的 DASHSCOPE_API_KEY 或 TRAFFIC_CREW_LLM_API_KEY")
            return 2
        print(f"[模式] 真实网关：base_url={settings.base_url} model={settings.model}")

    with tempfile.TemporaryDirectory() as storage:
        os.environ.setdefault("CREWAI_STORAGE_DIR", storage)
        service = _service_for_cli()
        if not service.available:
            print("[失败]", service.health().get("error"))
            return 2
        print(
            "[装配]",
            json.dumps(
                {
                    "process": settings.process,
                    "model": settings.model,
                    "fallback_corridors": len(service.planner.corridors),
                    "notify_channels": list(service.sink.channels),
                },
                ensure_ascii=False,
            )
        )
        result = run_crew(EmergencyEvent.model_validate(SAMPLE_EVENT), service=service)
        if server is not None:
            server.shutdown()

    print("\n--- 工具调用证据 ---")
    for item in result.tool_evidence:
        print(f"  {item.get('name'):<24} ok={item.get('ok')} source={item.get('source')} {item.get('summary', '')[:70]}")
    print("\n--- 处置方案报告 ---")
    print(result.report or "(空)")
    print(f"\n[结果] ok={result.ok} simulation={result.simulation} elapsed={result.elapsed_ms:.0f}ms")
    print(f"[审计] {settings.audit_path}")
    print(f"[通告] {settings.notify_path}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
