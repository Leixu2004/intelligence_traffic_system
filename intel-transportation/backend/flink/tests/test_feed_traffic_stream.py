import time
import unittest
from itertools import islice

from backend.flink.feed_traffic_stream import (
    CAMERAS,
    CONGESTION_SPEED_RANGE,
    FREE_FLOW_SPEED_RANGE,
    event_stream,
)


class EventStreamTests(unittest.TestCase):
    def test_payload_matches_project_contract(self):
        first = next(iter(event_stream(seed=7)))
        self.assertEqual(first["event_type"], "traffic_observation")
        self.assertEqual(first["schema_version"], 1)
        for key in ("time", "vehicle_id", "checkpoint_id", "camera_id", "speed_kmh", "bbox"):
            self.assertIn(key, first)
        self.assertTrue(first["time"].endswith("+00:00"))

    def test_one_tick_covers_every_camera(self):
        rows = list(islice(event_stream(seed=7), len(CAMERAS)))
        self.assertEqual([row["camera_id"] for row in rows], [camera for camera, _ in CAMERAS])
        for row in rows:
            low, high = FREE_FLOW_SPEED_RANGE
            self.assertLessEqual(low, row["speed_kmh"])
            self.assertLessEqual(row["speed_kmh"], high)

    def test_congestion_window_slows_the_designated_camera(self):
        # start_at 追述 150 秒 → 已过 congestion_after=120，CAM-02 进入低速段，其余相机不受影响
        rows = list(islice(event_stream(seed=7, start_at=time.time() - 150), len(CAMERAS)))
        speeds = {row["camera_id"]: row["speed_kmh"] for row in rows}
        low, high = CONGESTION_SPEED_RANGE
        free_low, free_high = FREE_FLOW_SPEED_RANGE
        self.assertTrue(low <= speeds["CAM-02"] <= high)
        for camera in ("CAM-01", "CAM-03"):
            self.assertTrue(free_low <= speeds[camera] <= free_high)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
