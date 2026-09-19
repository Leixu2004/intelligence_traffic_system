import unittest

from core.validator import PlateValidator


class PlateValidatorTests(unittest.TestCase):
    def test_accepts_complete_plate(self):
        self.assertTrue(PlateValidator().is_valid("粤B12345"))

    def test_rejects_valid_prefix_with_trailing_noise(self):
        self.assertFalse(PlateValidator().is_valid("粤B12345XYZ"))


if __name__ == "__main__":
    unittest.main()
