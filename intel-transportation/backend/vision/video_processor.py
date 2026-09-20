"""视频抽帧与关键帧筛选（课件提交物 video_processor.py）。

- 抽帧：OpenCV 顺序解码，按 ``idx % round(fps * interval) == 0`` 取帧（与课件示例一致）；
- 关键帧：与上一保留帧的**变化像素占比**低于阈值即丢弃，用于砍掉课件所说「80% 的无效分析」；
- 兜底：OpenCV 不可用或没有视频文件时，可直接吃图片文件/目录，链路不中断。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
from PIL import Image

try:  # OpenCV 是可选重依赖，缺失时抽帧退化为图片目录模式
    import cv2
except ImportError:  # pragma: no cover - 取决于本机环境
    cv2 = None

VIDEO_SUFFIXES = (".mp4", ".avi", ".mkv", ".mov", ".flv")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
RTSP_PREFIX = "rtsp://"
FALLBACK_FPS = 25.0
DIFFERENCE_THUMBNAIL = (64, 64)
# 单像素灰度差超过该值才算「变了」，用来忽略编码噪声与轻微光照抖动
PIXEL_CHANGE_THRESHOLD = 24


class VideoDecodeUnavailable(RuntimeError):
    """没有 OpenCV 或视频文件打不开时抛出，调用方按降级路径处理。"""


@dataclass(frozen=True)
class Frame:
    index: int
    timestamp_seconds: float
    image: Image.Image = field(repr=False)
    source: str = "video"

    @property
    def time_label(self) -> str:
        """课件解说时间轴用的 MM:SS 标签。"""
        total = int(round(self.timestamp_seconds))
        return f"{total // 60:02d}:{total % 60:02d}"

    def resized(self, max_side: int) -> Image.Image:
        if max_side <= 0:
            return self.image
        side = max(self.image.size)
        if side <= max_side:
            return self.image
        scale = max_side / side
        size = (max(1, int(self.image.width * scale)), max(1, int(self.image.height * scale)))
        return self.image.resize(size, Image.Resampling.LANCZOS)

    def to_jpeg(self, *, quality: int = 85, max_side: int = 1024) -> bytes:
        buffer = BytesIO()
        self.resized(max_side).convert("RGB").save(buffer, format="JPEG", quality=quality)
        return buffer.getvalue()

    def to_data_url(self, *, quality: int = 85, max_side: int = 1024) -> str:
        payload = base64.b64encode(self.to_jpeg(quality=quality, max_side=max_side)).decode("ascii")
        return f"data:image/jpeg;base64,{payload}"


def opencv_available() -> bool:
    return cv2 is not None


def extract_frames(
    video_path: str | Path,
    interval: float = 2.0,
    *,
    max_frames: int | None = None,
) -> list[Frame]:
    """按固定时间间隔抽帧。返回的帧带原始帧号与秒级时间戳。"""
    if cv2 is None:
        raise VideoDecodeUnavailable("未安装 opencv-python：pip install opencv-python-headless")
    path = Path(video_path)
    text = str(video_path)
    if not text.lower().startswith(RTSP_PREFIX) and not path.is_file():
        raise VideoDecodeUnavailable(f"视频文件不存在: {path}")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise VideoDecodeUnavailable(f"无法打开视频（编码不支持或文件损坏）: {path}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not fps or fps != fps or fps <= 0:  # 部分容器读不到帧率，按 25fps 兜底
            fps = FALLBACK_FPS
        step = max(1, int(round(fps * max(interval, 1.0 / 60))))
        frames: list[Frame] = []
        index = 0
        while True:
            ok, raw = capture.read()
            if not ok:
                break
            if index % step == 0:
                rgb = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
                frames.append(
                    Frame(
                        index=index,
                        timestamp_seconds=round(index / fps, 3),
                        image=Image.fromarray(rgb),
                        source="video",
                    )
                )
                if max_frames and len(frames) >= max_frames:
                    break
            index += 1
    finally:
        capture.release()
    if not frames:
        raise VideoDecodeUnavailable(f"视频没有可解码的帧: {path}")
    return frames


def load_image(path: str | Path, *, index: int = 0, source: str = "image") -> Frame:
    image_path = Path(path)
    if not image_path.is_file():
        raise VideoDecodeUnavailable(f"图片文件不存在: {image_path}")
    with Image.open(image_path) as handle:
        return Frame(
            index=index,
            timestamp_seconds=0.0,
            image=handle.convert("RGB").copy(),
            source=source,
        )


def frames_from_directory(
    directory: str | Path,
    *,
    max_frames: int | None = None,
) -> list[Frame]:
    """把已抽好的图片序列当作帧源（无 OpenCV 或离线抽帧时使用）。"""
    root = Path(directory)
    candidates = sorted(
        path for path in (root.iterdir() if root.is_dir() else [root]) if path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not candidates:
        raise VideoDecodeUnavailable(f"目录里没有可用图片: {root}")
    if max_frames:
        candidates = candidates[:max_frames]
    return [load_image(path, index=position, source="image") for position, path in enumerate(candidates)]


def frame_difference(left: Frame, right: Frame) -> float:
    """两帧之间的变化强度，0（完全相同）~ 1（完全不同）。

    先缩到 64x64 灰度（与分辨率无关、代价可忽略），再取**变化像素占比**：
    监控画面大部分是静止背景，用平均绝对差会被背景稀释，导致车辆位移也算「没变化」。
    """
    reference = np.asarray(left.image.convert("L").resize(DIFFERENCE_THUMBNAIL), dtype="int16")
    current = np.asarray(right.image.convert("L").resize(DIFFERENCE_THUMBNAIL), dtype="int16")
    changed = np.abs(current - reference) >= PIXEL_CHANGE_THRESHOLD
    return float(changed.mean())


def select_keyframes(
    frames: Iterable[Frame],
    threshold: float,
    *,
    comparer: Callable[[Frame, Frame], float] = frame_difference,
) -> tuple[list[Frame], list[Frame]]:
    """场景变化检测。返回 (保留帧, 被跳过的帧)。

    threshold <= 0 表示关闭筛选（全帧保留）。第一帧无条件保留，作为后续比较基准。
    """
    kept: list[Frame] = []
    dropped: list[Frame] = []
    iterator = iter(frames)
    if threshold <= 0:
        return list(iterator), dropped
    previous: Frame | None = None
    for frame in iterator:
        if previous is None:
            kept.append(frame)
            previous = frame
            continue
        if comparer(previous, frame) >= threshold:
            kept.append(frame)
            previous = frame
        else:
            dropped.append(frame)
    return kept, dropped


def summarize_extraction(
    *,
    source: str,
    all_frames: list[Frame],
    keyframes: list[Frame],
    interval: float,
    keyframe_threshold: float,
) -> dict[str, object]:
    kept_ratio = round(len(keyframes) / len(all_frames), 3) if all_frames else 0.0
    return {
        "source": source,
        "frames_extracted": len(all_frames),
        "keyframes": len(keyframes),
        "skipped_frames": len(all_frames) - len(keyframes),
        "keyframe_keep_ratio": kept_ratio,
        "interval_seconds": interval,
        "keyframe_threshold": keyframe_threshold,
        "duration_seconds": all_frames[-1].timestamp_seconds if all_frames else 0.0,
    }
