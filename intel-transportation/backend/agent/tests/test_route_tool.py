"""plan_route 工具：坐标校验、可信度标记与证据链的行为约束。"""

import json
import unittest

from backend.agent.tools import (
    TrafficToolbox,
    TrafficToolGateway,
    finish_tool_trace,
    start_tool_trace,
)


class _StubPlan:
    def __init__(self, **overrides):
        self.payload = {
            "ok": True,
            "source": "amap_v5",
            "verified": True,
            "name": "天河路—机场高速",
            "distance_km": 32.4,
            "eta_minutes": 41.0,
            "waypoints": [[113.3252, 23.133], [113.307, 23.387]],
            "note": "",
            "error": None,
        }
        self.payload.update(overrides)

    def as_dict(self):
        return dict(self.payload)


class _StubPlanner:
    def __init__(self, plan=None, error=None):
        self.plan_result = plan or _StubPlan()
        self.plan_error = error
        self.calls = []

    def plan(self, origin, destination=None):
        self.calls.append((origin, destination))
        if self.plan_error:
            raise self.plan_error
        return self.plan_result


def _toolbox(planner=None):
    return TrafficToolbox(
        TrafficToolGateway(
            traffic_records=lambda checkpoint_id, limit: ([], "TimescaleDB", ""),
            detection_records=lambda checkpoint_id, limit: ([], "TimescaleDB", ""),
            predict_checkpoint=lambda checkpoint_id, future_steps: {},
            route_planner=planner,
        )
    )


class PlanRouteToolTests(unittest.TestCase):
    def test_missing_planner_degrades_without_fabricating_a_route(self):
        payload = json.loads(_toolbox().plan_route("113.32", "23.13"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "RoutePlanner")
        self.assertIn("未接入", payload["message"])

    def test_out_of_range_and_non_numeric_coordinates_are_rejected(self):
        toolbox = _toolbox(_StubPlanner())
        for arguments in (("200", "23.13", "", ""), ("abc", "23.13", "", "")):
            payload = json.loads(toolbox.plan_route(*arguments))
            self.assertFalse(payload["ok"], arguments)
            self.assertEqual(payload["source"], "validation", arguments)

    def test_verified_route_keeps_source_and_records_evidence(self):
        planner = _StubPlanner()
        toolbox = _toolbox(planner)
        token = start_tool_trace()
        payload = json.loads(
            toolbox.plan_route("113.3252", "23.1330", "113.3070", "23.3870")
        )
        evidence = finish_tool_trace(token)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["verified"])
        self.assertEqual(payload["source"], "amap_v5")
        self.assertEqual(
            planner.calls, [((113.3252, 23.133), (113.307, 23.387))]
        )
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].name, "plan_route")
        self.assertEqual(evidence[0].source, "amap_v5")
        self.assertTrue(evidence[0].ok)
        self.assertIn("verified=True", evidence[0].summary)

    def test_fallback_corridor_is_reported_as_unverified(self):
        planner = _StubPlanner(_StubPlan(source="static_corridor", verified=False))
        payload = json.loads(_toolbox(planner).plan_route("113.3252", "23.1330"))
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["verified"])
        self.assertEqual(payload["source"], "static_corridor")
        self.assertEqual(planner.calls[0][1], None)

    def test_planner_runtime_error_is_reported_not_raised(self):
        toolbox = _toolbox(_StubPlanner(error=RuntimeError("高德接口超时")))
        payload = json.loads(toolbox.plan_route("113.3252", "23.1330"))
        self.assertFalse(payload["ok"])
        self.assertIn("高德接口超时", payload["message"])


if __name__ == "__main__":
    unittest.main()
