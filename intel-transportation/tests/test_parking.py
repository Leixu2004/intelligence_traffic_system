import unittest
from unittest.mock import patch

from core.violation import IllegalParkingChecker


class IllegalParkingTests(unittest.TestCase):
    def test_vehicle_must_remain_in_zone_for_threshold(self):
        checker = IllegalParkingChecker([100, 100, 200, 200], threshold_seconds=2.0)
        outside = [50, 50, 80, 80]
        inside = [120, 120, 150, 150]

        with patch("core.violation.time.time", side_effect=[100.0, 101.0, 102.5]):
            self.assertEqual(checker.check(outside, 99), (False, 0.0))
            self.assertEqual(checker.check(inside, 99), (False, 0.0))
            self.assertEqual(checker.check(inside, 99), (False, 1.0))
            violation, duration = checker.check(inside, 99)

        self.assertTrue(violation)
        self.assertEqual(duration, 2.5)
        self.assertEqual(checker.check(outside, 99), (False, 0.0))
        self.assertNotIn(99, checker.vehicle_entry_times)


if __name__ == "__main__":
    unittest.main()
