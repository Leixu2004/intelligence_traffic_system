import unittest

from backend.crew.routing import Corridor, RoutePlanner, haversine_km

CONFIG = {
    "route_fallback": {
        "corridors": [
            {
                "id": "A",
                "name": "近距走廊",
                "waypoints": [[116.40, 39.90], [116.45, 39.93]],
                "distance_km": 12.5,
                "eta_minutes": 20,
                "note": "测试",
            },
            {
                "id": "B",
                "name": "远距走廊",
                "waypoints": [[113.36, 23.12], [113.44, 23.15]],
            },
            {"id": "C", "name": "点不足", "waypoints": [[1.0, 2.0]]},
        ]
    }
}


def _planner(**kwargs):
    kwargs.setdefault("mode", "auto")
    kwargs.setdefault("amap_key", "")
    kwargs.setdefault("timeout_seconds", 5)
    return RoutePlanner.from_config(CONFIG, **kwargs)


class RoutingTests(unittest.TestCase):
    def test_haversine_matches_known_distance(self):
        self.assertAlmostEqual(haversine_km((116.40, 39.90), (116.40, 40.80)), 100.1, delta=1.0)

    def test_declared_corridor_metrics_are_preserved(self):
        plan = _planner().plan((116.41, 39.91), None)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.name, "近距走廊")
        self.assertEqual(plan.distance_km, 12.5)
        self.assertEqual(plan.eta_minutes, 20)

    def test_missing_metrics_fall_back_to_polyline_and_average_speed(self):
        planner = _planner()
        corridors = [c for c in planner.corridors if c.corridor_id == "B"]
        self.assertEqual(len(corridors), 1)
        self.assertIsNone(corridors[0].declared_distance_km)
        plan = RoutePlanner(corridors).plan((113.37, 23.13), None)
        self.assertGreater(plan.distance_km, 0)
        self.assertGreater(plan.eta_minutes, 0)

    def test_under_two_waypoint_corridor_is_dropped(self):
        self.assertEqual([c.corridor_id for c in _planner().corridors], ["A", "B"])

    def test_static_plan_is_always_marked_unverified(self):
        plan = _planner(mode="static").plan((116.40, 39.90), (120.0, 40.0)).as_dict()
        self.assertFalse(plan["verified"])
        self.assertEqual(plan["source"], "demonstration_topology")
        self.assertIn("未经真实路网核验", plan["note"])
        self.assertEqual(len(plan["waypoints"]), 2)

    def test_amap_mode_without_key_degrades_to_static_not_error(self):
        plan = _planner(mode="amap").plan((116.40, 39.90), None)
        self.assertTrue(plan.ok)
        self.assertEqual(plan.source, "demonstration_topology")

    def test_without_origin_and_corridors_returns_explicit_failure(self):
        plan = RoutePlanner([Corridor("X", "空", ())]).plan(None, None)
        self.assertFalse(plan.ok)
        self.assertIsNotNone(plan.error)

    def test_empty_config_returns_failure_with_reason(self):
        plan = RoutePlanner.from_config({}, mode="auto", amap_key="", timeout_seconds=5).plan(None, None)
        self.assertFalse(plan.ok)
        self.assertIn("演示走廊", plan.error or "")


if __name__ == "__main__":
    unittest.main()
