import unittest

import config


class RecognitionModeTests(unittest.TestCase):
    def test_all_mode_recognizes_non_violation_vehicle(self):
        self.assertTrue(config.should_recognize_vehicle(False, "all"))

    def test_violation_only_mode_skips_non_violation_vehicle(self):
        self.assertFalse(config.should_recognize_vehicle(False, "violation_only"))
        self.assertTrue(config.should_recognize_vehicle(True, "violation_only"))

    def test_invalid_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "all 或 violation_only"):
            config.normalize_recognition_mode("silent")


if __name__ == "__main__":
    unittest.main()
