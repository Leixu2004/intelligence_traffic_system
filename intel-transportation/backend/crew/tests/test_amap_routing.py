"""高德路径与降级链路：verified 标记必须只在真实路网返回时才为真。"""

import unittest
from unittest.mock import patch

from backend.crew.routing import RoutePlanner

CONFIG = {
    "route_fallback": {
        "corridors": [
            {"id": "A", "name": "备用走廊", "waypoints": [[116.40, 39.90], [116.45, 39.93]], "distance_km": 10, "eta_minutes": 15}
        ]
    }
}

ORIGIN = (116.4074, 39.9042)
DESTINATION = (116.6390, 39.6710)


def _planner(mode="auto", amap_key="fake-key"):
    return RoutePlanner.from_config(CONFIG, mode=mode, amap_key=amap_key, timeout_seconds=5)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


AMAP_OK = {
    "status": "1",
    "route": {
        "paths": [
            {
                "distance": "28460",
                "duration": "2100",
                "steps": [{"instruction": "沿 G2 行驶 5 公里"}, {"instruction": "右转进入 G15"}],
            }
        ]
    },
}


class AmapRoutingTests(unittest.TestCase):
    def test_verified_plan_comes_only_from_a_successful_amap_call(self):
        with patch("requests.get", return_value=_Response(AMAP_OK)):
            result = _planner().plan(ORIGIN, DESTINATION)
        self.assertTrue(result.ok)
        self.assertTrue(result.verified)
        self.assertEqual(result.source, "amap_v5")
        self.assertAlmostEqual(result.distance_km, 28.46)
        self.assertAlmostEqual(result.eta_minutes, 35.0)
        self.assertIn("沿 G2 行驶", result.note)

    def test_amap_business_error_degrades_to_static_and_says_so(self):
        payload = {"status": "0", "info": "USERKEY_PLAT_NOMATCH"}
        with patch("requests.get", return_value=_Response(payload)):
            result = _planner().plan(ORIGIN, DESTINATION)
        self.assertTrue(result.ok)
        self.assertFalse(result.verified)
        self.assertEqual(result.source, "demonstration_topology")
        self.assertIn("高德调用失败", result.note)
        self.assertIn("USERKEY_PLAT_NOMATCH", result.note)

    def test_network_failure_degrades_in_auto_mode(self):
        with patch("requests.get", side_effect=TimeoutError("连接超时")):
            result = _planner().plan(ORIGIN, DESTINATION)
        self.assertEqual(result.source, "demonstration_topology")
        self.assertIn("TimeoutError", result.note)

    def test_amap_mode_strict_does_not_hide_the_failure(self):
        with patch("requests.get", return_value=_Response({"status": "0", "info": "invalid key"})):
            result = _planner(mode="amap").plan(ORIGIN, DESTINATION)
        self.assertFalse(result.ok)
        self.assertEqual(result.source, "amap_v5")
        self.assertIn("invalid key", result.error)

    def test_http_error_is_captured_as_fallback_not_exception(self):
        with patch("requests.get", return_value=_Response({}, status_code=500)):
            result = _planner().plan(ORIGIN, DESTINATION)
        self.assertEqual(result.source, "demonstration_topology")
        self.assertFalse(result.verified)

    def test_missing_destination_skips_amap_entirely(self):
        with patch("requests.get", side_effect=AssertionError("不应调用高德")):
            result = _planner().plan(ORIGIN, None)
        self.assertEqual(result.source, "demonstration_topology")


if __name__ == "__main__":
    unittest.main()
