import tempfile
import unittest
from pathlib import Path

from edge.compare_models import (
    Detection,
    aggregate_summaries,
    box_iou,
    collect_images,
    compare_models,
    match_detections,
    summarise_image,
)


class EdgeModelComparisonTests(unittest.TestCase):
    def test_box_iou_handles_identical_and_disjoint_boxes(self):
        self.assertEqual(box_iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)
        self.assertEqual(box_iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)

    def test_match_detections_is_class_aware_and_one_to_one(self):
        reference = [
            Detection((0, 0, 10, 10), 0.9, 2),
            Detection((20, 20, 30, 30), 0.8, 2),
        ]
        candidate = [
            Detection((0, 0, 10, 10), 0.88, 2),
            Detection((20, 20, 30, 30), 0.7, 3),
            Detection((1, 1, 9, 9), 0.6, 2),
        ]

        matches = match_detections(reference, candidate, iou_threshold=0.5)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["reference_index"], 0)
        self.assertEqual(matches[0]["candidate_index"], 0)
        self.assertAlmostEqual(matches[0]["confidence_delta"], 0.02)

    def test_aggregate_summaries_reports_detection_evidence(self):
        image = Path("sample.jpg")
        first = summarise_image(
            image,
            [Detection((0, 0, 10, 10), 0.9, 2)],
            [Detection((0, 0, 10, 10), 0.8, 2)],
            0.5,
        )
        second = summarise_image(Path("empty.jpg"), [], [], 0.5)

        summary = aggregate_summaries([first, second])

        self.assertEqual(summary["image_count"], 2)
        self.assertEqual(summary["images_with_reference_detections"], 1)
        self.assertEqual(summary["matched_count"], 1)
        self.assertEqual(summary["reference_match_rate"], 1.0)
        self.assertEqual(summary["mean_iou"], 1.0)

    def test_collect_images_recurses_and_rejects_empty_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            image = nested / "frame.JPG"
            image.write_bytes(b"image")
            (root / "note.txt").write_text("ignore", encoding="utf-8")

            self.assertEqual(collect_images([root]), [image.resolve()])

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "没有找到"):
                collect_images([directory])

    def test_compare_models_rejects_invalid_image_size_and_match_iou_before_loading_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.pt"
            candidate = root / "candidate.onnx"
            image = root / "frame.jpg"
            reference.write_bytes(b"reference")
            candidate.write_bytes(b"candidate")
            image.write_bytes(b"image")

            with self.assertRaisesRegex(ValueError, "image_size"):
                compare_models(reference, candidate, [image], image_size=0)
            with self.assertRaisesRegex(ValueError, "match_iou"):
                compare_models(reference, candidate, [image], match_iou=1.1)


if __name__ == "__main__":
    unittest.main()
