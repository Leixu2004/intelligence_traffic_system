import unittest

from acceptance.simulate_pipeline_events import build_simulated_events


class AcceptanceSimulatorTests(unittest.TestCase):
    def test_builds_three_explicitly_simulated_event_types(self):
        events = build_simulated_events("RUN-1", "2026-09-16T08:00:00+00:00")
        self.assertEqual(len(events), 3)
        self.assertEqual(
            [payload["event_type"] for _, payload in events],
            ["traffic_observation", "plate_recognition", "traffic_violation"],
        )
        self.assertTrue(all(payload["simulation"] for _, payload in events))
        self.assertTrue(all("software" in payload["simulation_scope"] for _, payload in events))
        self.assertTrue(events[1][1]["event_id"])
        self.assertTrue(events[2][1]["event_id"])


if __name__ == "__main__":
    unittest.main()
