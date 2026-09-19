import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.crew.notice import DEFAULT_CHANNELS, PublicNoticeSink


class PublicNoticeSinkTests(unittest.TestCase):
    def _sink(self, root):
        return PublicNoticeSink(Path(root) / "nested" / "notices.jsonl")

    def test_publish_writes_unverified_local_record(self):
        with TemporaryDirectory() as tmp:
            sink = self._sink(tmp)
            record = sink.publish(event_id="EV-1", content="前方事故，请绕行", channels=["短信"])
            line = sink.path.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        self.assertTrue(record["ok"])
        self.assertEqual(parsed["event_id"], "EV-1")
        self.assertEqual(parsed["channels"], ["短信"])
        # 通告未接真实通道：必须同时带模拟与未送达标记，且不能声称已核验。
        self.assertTrue(parsed["simulation"])
        self.assertFalse(parsed["delivered"])
        self.assertEqual(parsed["channel_mode"], "local_file")
        self.assertIn("不构成触达证据", parsed["note"])

    def test_default_channels_apply_when_none_supplied(self):
        with TemporaryDirectory() as tmp:
            record = self._sink(tmp).publish(event_id="EV-2", content="正文")
        self.assertEqual(list(record["channels"]), list(DEFAULT_CHANNELS))

    def test_records_append_rather_than_overwrite(self):
        with TemporaryDirectory() as tmp:
            sink = self._sink(tmp)
            sink.publish(event_id="EV-A", content="一")
            sink.publish(event_id="EV-B", content="二")
            lines = sink.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_string_path_is_accepted(self):
        with TemporaryDirectory() as tmp:
            sink = PublicNoticeSink(str(Path(tmp) / "notices.jsonl"))
            self.assertTrue(sink.publish(event_id="EV-3", content="正文")["ok"])


if __name__ == "__main__":
    unittest.main()
