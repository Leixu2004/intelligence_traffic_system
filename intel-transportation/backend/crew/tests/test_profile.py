import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.crew.config import DEFAULT_CONFIG_PATH
from backend.crew.profile import load_profile


class CrewProfileTests(unittest.TestCase):
    def test_shipped_profile_supplies_three_roles_and_three_tasks(self):
        profile = load_profile(DEFAULT_CONFIG_PATH)
        self.assertEqual(sorted(profile.roles), ["analyst", "commander", "dispatcher"])
        for key in ("commander", "analyst", "dispatcher"):
            self.assertTrue(profile.roles[key]["role"])
            self.assertTrue(profile.roles[key]["goal"])
            self.assertTrue(profile.roles[key]["backstory"].strip())
        self.assertEqual(sorted(profile.task_outputs), ["analysis", "command", "dispatch"])

    def test_missing_file_falls_back_to_defaults_without_raising(self):
        with TemporaryDirectory() as tmp:
            profile = load_profile(Path(tmp) / "absent.yaml")
        self.assertEqual(sorted(profile.roles), ["analyst", "commander", "dispatcher"])
        self.assertEqual(sorted(profile.task_outputs), ["analysis", "command", "dispatch"])

    def test_broken_yaml_falls_back_to_defaults(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.yaml"
            path.write_text("roles: [this is: not: valid: yaml", encoding="utf-8")
            profile = load_profile(path)
        self.assertIn("commander", profile.roles)


if __name__ == "__main__":
    unittest.main()
