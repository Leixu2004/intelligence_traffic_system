"""端到端集成测试套件（9/22 课件交付物 `e2e_test_suite.py`）。

覆盖六条用例，全部在本机可跑：vLLM 端点用仓库内自建的 Mock OpenAI 兼容服务替代
（正文标 `[Mock·非模型推理]`），窗口数据用固定夹具替代。**因此本套件证明的是接线，
不是真机能力**：Mock 端点上量到的任何耗时都不进报告、不当作课件第 9 页的性能指标。

用法：
    python -m backend.integration.e2e_test_suite            # 跑六条并打表
    .venv/Scripts/python.exe -m pytest backend/integration -q   # 同一批用例（tests/test_e2e.py 复用）
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence

from .agent_pipeline import ForecastResult
from .config import IntegrationSettings, load_integration_settings
from .flink_source import WindowReader, WindowRow
from .service import IntegrationService, build_prompt, stage_map
from .vllm_client import VllmClient

MOCK_MODEL = "mock-qwen-7b"
MOCK_REPLY = "【Mock·非模型推理】紫级：先封上游匝道再放行救援车，依据为窗口均速 14.4 km/h 与连续 2 个低速窗口。"


class FakeWindowReader(WindowReader):
    """固定窗口夹具，替代库侧读取。

    默认 `source="timescaledb"` 是为了让「五段全绿」这条用例真的能走到 verified=True：
    它验证的是接线自洽，不是库侧数据 —— 真机路径由 `DatabaseWindowReader` 的单测覆盖。
    """

    def __init__(self, rows: Sequence[WindowRow], *, source: str = "timescaledb", error: Exception | None = None) -> None:
        self._rows = list(rows)
        self._source = source
        self._error = error

    @property
    def source(self) -> str:
        return self._source

    def latest(self, *, limit: int = 20, camera_id: str | None = None) -> list[WindowRow]:
        if self._error is not None:
            raise self._error
        rows = [row for row in self._rows if camera_id is None or row.camera_id == camera_id]
        return sorted(rows, key=lambda item: item.window_start, reverse=True)[:limit]


def predict_ok(checkpoint_id: str, steps: int) -> ForecastResult:
    """预测段替身：给出确定性的递减流量，verified=True（Mock 装配下的自洽标记）。"""
    return ForecastResult(
        checkpoint_id=checkpoint_id,
        current_flow=120.0,
        forecast=tuple(120.0 - 8.0 * index for index in range(steps)),
        unit="vehicles/300s",
        model="lstm-onnx-mock",
        backend="mock",
        latency_ms=12,
        verified=True,
    )


# --------------------------------------------------------------------------
# Mock vLLM：只实现 /v1/models 与 /v1/chat/completions 两个端点
# --------------------------------------------------------------------------
class _MockVllmHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 命名约定
        if self.path.rstrip("/").endswith("/models"):
            self._reply({"object": "list", "data": [{"id": MOCK_MODEL, "object": "model"}]})
        else:
            self._reply({}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        if not str((body.get("messages") or [{}])[-1].get("content") or "").strip():
            self._reply({"error": {"message": "empty prompt"}}, status=400)
            return
        self._reply(
            {
                "id": "chatcmpl-mock",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model") or MOCK_MODEL,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": MOCK_REPLY}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )

    def _reply(self, payload: dict[str, Any], *, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args: object) -> None:  # 静音访问日志
        return


class MockVllmServer:
    """起在临时端口上的 Mock 端点；用 0 号端口避免和 crew_smoke 的 8099 抢。"""

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _MockVllmHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/v1"

    def __enter__(self) -> "MockVllmServer":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def closed_port_base_url() -> str:
    """一个确定没人监听的端点，用来验证「vLLM 挂了会怎样」。"""
    with ThreadingHTTPServer(("127.0.0.1", 0), _MockVllmHandler) as probe:
        port = probe.server_address[1]
    return f"http://127.0.0.1:{port}/v1"


# --------------------------------------------------------------------------
# 用例
# --------------------------------------------------------------------------
PURPLE_ROW = WindowRow(
    camera_id="CAM-02",
    checkpoint_id="CP-SOUTH-02",
    window_start="2026-09-21T14:20:00",
    avg_speed=14.4,
    max_speed=30.0,
    vehicle_count=9,
    alert_level="PURPLE",
    duration_min=20.0,
    source="timescaledb",
)
GREEN_ROW = replace(PURPLE_ROW, camera_id="CAM-01", checkpoint_id="CP-NORTH-01", avg_speed=62.0, alert_level="GREEN", duration_min=0.0, vehicle_count=4)


def make_service(
    *,
    rows: Sequence[WindowRow] = (PURPLE_ROW, GREEN_ROW),
    window_source: str = "timescaledb",
    predict: Any = predict_ok,
    window_error: Exception | None = None,
    vllm_base_url: str | None = None,
    push_dir: Path | None = None,
    enabled: bool = True,
) -> tuple[IntegrationService, IntegrationSettings, Path]:
    settings = replace(load_integration_settings(), enabled=enabled, vllm_base_url=vllm_base_url or "", vllm_model=MOCK_MODEL)
    push_path = (push_dir or Path(tempfile.mkdtemp())) / "screen_push.jsonl"
    vllm = VllmClient(settings) if vllm_base_url is not None else None
    service = IntegrationService(
        settings,
        windows=FakeWindowReader(rows, source=window_source, error=window_error),
        predict=predict,
        vllm=vllm,
        push_path=push_path,
    )
    return service, settings, push_path


class EndToEndCases(unittest.TestCase):
    maxDiff = None

    def test_E1_full_chain_with_mock_vllm(self) -> None:
        """E1 五段全绿：Flink 窗口 → 预测 → vLLM 建议 → Agent → 大屏落盘。"""
        with MockVllmServer() as mock:
            service, _, push_path = make_service(vllm_base_url=mock.base_url)
            run = service.run()
        payload = run.as_dict()
        self.assertEqual([stage.name for stage in run.stages], list(service.health()["stages"]))
        self.assertTrue(run.verified, f"预期无降级，实际 {run.degradations}")
        self.assertEqual([stage.status for stage in run.stages], ["ok"] * 5)
        self.assertEqual(run.agent.recommendation.origin, "vllm")
        self.assertEqual(run.agent.recommendation.model, MOCK_MODEL)
        self.assertEqual(len(run.agent.forecast.forecast), service.settings.forecast_steps)
        self.assertEqual(run.query.checkpoint_id, "CP-SOUTH-02")  # 两路里取最严重的一路
        self.assertEqual(run.agent.recommendation.evidence[0], f"speed_stats@{run.query.window_start} avg=14.4 n=9")
        self.assertTrue(push_path.is_file())
        self.assertFalse(payload["simulation"])

    def test_E2_vllm_down_falls_back_to_rule_template(self) -> None:
        """E2 vLLM 端点不可达：建议段回落规则模板并写明 origin，不抛异常不静默。"""
        service, _, _ = make_service(vllm_base_url=closed_port_base_url())
        run = service.run()
        stages = stage_map(run)
        self.assertEqual(run.agent.recommendation.origin, "rule_fallback")
        self.assertIn("紫级", run.agent.recommendation.text)
        self.assertEqual(stages["vllm_analysis"].status, "degraded")
        self.assertTrue(any("advise_failed" in item for item in run.degradations))
        self.assertFalse(run.verified)
        self.assertEqual(stages["screen_push"].status, "ok")  # 推送段不受模型影响

    def test_E3_prediction_unavailable_keeps_chain_running(self) -> None:
        """E3 预测段缺失：其余四段照跑，forecast 段报 prediction_unavailable。"""
        with MockVllmServer() as mock:
            service, _, _ = make_service(vllm_base_url=mock.base_url, predict=None)
            run = service.run()
        stages = stage_map(run)
        self.assertEqual(stages["forecast"].status, "degraded")
        self.assertIn("prediction_unavailable", stages["forecast"].detail)
        self.assertIsNone(run.agent.forecast)
        self.assertEqual(run.agent.recommendation.origin, "vllm")  # 建议段仍走模型
        self.assertIn("预测段本次不可用", build_prompt(run.query, None))

    def test_E4_empty_window_table_aborts_pipeline(self) -> None:
        """E4 窗口表为空：第 1 段就 skipped，后四段不执行，且明确标 aborted。"""
        service, _, push_path = make_service(rows=())
        run = service.run()
        self.assertIsNone(run.query)
        self.assertEqual([stage.name for stage in run.stages], ["flink_window"])
        self.assertEqual(run.stages[0].status, "skipped")
        self.assertTrue(any(item.startswith("pipeline_aborted") for item in run.degradations))
        self.assertFalse(push_path.exists())  # 没结果就不往大屏写

    def test_E4b_reader_failure_is_a_degradation_not_a_crash(self) -> None:
        """E4b 读取窗口时抛异常（库挂了/驱动缺失）：报成 skipped，不冒泡成 500。"""
        service, _, _ = make_service(window_error=RuntimeError("psycopg2 未安装"))
        run = service.run()
        self.assertIsNone(run.query)
        self.assertEqual(run.stages[0].status, "skipped")
        self.assertTrue(run.stages[0].detail.startswith("flink_window_failed"))
        self.assertFalse(run.verified)

    def test_E5_screen_polling_reads_push_log_after_restart(self) -> None:
        """E5 大屏轮询：进程内 latest 命中本次结果；「重启」后从 JSONL 回读最后一条。"""
        with tempfile.TemporaryDirectory() as storage:
            push_dir = Path(storage)
            service, _, push_path = make_service(vllm_base_url=None, predict=None, push_dir=push_dir)
            first = service.run().as_dict()
            self.assertEqual(service.latest().query.checkpoint_id, "CP-SOUTH-02")

            restarted, _, _ = make_service(vllm_base_url=None, predict=None, push_dir=push_dir)
            latest = restarted.latest()
            self.assertIsNotNone(latest)
            self.assertEqual(latest.query.checkpoint_id, "CP-SOUTH-02")
            self.assertEqual(latest.degradations, tuple(first["degradations"]))
            self.assertEqual(latest.query.source, "push_log")
            record = json.loads(push_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(record["alert_level"], "PURPLE")
            self.assertEqual(record["recommendation_origin"], "rule_fallback")
            self.assertFalse(record["verified"])

    def test_E6_http_api_contract(self) -> None:
        """E6 HTTP 契约：未启用 503，启用后 /run、/latest、/health 返回统一信封。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from .router import router

        with MockVllmServer() as mock:
            settings = replace(load_integration_settings(), enabled=True, vllm_base_url=mock.base_url, vllm_model=MOCK_MODEL)
            with tempfile.TemporaryDirectory() as storage:
                push_path = Path(storage) / "screen_push.jsonl"

                def build(current: IntegrationSettings) -> IntegrationService:
                    return IntegrationService(
                        current,
                        windows=FakeWindowReader((PURPLE_ROW, GREEN_ROW)),
                        predict=predict_ok,
                        vllm=VllmClient(current),
                        push_path=push_path,
                    )

                service = build(settings)
                app = FastAPI()
                app.include_router(router)
                client = TestClient(app)

                app.state.integration_service = build(replace(settings, enabled=False))
                self.assertEqual(client.post("/api/v1/integration/run").status_code, 503)

                app.state.integration_service = service
                health = client.get("/api/v1/integration/health").json()
                self.assertEqual(health["code"], 200)
                self.assertTrue(health["data"]["vllm"]["reachable"])
                self.assertEqual(health["data"]["vllm"]["models"], [MOCK_MODEL])

                body = client.post("/api/v1/integration/run", params={"checkpoint_id": "CP-NORTH-01"}).json()
                self.assertEqual(body["code"], 200)
                self.assertEqual(body["data"]["query"]["checkpoint_id"], "CP-NORTH-01")
                self.assertEqual(body["data"]["agent"]["recommendation"]["origin"], "vllm")
                self.assertFalse(body["data"]["simulation"])
                self.assertEqual(client.get("/api/v1/integration/latest").json()["data"]["query"]["checkpoint_id"], "CP-NORTH-01")
                self.assertEqual(client.get("/api/v1/integration/pushes").json()["data"]["records"][0]["checkpoint_id"], "CP-NORTH-01")

                missing = client.post("/api/v1/integration/run", params={"checkpoint_id": "CP-NOWHERE"})
                self.assertEqual(missing.status_code, 502)
                self.assertIn("checkpoint_not_found", missing.json()["detail"])


def build_suite() -> unittest.TestSuite:
    return unittest.defaultTestLoader.loadTestsFromTestCase(EndToEndCases)


def _flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    cases: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            cases.extend(_flatten(item))
        else:
            cases.append(item)
    return cases


def main(argv: list[str] | None = None) -> int:
    """跑端到端用例并打表：`python -m backend.integration.e2e_test_suite`。"""
    suite = build_suite()
    cases = _flatten(suite)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    failed = {str(test) for test, _ in result.failures}
    errored = {str(test) for test, _ in result.errors}
    print("\n--- 端到端用例 ---")
    for case in cases:
        description = (case.shortDescription() or case.id()).splitlines()[0]
        outcome = "错误" if str(case) in errored else "失败" if str(case) in failed else "通过"
        print(f"  {description:<56} {outcome}")
    print(f"\n合计 {result.testsRun} 条：失败 {len(result.failures)}，错误 {len(result.errors)}")
    print("说明：vLLM 端点与窗口数据都是 Mock/夹具，本套件只证明接线正确，不构成真机或性能结论。")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
