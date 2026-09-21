"""管线单元：AgentPipeline 的降级记账、预测适配、提示词与 vLLM 客户端。"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.integration.agent_pipeline import (
    RULE_FALLBACK,
    AgentPipeline,
    ForecastResult,
    QueryResult,
    Recommendation,
    worst_level,
)
from backend.integration.config import load_integration_settings
from backend.integration.e2e_test_suite import (
    GREEN_ROW,
    MOCK_MODEL,
    PURPLE_ROW,
    FakeWindowReader,
    MockVllmServer,
    closed_port_base_url,
)
from backend.integration.service import (
    STAGES,
    IntegrationRun,
    IntegrationService,
    StageResult,
    build_prompt,
    forecast_from_mapping,
    read_pushes,
    run_from_push,
    stage_map,
)
from backend.integration.vllm_client import VllmClient, VllmUnavailableError

QUERY = QueryResult(
    checkpoint_id="CP-SOUTH-02",
    camera_id="CAM-02",
    window_start="2026-09-21T14:20:00",
    avg_speed=14.4,
    max_speed=30.0,
    vehicle_count=9,
    alert_level="PURPLE",
    source="timescaledb",
)


def _forecast(steps: int = 5) -> ForecastResult:
    return ForecastResult(
        checkpoint_id=QUERY.checkpoint_id,
        current_flow=120.0,
        forecast=tuple(120.0 - 8.0 * index for index in range(steps)),
        unit="vehicles/300s",
        model="lstm-onnx",
        backend="onnxruntime",
        latency_ms=9,
        verified=True,
    )


_UNSET = object()


class AgentPipelineTest(unittest.TestCase):
    def _pipeline(self, *, predict: object = _UNSET, advise=None) -> AgentPipeline:
        return AgentPipeline(
            query=lambda _checkpoint: QUERY,
            predict=(lambda _cp, steps: _forecast(steps)) if predict is _UNSET else predict,  # type: ignore[arg-type]
            advise=advise if advise is not None else (lambda _q, _f: Recommendation(text="模型建议", origin="vllm", model=MOCK_MODEL)),
        )

    def test_happy_path_is_verified(self) -> None:
        run = self._pipeline().run(QUERY.checkpoint_id)
        self.assertEqual((), run.degradations)
        self.assertTrue(run.verified)
        self.assertEqual("vllm", run.recommendation.origin)
        self.assertEqual(5, len(run.forecast.forecast))

    def test_missing_prediction_is_recorded(self) -> None:
        run = self._pipeline(predict=None).run(QUERY.checkpoint_id)
        self.assertIsNone(run.forecast)
        self.assertTrue(any(item.startswith("prediction_unavailable") for item in run.degradations))
        self.assertFalse(run.verified)

    def test_failing_prediction_does_not_break_the_chain(self) -> None:
        def boom(_cp: str, _steps: int) -> ForecastResult:
            raise ValueError("没有可用于预测的流量记录")

        run = self._pipeline(predict=boom).run(QUERY.checkpoint_id)
        self.assertIsNone(run.forecast)
        self.assertIn("prediction_failed:ValueError", run.degradations)
        self.assertEqual("vllm", run.recommendation.origin)  # 建议段照跑

    def test_unverified_forecast_is_flagged(self) -> None:
        def placeholder(_cp: str, _steps: int) -> ForecastResult:
            return replace(_forecast(), verified=False)

        run = self._pipeline(predict=placeholder).run(QUERY.checkpoint_id)
        self.assertIn("forecast_verified=False（模型/数据未达验收口径）", run.degradations)

    def test_advise_failure_falls_back_to_rule_template_per_level(self) -> None:
        def boom(*_args: object) -> Recommendation:
            raise RuntimeError("端点挂了")

        run = self._pipeline(advise=boom).run(QUERY.checkpoint_id)
        self.assertEqual("rule_fallback", run.recommendation.origin)
        self.assertEqual(RULE_FALLBACK["PURPLE"], run.recommendation.text)
        self.assertIn("recommendation=rule_fallback（模板文案，非模型生成）", run.degradations)

    def test_simulation_source_query_is_always_a_degradation(self) -> None:
        pipeline = AgentPipeline(
            query=lambda _cp: replace(QUERY, source="simulation"),
            predict=lambda _cp, steps: _forecast(steps),
            advise=lambda _q, _f: Recommendation(text="x", origin="vllm"),
        )
        run = pipeline.run(QUERY.checkpoint_id)
        self.assertIn("query_source=simulation（非库侧真实数据）", run.degradations)
        self.assertFalse(run.verified)

    def test_steps_order_and_run_shape(self) -> None:
        run = self._pipeline().run(QUERY.checkpoint_id)
        self.assertEqual(["query_traffic", "predict_flow", "generate_plan"], run.as_dict()["steps"])
        self.assertGreaterEqual(run.latency_ms, 1)

    def test_worst_level(self) -> None:
        self.assertEqual("PURPLE", worst_level(["GREEN", "AMBER", "RED", "PURPLE"]))
        self.assertEqual("RED", worst_level(["RED", "AMBER", "无关"]))
        self.assertEqual("GREEN", worst_level([]))


class ForecastAdapterTest(unittest.TestCase):
    def test_empty_payload_is_not_verified(self) -> None:
        result = forecast_from_mapping("CP-01", {"forecast": [], "model": "lstm-onnx"})
        self.assertEqual((), result.forecast)
        self.assertFalse(result.verified)

    def test_mock_backend_is_never_verified(self) -> None:
        result = forecast_from_mapping("CP-01", {"forecast": [10, 11], "model": "mock-baseline", "unit": "辆/300s"})
        self.assertEqual((10.0, 11.0), result.forecast)
        self.assertFalse(result.verified)
        self.assertEqual("辆/300s", result.unit)

    def test_real_model_output_is_verified(self) -> None:
        result = forecast_from_mapping("CP-01", {"forecast": [10.5, 11.25], "model": "lstm-onnx", "backend": "onnxruntime", "latency_ms": 8, "unit": "辆"})
        self.assertTrue(result.verified)
        self.assertEqual(8, result.latency_ms)
        self.assertEqual("onnxruntime", result.backend)

    def test_explicit_simulation_flag_wins(self) -> None:
        self.assertFalse(forecast_from_mapping("CP-01", {"forecast": [3], "model": "lstm-onnx", "simulation": True}).verified)


class PromptTest(unittest.TestCase):
    def test_prompt_carries_window_and_forecast_facts(self) -> None:
        prompt = build_prompt(QUERY, _forecast(3))
        self.assertIn("CAM-02", prompt)
        self.assertIn("14.4 km/h", prompt)
        self.assertIn("PURPLE", prompt)
        self.assertIn("预测未来 3 步", prompt)
        self.assertIn("120", prompt)
        self.assertIn("vehicles/300s", prompt)

    def test_prompt_states_missing_prediction_instead_of_omitting_it(self) -> None:
        self.assertIn("预测段本次不可用", build_prompt(QUERY, None))
        self.assertIn("预测段本次不可用", build_prompt(QUERY, replace(_forecast(), forecast=())))


class PushLogTest(unittest.TestCase):
    def test_read_pushes_skips_bad_lines_and_returns_newest_first(self) -> None:
        with TemporaryDirectory() as storage:
            path = Path(storage) / "screen_push.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"checkpoint_id": "CP-01"}),
                        "这一行不是 JSON",
                        "",
                        json.dumps({"checkpoint_id": "CP-02"}),
                        json.dumps({"checkpoint_id": "CP-03"}),
                    ]
                ),
                encoding="utf-8",
            )
            records = read_pushes(path, limit=2)
            self.assertEqual(["CP-03", "CP-02"], [item["checkpoint_id"] for item in records])
            self.assertEqual([], read_pushes(Path(storage) / "missing.jsonl"))

    def test_run_from_push_marks_source_and_keeps_degradations(self) -> None:
        run = run_from_push(
            {
                "time": "2026-09-21T06:00:00Z",
                "checkpoint_id": "CP-01",
                "camera_id": "CAM-01",
                "window_start": "2026-09-21T14:20:00",
                "alert_level": "RED",
                "avg_speed": 18.2,
                "vehicle_count": 7,
                "degradations": ["vllm_unreachable:ConnectError"],
                "verified": False,
            }
        )
        self.assertEqual("push_log", run.query.source)
        self.assertEqual(("vllm_unreachable:ConnectError",), run.degradations)
        self.assertFalse(run.verified)
        self.assertEqual(5, len(run.stages))


class ServiceHealthTest(unittest.TestCase):
    def _service(self, **kwargs: object) -> IntegrationService:
        settings = load_integration_settings()
        from backend.integration.e2e_test_suite import FakeWindowReader

        return IntegrationService(
            settings,
            windows=FakeWindowReader(()),
            vllm=VllmClient(replace(settings, vllm_base_url=closed_port_base_url())),
            **kwargs,  # type: ignore[arg-type]
        )

    def test_health_lists_stages_and_reasons(self) -> None:
        health = self._service().health()
        self.assertEqual(
            ["flink_window", "forecast", "vllm_analysis", "agent_plan", "screen_push"],
            health["stages"],
        )
        self.assertEqual("unavailable", health["prediction"])
        self.assertFalse(health["vllm"]["reachable"])
        self.assertEqual("ConnectError", health["vllm"]["reason"])

    def test_stage_map_indexes_by_name(self) -> None:
        run = IntegrationRun(
            started_at="2026-09-21T06:00:00Z",
            stages=(StageResult("flink_window", "ok"), StageResult("forecast", "degraded")),
            query=QUERY,
            agent=None,
            push_path="p",
            push_verified=True,
        )
        self.assertEqual({"flink_window", "forecast"}, set(stage_map(run)))
        self.assertFalse(run.as_dict()["verified"])


class VllmClientTest(unittest.TestCase):
    def _client(self, base_url: str, **overrides: object) -> VllmClient:
        settings = replace(load_integration_settings(), **overrides)
        settings = replace(settings, vllm_base_url=base_url, vllm_model=MOCK_MODEL)
        return VllmClient(settings)

    def test_health_against_mock_endpoint(self) -> None:
        with MockVllmServer() as mock:
            client = self._client(mock.base_url)
            health = client.health()
            self.assertTrue(health["reachable"])
            self.assertEqual([MOCK_MODEL], health["models"])
            self.assertTrue(health["served_model_present"])
            client.close()

    def test_unreachable_endpoint_reports_reason_without_raising(self) -> None:
        client = self._client(closed_port_base_url())
        health = client.health()
        self.assertFalse(health["reachable"])
        self.assertEqual("ConnectError", health["reason"])
        client.close()

    def test_chat_returns_completion_with_latency_and_usage(self) -> None:
        with MockVllmServer() as mock:
            client = self._client(mock.base_url)
            completion = client.analyze("给出紫级处置建议")
            self.assertEqual(MOCK_MODEL, completion.model)
            self.assertIn("Mock", completion.text)
            self.assertGreaterEqual(completion.latency_ms, 1)
            self.assertEqual("/chat/completions", completion.endpoint)
            self.assertEqual(1, completion.completion_tokens)
            client.close()

    def test_unconfigured_client_raises_before_any_request(self) -> None:
        client = self._client("")
        with self.assertRaises(VllmUnavailableError):
            client.chat([{"role": "user", "content": "hi"}])
        self.assertFalse(client.health()["configured"])
        client.close()

    def test_retries_are_bounded_and_reported(self) -> None:
        client = self._client(closed_port_base_url(), max_retries=2)
        with self.assertRaises(VllmUnavailableError) as caught:
            client.analyze("hi")
        self.assertIn("尝试 3 次", str(caught.exception))
        client.close()

    def test_empty_content_is_rejected(self) -> None:
        client = self._client("http://127.0.0.1:1/v1")
        with self.assertRaises(VllmUnavailableError):
            client._to_completion({"choices": [{"message": {"content": "   "}}]}, latency_ms=1)
        with self.assertRaises(VllmUnavailableError):
            client._to_completion("不是对象", latency_ms=1)
        client.close()


class IntegrationServiceTest(unittest.TestCase):
    """服务编排层：降级记账、推送落盘与 aborted 路径（HTTP 契约在 e2e 套件里）。"""

    def _service(
        self,
        *,
        rows: tuple = (PURPLE_ROW, GREEN_ROW),
        predict=None,
        vllm=None,
        screen_url: str | None = None,
        push_path: Path | None = None,
    ) -> IntegrationService:
        return IntegrationService(
            load_integration_settings(),
            windows=FakeWindowReader(rows, source="timescaledb"),
            predict=predict,
            vllm=vllm,
            screen_url=screen_url,
            push_path=push_path,
        )

    def test_unknown_checkpoint_aborts_at_stage_1(self) -> None:
        with TemporaryDirectory() as storage:
            push_path = Path(storage) / "screen_push.jsonl"
            run = self._service(push_path=push_path).run("CP-NOWHERE")
        self.assertEqual(["flink_window"], [stage.name for stage in run.stages])
        self.assertEqual("skipped", run.stages[0].status)
        self.assertIn("checkpoint_not_found:CP-NOWHERE", run.stages[0].detail)
        self.assertTrue(any(item.startswith("pipeline_aborted") for item in run.degradations))
        self.assertFalse(run.verified)
        self.assertFalse(push_path.exists())  # 没结果就不往大屏写

    def test_stage_order_matches_pipeline_definition(self) -> None:
        with MockVllmServer() as mock:
            client = VllmClient(replace(load_integration_settings(), vllm_base_url=mock.base_url, vllm_model=MOCK_MODEL))
            with TemporaryDirectory() as storage:
                run = self._service(
                    predict=lambda _cp, steps: _forecast(steps),
                    vllm=client,
                    push_path=Path(storage) / "screen_push.jsonl",
                ).run()
        self.assertEqual(list(STAGES), [stage.name for stage in run.stages])
        self.assertEqual(list(STAGES), run.as_dict()["pipeline"])
        self.assertEqual(["ok"] * 5, [stage.status for stage in run.stages])
        self.assertTrue(run.verified)
        client.close()

    def test_push_record_carries_fallback_origin_and_verified_flag(self) -> None:
        with TemporaryDirectory() as storage:
            push_path = Path(storage) / "screen_push.jsonl"
            run = self._service(push_path=push_path).run()  # 无 vLLM、无预测：建议必回落规则模板
            self.assertEqual("rule_fallback", run.agent.recommendation.origin)
            record = json.loads(push_path.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual("CP-SOUTH-02", record["checkpoint_id"])
            self.assertEqual("PURPLE", record["alert_level"])
            self.assertEqual("rule_fallback", record["recommendation_origin"])
            self.assertFalse(record["verified"])
            self.assertTrue(any(item.startswith("advise_failed") for item in record["degradations"]))

    def test_screen_webhook_failure_is_recorded_but_push_survives(self) -> None:
        with TemporaryDirectory() as storage:
            push_path = Path(storage) / "screen_push.jsonl"
            run = self._service(screen_url="http://127.0.0.1:1/never", push_path=push_path).run()
            self.assertIn("screen_webhook_unreachable（已落盘，大屏改走轮询）", run.degradations)
            push_stage = stage_map(run)["screen_push"]
            self.assertEqual("ok", push_stage.status)
            self.assertIn("webhook=failed", push_stage.detail)
            self.assertTrue(push_path.is_file())


if __name__ == "__main__":
    unittest.main()
