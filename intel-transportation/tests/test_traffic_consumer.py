import unittest

from backend.events import PlateRecognitionEvent, TrafficObservation, ViolationEvent
from backend.traffic_consumer import insert_event


class _Cursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, values):
        self.calls.append((sql, values))


class TrafficConsumerTests(unittest.TestCase):
    def test_routes_all_event_topics_to_separate_tables(self):
        cursor = _Cursor()
        traffic_type = insert_event(
            cursor,
            "traffic_stream",
            TrafficObservation(
                time="2026-09-11T04:00:00+00:00",
                vehicle_id="TRACK-1",
                checkpoint_id="CP-1",
                camera_id="CAM-1",
            ).to_payload(),
        )
        violation_type = insert_event(
            cursor,
            "traffic_violations",
            ViolationEvent(
                time="2026-09-11T04:01:00+00:00",
                plate="粤B12345",
                violation_type="压线违章",
                checkpoint_id="CP-1",
                camera_id="CAM-1",
                image_path="evidence.jpg",
            ).to_payload(),
        )
        plate_type = insert_event(
            cursor,
            "plate_recognitions",
            PlateRecognitionEvent(
                time="2026-09-11T04:00:30+00:00",
                plate="粤B12345",
                checkpoint_id="CP-1",
                camera_id="CAM-1",
                ocr_confidence=0.97,
            ).to_payload(),
        )

        self.assertEqual(traffic_type, "traffic_observation")
        self.assertEqual(violation_type, "traffic_violation")
        self.assertEqual(plate_type, "plate_recognition")
        self.assertIn("traffic_gps", cursor.calls[0][0])
        self.assertIn("traffic_violations", cursor.calls[1][0])
        self.assertIn("plate_recognitions", cursor.calls[2][0])


if __name__ == "__main__":
    unittest.main()
