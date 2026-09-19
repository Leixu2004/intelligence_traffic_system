import unittest

from backend.events import PlateRecognitionEvent, TrafficObservation, ViolationEvent, parse_event_time


class EventContractTests(unittest.TestCase):
    def test_traffic_observation_round_trip(self):
        event = TrafficObservation(
            time="2026-09-11T04:00:00+00:00",
            vehicle_id="TRACK-7",
            checkpoint_id="CP-1",
            camera_id="CAM-1",
            bbox=[1, 2, 3, 4],
        )
        parsed = TrafficObservation.from_payload(event.to_payload())
        self.assertEqual(parsed.vehicle_id, "TRACK-7")
        self.assertEqual(parsed.bbox, [1, 2, 3, 4])

    def test_violation_legacy_fields_are_normalised(self):
        event = ViolationEvent.from_payload(
            {
                "timestamp": "20260911_120000",
                "plate": "粤B12345",
                "type": "压线违章",
                "checkpoint_id": "CP-1",
            }
        )
        self.assertEqual(event.violation_type, "压线违章")
        self.assertTrue(event.event_id)

    def test_plate_recognition_round_trip_keeps_model_confidences(self):
        event = PlateRecognitionEvent(
            time="2026-09-11T04:00:00+00:00",
            plate="粤B12345",
            checkpoint_id="CP-1",
            camera_id="CAM-1",
            ocr_confidence=0.96,
            detector_confidence=0.91,
            bbox=[10, 20, 30, 40],
        )
        payload = event.to_payload()
        parsed = PlateRecognitionEvent.from_payload(payload)
        self.assertTrue(parsed.event_id)
        self.assertEqual(parsed.plate, "粤B12345")
        self.assertEqual(parsed.ocr_confidence, 0.96)
        self.assertEqual(parsed.detector_confidence, 0.91)
        self.assertEqual(parsed.bbox, [10, 20, 30, 40])

    def test_naive_iso_time_is_treated_as_utc(self):
        parsed = parse_event_time("2026-09-11T12:00:00")
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)


if __name__ == "__main__":
    unittest.main()
