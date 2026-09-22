import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.agent.tools import TrafficToolGateway
from backend.crew.contracts import CrewRunResult
from backend.crew.service import CrewService

from .support import SAMPLE_EVENT, make_service, make_settings


class CrewServiceTests(unittest.TestCase):
    def test_disabled_service_reports_reason_and_degrades(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp, TRAFFIC_CREW_ENABLED="false")
            self.assertFalse(service.available)
            health = service.health()
            self.assertIn("TRAFFIC_CREW_ENABLED", health["error"])
            self.assertFalse(health["available"])
            self._assert_degraded(service.respond(SAMPLE_EVENT))
            self.assertFalse(json.loads(service.audit.path.read_text(encoding="utf-8"))["ok"])

    def test_missing_key_blocks_availability_without_raising(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp, TRAFFIC_CREW_LLM_API_KEY="")
            self.assertFalse(service.available)
            self.assertIn("密钥", service.health()["error"])
            self._assert_degraded(service.respond(SAMPLE_EVENT))

    def test_missing_crewai_dependency_is_reported(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp)
            self.assertTrue(service.available)
            with patch.dict(sys.modules, {"crewai": None}):
                rebuilt = CrewService(service.settings, service.profile, service.toolbox, service.planner, service.sink)
            self.assertFalse(rebuilt.available)
            self.assertIn("crewai", rebuilt.health()["error"].lower())

    def test_health_exposes_configuration_for_the_dashboard(self):
        with TemporaryDirectory() as tmp:
            health = make_service(tmp).health()
        self.assertEqual(health["process"], "hierarchical")
        self.assertEqual(health["model"], "deepseek-v4-flash-0731")
        self.assertEqual(health["route_mode"], "auto")
        self.assertFalse(health["amap_configured"])
        self.assertGreaterEqual(health["fallback_corridors"], 1)
        self.assertEqual(health["notify_channels"], ["短信", "APP推送", "交通广播"])
        self.assertIsNone(health["error"])

    def test_settings_are_resolved_from_environment_only_for_secrets(self):
        with TemporaryDirectory() as tmp:
            settings = make_settings(tmp)
            self.assertEqual(settings.api_key, "test-key")
            self.assertEqual(settings.audit_path, Path(tmp) / "crew_runs.jsonl")
            self.assertTrue(settings.configured)

    def test_collaboration_failure_degrades_instead_of_bubbling_up(self):
        with TemporaryDirectory() as tmp:
            service = make_service(tmp)
            with patch("backend.crew.crew_system.run_crew", side_effect=RuntimeError("模型超时")):
                result = service.respond(SAMPLE_EVENT)
        self._assert_degraded(result)
        self.assertIn("RuntimeError", result.degradation)

    def test_successful_run_is_audited_with_tool_evidence_count(self):
        outcome = CrewRunResult(
            ok=True,
            process="hierarchical",
            event=SAMPLE_EVENT,
            report="处置方案正文",
            tool_evidence=[{"name": "查询卡口历史流量", "ok": True}],
            elapsed_ms=12.5,
            simulation=True,
        )
        with TemporaryDirectory() as tmp:
            service = make_service(tmp)
            with patch("backend.crew.crew_system.run_crew", return_value=outcome):
                result = service.respond(SAMPLE_EVENT)
            record = json.loads(service.audit.path.read_text(encoding="utf-8"))
        self.assertTrue(result.ok)
        self.assertTrue(result.simulation)
        self.assertEqual(record["event_id"], "EV-TEST-1")
        self.assertEqual(record["tool_calls"], 1)

    def test_broken_data_layer_still_builds_a_service(self):
        def boom(*_args):
            raise RuntimeError("TimescaleDB 未就绪")

        with TemporaryDirectory() as tmp:
            service = CrewService.build(
                make_settings(tmp),
                gateway=TrafficToolGateway(
                    traffic_records=boom, detection_records=boom, predict_checkpoint=boom, law_search=boom
                ),
            )
            self.assertTrue(service.available)
            self.assertEqual(service.health()["fallback_corridors"], 3)

    def test_build_attaches_planner_to_the_toolbox_gateway(self):
        """回归：toolbox 持有构造时的网关引用，planner 必须在建 toolbox 之前回填进网关。

        早先 dashboard_api 在 CrewService.build 之后再 replace 一份新网关，导致
        /api/v1/crew/route/plan 走 Crew 自己的 toolbox 时 planner 仍是 None，返回 503「路径规划器未接入」。
        """
        with TemporaryDirectory() as tmp:
            service = CrewService.build(
                make_settings(tmp),
                gateway=TrafficToolGateway(
                    traffic_records=lambda *a: [],
                    detection_records=lambda *a: [],
                    predict_checkpoint=lambda *a: {},
                    law_search=lambda *a: [],
                ),
            )
            self.assertIs(service.toolbox.gateway.route_planner, service.planner)
            payload = json.loads(service.toolbox.plan_route("113.361", "23.129", "113.307", "23.387"))
            self.assertTrue(payload["ok"], payload.get("message"))
            self.assertEqual(payload["source"], "demonstration_topology")
            self.assertFalse(payload["verified"])

    def _assert_degraded(self, result: CrewRunResult) -> None:
        self.assertFalse(result.ok)
        self.assertTrue(result.degradation)
        self.assertTrue(result.simulation)
        self.assertEqual(result.report, "")
        self.assertTrue(result.limitations)


if __name__ == "__main__":
    unittest.main()
