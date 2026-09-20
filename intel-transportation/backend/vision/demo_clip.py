"""合成演示片段：给抽帧/关键帧链路一份可复现、可分发的输入。

仓库里没有真实监控视频，也没有摄像头。这个生成器画一条三车道道路：
正常车流 → 中段两车在同车道停住并重叠（模拟事故） → 后车排队。
**画面是程序生成的，不是真实事故影像**，只用于验证抽帧、关键帧筛选与端到端流程，
不得作为事故检测准确率或真实场景能力的证据。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

WIDTH = 480
HEIGHT = 320
LANE_CENTERS = (110, 240, 370)
COLLISION_START = 18.0
COLLISION_DURATION = 12.0
COLLISION_LANE = 0
VIDEO_FOURCC = "mp4v"
PALETTE = (
    (198, 40, 40),
    (40, 96, 198),
    (236, 236, 236),
    (60, 160, 90),
    (240, 190, 60),
)


@dataclass
class Vehicle:
    lane: int
    y: float
    cruise: float
    color: tuple[int, int, int]
    size: tuple[int, int] = (34, 62)

    @property
    def box(self) -> tuple[int, int, int, int]:
        half_w, half_h = self.size[0] // 2, self.size[1] // 2
        center_x = LANE_CENTERS[self.lane]
        return (center_x - half_w, int(self.y) - half_h, center_x + half_w, int(self.y) + half_h)


def build_vehicles(seed: int = 20260918) -> list[Vehicle]:
    rng = random.Random(seed)
    vehicles: list[Vehicle] = []
    for lane in range(3):
        for row in range(3):
            vehicles.append(
                Vehicle(
                    lane=lane,
                    y=rng.uniform(-260.0, HEIGHT + 60.0) - row * 120.0,
                    cruise=rng.uniform(58.0, 92.0),
                    color=PALETTE[(lane + row) % len(PALETTE)],
                )
            )
    return vehicles


def _draw_scene(vehicles: list[Vehicle], moment: float) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), (58, 60, 64))  # 沥青
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 40, HEIGHT), fill=(84, 88, 92))  # 左路肩
    draw.rectangle((WIDTH - 40, 0, WIDTH, HEIGHT), fill=(84, 88, 92))
    for center in LANE_CENTERS[1:]:
        for offset in range(0, HEIGHT, 46):
            draw.rectangle((center - 3, offset, center + 3, offset + 26), fill=(226, 226, 226))
    for vehicle in vehicles:
        x1, y1, x2, y2 = vehicle.box
        draw.rounded_rectangle((x1, y1, x2, y2), radius=6, fill=vehicle.color, outline=(18, 18, 18))
        draw.rectangle((x1 + 5, y1 + 8, x2 - 5, y1 + 20), fill=(20, 24, 30))  # 挡风玻璃
    if COLLISION_START <= moment < COLLISION_START + COLLISION_DURATION:
        center = LANE_CENTERS[COLLISION_LANE]
        draw.polygon(
            [(center - 26, 150), (center, 118), (center + 26, 150)],
            fill=(232, 128, 24),  # 事故警示三角，时间轴上可辨识
        )
    return image


def generate_demo_video(
    path: str | Path,
    *,
    seconds: float = 40.0,
    fps: float = 10.0,
    seed: int = 20260918,
) -> Path:
    """写出演示 mp4 并返回路径；文件已存在则直接复用（幂等，便于反复演示）。"""
    target = Path(path)
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    vehicles = build_vehicles(seed)
    collision_pair = [vehicle for vehicle in vehicles if vehicle.lane == COLLISION_LANE][:2]
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*VIDEO_FOURCC), fps, (WIDTH, HEIGHT))
    if not writer.isOpened():
        raise RuntimeError(f"无法写入演示视频 {target}：OpenCV 未启用 MP4 编码器")
    try:
        for frame_index in range(int(round(seconds * fps))):
            moment = frame_index / fps
            in_collision = COLLISION_START <= moment < COLLISION_START + COLLISION_DURATION
            for vehicle in vehicles:
                if vehicle in collision_pair:
                    speed = 0.0 if in_collision else vehicle.cruise
                elif vehicle.lane == COLLISION_LANE:
                    speed = 12.0 if in_collision else vehicle.cruise  # 同车道排队
                else:
                    speed = vehicle.cruise
                vehicle.y += speed / fps
                if vehicle.y - vehicle.size[1] > HEIGHT:  # 出画后从上方回灌
                    vehicle.y = -vehicle.size[1] - rng.uniform(0.0, 160.0)
            for order, vehicle in enumerate(collision_pair):
                if moment >= COLLISION_START:
                    # 停住并保持重叠，构成「追尾」视觉证据
                    vehicle.y = min(vehicle.y, 150.0 + order * 46.0)
            frame = np.asarray(_draw_scene(vehicles, moment), dtype="uint8")
            writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    finally:
        writer.release()
    return target


def scene_ground_truth(seconds: float = 40.0) -> list[dict[str, object]]:
    """演示片段的脚本化事件表（生成器的已知设定，不是模型输出）。"""
    return [
        {"at_seconds": 0.0, "scripted_scene": "三车道正常通行，车辆匀速"},
        {
            "at_seconds": COLLISION_START,
            "scripted_scene": (
                f"第 {COLLISION_LANE + 1} 车道两车停住并重叠，持续 {COLLISION_DURATION:.0f} 秒，同车道后车排队"
            ),
        },
        {
            "at_seconds": min(COLLISION_START + COLLISION_DURATION, seconds),
            "scripted_scene": "停放车辆恢复行驶，车流回到正常速度",
        },
    ]
