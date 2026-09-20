"""抽帧、关键帧筛选与帧编码测试（验收 #4：按间隔抽取关键帧）。"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from backend.vision.demo_clip import generate_demo_video
from backend.vision.video_processor import (
    Frame,
    VideoDecodeUnavailable,
    extract_frames,
    frame_difference,
    frames_from_directory,
    load_image,
    opencv_available,
    select_keyframes,
    summarize_extraction,
)

try:
    import cv2  # noqa: F401
except ImportError:  # pragma: no cover
    cv2 = None


def solid_frame(color: tuple[int, int, int], index: int = 0, timestamp: float = 0.0) -> Frame:
    return Frame(index=index, timestamp_seconds=timestamp, image=Image.new("RGB", (80, 50), color))


def noisy_frame(index: int = 0, timestamp: float = 0.0) -> Frame:
    rng = np.random.default_rng(index + 1)
    array = rng.integers(0, 256, size=(50, 80, 3), dtype="uint8")
    return Frame(index=index, timestamp_seconds=timestamp, image=Image.fromarray(array))


class FrameTest(unittest.TestCase):
    def test_time_label_is_mm_ss(self) -> None:
        self.assertEqual("00:00", solid_frame((0, 0, 0)).time_label)
        self.assertEqual("00:32", solid_frame((0, 0, 0), timestamp=32.0).time_label)
        self.assertEqual("01:05", solid_frame((0, 0, 0), timestamp=65.4).time_label)

    def test_jpeg_roundtrip_and_data_url(self) -> None:
        frame = solid_frame((10, 120, 200))
        decoded = Image.open(io.BytesIO(frame.to_jpeg(quality=60)))
        self.assertEqual("JPEG", decoded.format)
        self.assertTrue(frame.to_data_url().startswith("data:image/jpeg;base64,"))

    def test_resize_caps_long_side_only(self) -> None:
        self.assertEqual((64, 40), solid_frame((0, 0, 0)).resized(64).size)
        self.assertEqual((80, 50), solid_frame((0, 0, 0)).resized(600).size)


class KeyframeSelectionTest(unittest.TestCase):
    def test_identical_frames_are_dropped(self) -> None:
        frames = [solid_frame((20, 20, 20), index=i, timestamp=float(i * 2)) for i in range(6)]
        kept, dropped = select_keyframes(frames, 0.02)
        self.assertEqual(1, len(kept))
        self.assertEqual(5, len(dropped))
        self.assertEqual(0, kept[0].index)

    def test_changing_scene_is_kept(self) -> None:
        frames = [solid_frame((0, 0, 0), index=0), noisy_frame(index=1, timestamp=2.0)]
        kept, dropped = select_keyframes(frames, 0.02)
        self.assertEqual(2, len(kept))
        self.assertEqual([], dropped)

    def test_threshold_zero_disables_filtering(self) -> None:
        frames = [solid_frame((0, 0, 0)) for _ in range(4)]
        kept, dropped = select_keyframes(frames, 0.0)
        self.assertEqual(4, len(kept))
        self.assertEqual([], dropped)

    def test_difference_is_normalised_and_symmetric(self) -> None:
        self.assertEqual(0.0, frame_difference(solid_frame((5, 5, 5)), solid_frame((5, 5, 5))))
        left, right = solid_frame((0, 0, 0)), solid_frame((255, 255, 255))
        self.assertAlmostEqual(frame_difference(left, right), frame_difference(right, left))
        self.assertGreater(frame_difference(left, right), 0.9)

    def test_custom_comparer_is_used(self) -> None:
        calls: list[tuple[int, int]] = []

        def comparer(left: Frame, right: Frame) -> float:
            calls.append((left.index, right.index))
            return 1.0

        kept, _ = select_keyframes([solid_frame((0, 0, 0), index=i) for i in range(3)], 0.5, comparer=comparer)
        self.assertEqual(3, len(kept))
        self.assertEqual([(0, 1), (1, 2)], calls)


class SummarizeTest(unittest.TestCase):
    def test_counts_and_ratio(self) -> None:
        frames = [solid_frame((0, 0, 0), index=i, timestamp=float(i)) for i in range(10)]
        kept, _ = select_keyframes(frames, 0.02)
        summary = summarize_extraction(
            source="clip.mp4", all_frames=frames, keyframes=kept, interval=1.0, keyframe_threshold=0.02
        )
        self.assertEqual(10, summary["frames_extracted"])
        self.assertEqual(9, summary["skipped_frames"])
        self.assertEqual(0.1, summary["keyframe_keep_ratio"])
        self.assertEqual(9.0, summary["duration_seconds"])

    def test_empty_frames_do_not_divide_by_zero(self) -> None:
        summary = summarize_extraction(
            source="clip.mp4", all_frames=[], keyframes=[], interval=2.0, keyframe_threshold=0.02
        )
        self.assertEqual(0.0, summary["keyframe_keep_ratio"])
        self.assertEqual(0.0, summary["duration_seconds"])


class SourceLoadingTest(unittest.TestCase):
    def test_missing_video_is_reported(self) -> None:
        with self.assertRaises(VideoDecodeUnavailable):
            extract_frames("data/vision/does-not-exist.mp4")

    def test_image_directory_becomes_frame_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for position in range(3):
                solid_frame((position * 40, 0, 0)).image.save(directory / f"frame_{position}.jpg")
            (directory / "notes.txt").write_text("忽略", encoding="utf-8")
            frames = frames_from_directory(directory)
            self.assertEqual(3, len(frames))
            self.assertEqual(["image"] * 3, [f.source for f in frames])
            self.assertEqual([0, 1, 2], [f.index for f in frames])

    def test_empty_directory_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(VideoDecodeUnavailable):
                frames_from_directory(tmp)

    def test_load_image_rejects_missing_file(self) -> None:
        with self.assertRaises(VideoDecodeUnavailable):
            load_image("data/vision/nope.jpg")


@unittest.skipUnless(cv2 is not None, "需要 OpenCV 才能验证抽帧")
class VideoExtractionTest(unittest.TestCase):
    """端到端：合成一段「行驶→追尾→拥堵」短片，按 2 秒间隔抽帧。"""

    def test_interval_extraction_and_unique_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "road.mp4"
            generate_demo_video(clip, seconds=20, fps=10)
            frames = extract_frames(clip, interval=2.0)
            self.assertEqual(10, len(frames))  # 20s / 2s
            self.assertEqual([0, 2, 4, 6, 8, 10, 12, 14, 16, 18], [f.timestamp_seconds for f in frames])
            self.assertEqual([0, 20, 40, 60], [f.index for f in frames[:4]])
            fingerprints = {np.asarray(f.image.convert("L")).tobytes() for f in frames}
            self.assertEqual(len(frames), len(fingerprints))  # 无 OpenCV 时会是「全是黑帧」

    def test_max_frames_caps_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "road.mp4"
            generate_demo_video(clip, seconds=20, fps=10)
            self.assertEqual(3, len(extract_frames(clip, interval=2.0, max_frames=3)))

    def test_keyframes_keep_collision_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "road.mp4"
            generate_demo_video(clip, seconds=40, fps=10)
            frames = extract_frames(clip, interval=2.0)
            kept, dropped = select_keyframes(frames, 0.02)
            self.assertGreater(len(kept), 0)
            self.assertEqual(len(frames), len(kept) + len(dropped))
            self.assertLessEqual(10, len(kept))  # 碰撞段画面剧变，不该被当成静止背景滤掉

    def test_opencv_available_matches_import(self) -> None:
        self.assertEqual(cv2 is not None, opencv_available())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
