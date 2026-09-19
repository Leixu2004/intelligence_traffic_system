import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.agent.tools import TrafficToolbox, TrafficToolGateway, finish_tool_trace, start_tool_trace
from backend.crew.notice import PublicNoticeSink
from backend.crew.routing import RoutePlanner
from backend.crew.tools import build_crew_tools
from backend.crew.tools.notify import _channels
from backend.crew.tools.route import PlanDetourRouteTool

CORRIDORS = {
    "route_fallback": {
        "corridors": [
            {"id": "A", "name": "测试走廊", "waypoints": [[116.40, 39.90], [116.45, 39.93]], "distance_km": 10, "eta_minutes": 15}
        ]
    }
}


def _healthy_gateway():
    rows = [{"checkpoint_id": "CP-NORTH-01", "passenger_vehicle_flow_number": 120, "time": "2026-08-23 14:00:00"}]
    return TrafficToolGateway(
        traffic_records=lambda checkpoint_id, limit: (rows, "TimescaleDB", ""),
        detection_records=lambda checkpoint_id, limit: (rows, "TimescaleDB", ""),
        predict_checkpoint=lambda checkpoint_id, steps: {"predicted": [{"step": 1, "flow": 90}]},
        law_search=lambda question, top_k: {"citations": [{"article": "第47条"}], "answer": "需在来车方向设警告标志"},
    )


def _broken_gateway():
    def boom(*_args):
        raise RuntimeError("数据源不可达")

    return TrafficToolGateway(
        traffic_records=boom, detection_records=boom, predict_checkpoint=boom, law_search=boom
    )


def _tools(gateway, root, default_origin=(116.4074, 39.9042)):
    planner = RoutePlanner.from_config(CORRIDORS, mode="static", amap_key="", timeout_seconds=5)
    sink = PublicNoticeSink(Path(root) / "notices.jsonl")
    return build_crew_tools(
        toolbox=TrafficToolbox(gateway),
        planner=planner,
        sink=sink,
        event_id="EV-TOOL",
        default_origin=default_origin,
    )


class CrewToolTests(unittest.TestCase):
    def test_role_tool_split_matches_courseware_architecture(self):
        with TemporaryDirectory() as tmp:
            tools = _tools(_healthy_gateway(), tmp)
        self.assertEqual(
            [t.name for t in tools["commander"]],
            ["query_checkpoint_flow", "query_peak_period", "publish_public_notice"],
        )
        self.assertEqual(
            [t.name for t in tools["analyst"]],
            ["query_checkpoint_flow", "query_peak_period", "predict_checkpoint_flow", "search_traffic_law"],
        )
        self.assertEqual([t.name for t in tools["dispatcher"]], ["plan_detour_route", "publish_public_notice"])

    def test_tool_names_are_provider_safe_and_descriptions_stay_chinese(self):
        """CrewAI 会把工具名转成 OpenAI function name：非 ASCII 名会在 kickoff 阶段抛 ValueError。"""
        with TemporaryDirectory() as tmp:
            tools = _tools(_healthy_gateway(), tmp)
        for group in tools.values():
            for tool in group:
                self.assertRegex(tool.name, r"^[a-z_][a-z0-9_]{0,63}$")
                self.assertRegex(tool.description, r"[\u4e00-\u9fff]")

    def test_data_tools_return_real_source_on_healthy_gateway(self):
        with TemporaryDirectory() as tmp:
            flow, peak, predict, law = _tools(_healthy_gateway(), tmp)["analyst"]
            payload = json.loads(flow._run("CP-NORTH-01"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["checkpoint_id"], "CP-NORTH-01")
            self.assertTrue(json.loads(peak._run("CP-NORTH-01", 24))["ok"])
            self.assertIn("predicted", json.loads(predict._run("CP-NORTH-01", 1)))
            self.assertEqual(json.loads(law._run("高速故障如何设警告标志"))["citations"][0]["article"], "第47条")

    def test_tool_failures_degrade_to_json_instead_of_raising(self):
        """课件验收第 7 条：工具失败必须返回可继续推理的结果，而不是中断协作。"""
        with TemporaryDirectory() as tmp:
            for tool in _tools(_broken_gateway(), tmp)["analyst"]:
                payload = json.loads(tool._run("CP-NORTH-01", 3))
                self.assertFalse(payload["ok"], tool.name)
                self.assertTrue(payload["message"], tool.name)

    def test_missing_arguments_are_rejected_without_calling_data_layer(self):
        def explode(*_args):
            raise AssertionError("数据层不应被调用")

        with TemporaryDirectory() as tmp:
            tools = _tools(
                TrafficToolGateway(
                    traffic_records=explode, detection_records=explode, predict_checkpoint=explode, law_search=explode
                ),
                tmp,
            )
            for tool in tools["analyst"]:
                payload = json.loads(tool._run())
                self.assertEqual(payload["source"], "input_invalid", tool.name)
                self.assertFalse(payload["ok"], tool.name)

    def test_route_tool_falls_back_to_marked_demonstration_plan(self):
        with TemporaryDirectory() as tmp:
            route = _tools(_healthy_gateway(), tmp)["dispatcher"][0]
            payload = json.loads(route._run("116.40", "39.90"))
            self.assertTrue(payload["ok"])
            # 未经真实路网核验的路线必须显式标注，避免被当成导航结论。
            self.assertFalse(payload["verified"])
            self.assertEqual(payload["source"], "demonstration_topology")
            self.assertEqual(payload["name"], "测试走廊")

    def test_route_tool_uses_default_origin_and_survives_planner_errors(self):
        with TemporaryDirectory() as tmp:
            route = _tools(_healthy_gateway(), tmp)["dispatcher"][0]
            payload = json.loads(route._run())
            self.assertTrue(payload["ok"])
            self.assertIsNotNone(payload["waypoints"])
            route.planner = None
            failed = json.loads(route._run("1", "2"))
            self.assertFalse(failed["ok"])
            self.assertEqual(failed["source"], "route_error")
            self.assertFalse(failed["verified"])

    def test_notice_tool_records_simulation_and_accepts_flexible_channels(self):
        with TemporaryDirectory() as tmp:
            notice = _tools(_healthy_gateway(), tmp)["dispatcher"][1]
            payload = json.loads(notice._run("EV-9", "绕行通告", "短信, APP推送,"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["channels"], ["短信", "APP推送"])
            self.assertTrue(payload["simulation"])
            self.assertFalse(payload["delivered"])
            self.assertEqual(json.loads(notice._run("EV-9", "列表渠道", ["交通广播"]))["channels"], ["交通广播"])
            self.assertEqual(json.loads(notice._run("EV-9", "  "))["source"], "input_invalid")
            notice.sink = None
            self.assertEqual(json.loads(notice._run("EV-9", "正文"))["source"], "notice_error")

    def test_channel_coercion_handles_none_and_empty_values(self):
        self.assertIsNone(_channels(None))
        self.assertIsNone(_channels(""))
        self.assertIsNone(_channels([" ", ""]))
        self.assertEqual(_channels("a,,b"), ["a", "b"])

    def test_route_tool_reports_failure_without_any_usable_input(self):
        route = PlanDetourRouteTool(
            planner=RoutePlanner.from_config({}, mode="static", amap_key="", timeout_seconds=5),
            default_origin=None,
        )
        payload = json.loads(route._run("经度未知", "lat"))
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["verified"])
        self.assertTrue(payload.get("error"))

    def test_route_and_notice_tools_write_the_shared_tool_evidence(self):
        """路线与通告不经过 TrafficToolbox，必须自己写入证据链，否则接口的 tool_evidence 会缺两条。"""
        with TemporaryDirectory() as tmp:
            route, notice = _tools(_healthy_gateway(), tmp)["dispatcher"]
            token = start_tool_trace()
            json.loads(route._run("116.40", "39.90"))
            json.loads(notice._run("EV-9", "绕行通告", "短信"))
            route.planner = None
            json.loads(route._run("1", "2"))
            evidence = finish_tool_trace(token)

        self.assertEqual(
            [item.name for item in evidence], ["plan_detour_route", "publish_public_notice", "plan_detour_route"]
        )
        self.assertTrue(evidence[0].ok)
        self.assertEqual(evidence[0].source, "demonstration_topology")
        self.assertEqual(evidence[0].arguments["origin"], [116.4, 39.9])
        self.assertTrue(evidence[1].ok)
        self.assertEqual(evidence[1].source, "local_file")
        self.assertIn("NT-", evidence[1].summary)
        self.assertEqual(evidence[1].arguments, {"event_id": "EV-9", "channels": ["短信"]})
        self.assertFalse(evidence[2].ok)
        self.assertEqual(evidence[2].source, "route_error")

    def test_notice_evidence_marks_write_failure_as_notice_error(self):
        class _FailingSink:
            def publish(self, **_kwargs):
                raise OSError("磁盘不可写")

        with TemporaryDirectory() as tmp:
            notice = _tools(_healthy_gateway(), tmp)["dispatcher"][1]
            notice.sink = _FailingSink()
            token = start_tool_trace()
            payload = json.loads(notice._run("EV-9", "正文"))
            evidence = finish_tool_trace(token)
        self.assertEqual(payload["source"], "notice_error")
        self.assertFalse(evidence[0].ok)
        self.assertEqual(evidence[0].source, "notice_error")

    def test_evidence_is_skipped_when_no_trace_is_active(self):
        """独立调用（非 Crew 运行）时没有 trace 上下文，工具仍须正常返回。"""
        with TemporaryDirectory() as tmp:
            route = _tools(_healthy_gateway(), tmp)["dispatcher"][0]
            self.assertTrue(json.loads(route._run())["ok"])


if __name__ == "__main__":
    unittest.main()
