import json
import unittest

from backend.agent.tools import TrafficToolbox, TrafficToolGateway


class TrafficToolTests(unittest.TestCase):
    def setUp(self):
        traffic = [
            {
                "time": "2026-09-16T08:01:00Z",
                "checkpoint_id": "CP-1",
                "vehicle_count": 12,
                "average_speed": 35.0,
            },
            {
                "time": "2026-09-16T08:00:00Z",
                "checkpoint_id": "CP-1",
                "vehicle_count": 8,
                "average_speed": 40.0,
            },
        ]
        detections = [
            {"checkpoint_id": "CP-1", "vehicle_type": "car"},
            {"checkpoint_id": "CP-1", "vehicle_type": "car"},
            {"checkpoint_id": "CP-1", "vehicle_type": "truck"},
        ]
        self.toolbox = TrafficToolbox(
            TrafficToolGateway(
                traffic_records=lambda checkpoint_id, limit: (
                    [
                        item
                        for item in traffic
                        if checkpoint_id is None
                        or item["checkpoint_id"] == checkpoint_id
                    ][:limit],
                    "TimescaleDB",
                    "",
                ),
                detection_records=lambda checkpoint_id, limit: (
                    [
                        item
                        for item in detections
                        if checkpoint_id is None
                        or item["checkpoint_id"] == checkpoint_id
                    ][:limit],
                    "TimescaleDB",
                    "",
                ),
                predict_checkpoint=lambda checkpoint_id, steps: {
                    "forecast": [20.0] * steps,
                    "model": "candidate-v1",
                    "deployment_stage": "research_candidate",
                },
            )
        )

    def test_checkpoint_flow_is_bounded_and_source_is_visible(self):
        payload = json.loads(self.toolbox.query_checkpoint_flow("CP-1", 1))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "TimescaleDB")
        self.assertEqual(len(payload["records"]), 1)

    def test_unknown_checkpoint_does_not_fabricate_data(self):
        payload = json.loads(self.toolbox.query_checkpoint_flow("UNKNOWN"))
        self.assertFalse(payload["ok"])
        self.assertIn("没有可用", payload["message"])

    def test_checkpoint_validation_rejects_unbounded_input(self):
        payload = json.loads(self.toolbox.query_checkpoint_flow("CP-1' OR 1=1"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "validation")

    def test_peak_vehicle_distribution_and_prediction(self):
        peak = json.loads(self.toolbox.query_peak_period("CP-1"))
        distribution = json.loads(
            self.toolbox.query_vehicle_type_distribution("CP-1")
        )
        forecast = json.loads(self.toolbox.predict_checkpoint_flow("CP-1", 2))
        self.assertEqual(peak["vehicle_count"], 12.0)
        self.assertEqual(distribution["distribution"][0]["vehicle_type"], "car")
        self.assertEqual(forecast["forecast"], [20.0, 20.0])
        self.assertEqual(forecast["deployment_stage"], "research_candidate")


if __name__ == "__main__":
    unittest.main()
