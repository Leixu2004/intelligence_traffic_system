# -*- coding: utf-8 -*-
"""
CitySim 无人机轨迹 CSV -> 本项目 60 秒流量聚合 CSV
==================================================

CitySim（UCF-SST，无人机俯视）每个位置的 Trajectories/*.csv 中，每行是某辆车在某一
帧的轨迹点，30 FPS，关键字段：
  frameNum            帧号（30fps）
  carId               车辆 ID（整段视频内稳定）
  boundingBox1X..4Y   旋转框四个角点（像素 / 英尺 / 经纬度）
  speed               车速（英里/小时 MPH）
  heading/course/laneId 航向、车道号

本脚本把它聚合成与项目 LSTM 数据链完全一致的 60 秒流量表
（见 backend/prediction/data.py 的“聚合帧”读取契约）：
  series_id,bucket_start_seconds,bin_seconds,vehicle_count,
  entering_vehicle_count,source_file

口径（与项目既有无人机轨迹处理保持一致）：
  vehicle_count          该时间桶内出现过轨迹点的“唯一车辆数”（活跃/占用口径）
  entering_vehicle_count 首帧落在该桶的“新出现车辆数”（流入口径，更贴近检测线计数）

CitySim 为连续无人机视频，桶是完整视频时间窗，即使某桶计数为 0 也视为真实 0 并保留
（与收费站缺采集事件的“缺口”语义不同）。

完整数据需向 UCF-SST 申请（仓库 asset/MainPage/Data_Request_Form.pdf，
邮件 citysim.ucfsst@gmail.com），用于教学/研究，不可直接商用或再分发。

用法：
  python citysim_to_flow.py --input D:/datasets/CitySim --out data/lstm_sources/processed/citysim
  python citysim_to_flow.py --input D:/datasets/CitySim --out out_dir --bin-seconds 60 --with-speed
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

FPS = 30.0
MPH_TO_KMH = 1.609344
FLOW_COLUMNS = [
    "series_id",
    "bucket_start_seconds",
    "bin_seconds",
    "vehicle_count",
    "entering_vehicle_count",
    "source_file",
]


def derive_series_id(csv_path: Path) -> str:
    """.../Location/Trajectories/xxx.csv -> Location__xxx，否则用文件名。"""
    parts = csv_path.parts
    for i, part in enumerate(parts):
        if part.lower() == "trajectories" and i > 0:
            return f"{parts[i - 1]}__{csv_path.stem}"
    return csv_path.stem


def aggregate_file(csv_path: Path, bin_seconds: int, with_speed: bool):
    df = pd.read_csv(csv_path)
    required = {"frameNum", "carId"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{csv_path.name} 缺少必要列: {sorted(missing)}")

    df = df[["frameNum", "carId"] + (["speed"] if with_speed and "speed" in df.columns else [])].copy()
    df["frameNum"] = pd.to_numeric(df["frameNum"], errors="coerce").astype("Int64")
    df["carId"] = df["carId"].astype(str)
    df = df.dropna(subset=["frameNum"])
    df["bucket"] = (df["frameNum"].to_numpy() / (FPS * bin_seconds)).astype(int)

    series_id = derive_series_id(csv_path)
    last_bucket = int(df["bucket"].max()) if len(df) else -1

    active = df.groupby("bucket")["carId"].nunique()
    first_bucket = df.groupby("carId")["bucket"].min()
    entering = first_bucket.value_counts().sort_index()

    rows = []
    speed_rows = []
    for b in range(last_bucket + 1):
        rows.append(
            {
                "series_id": series_id,
                "bucket_start_seconds": b * bin_seconds,
                "bin_seconds": bin_seconds,
                "vehicle_count": int(active.get(b, 0)),
                "entering_vehicle_count": int(entering.get(b, 0)),
                "source_file": csv_path.name,
            }
        )
        if with_speed and "speed" in df.columns:
            sub = df.loc[df["bucket"] == b, "speed"]
            sub = pd.to_numeric(sub, errors="coerce").dropna()
            speed_rows.append(
                {
                    "series_id": series_id,
                    "bucket_start_seconds": b * bin_seconds,
                    "bin_seconds": bin_seconds,
                    "speed_mean_kmh": round(float(sub.mean() * MPH_TO_KMH), 3) if len(sub) else np.nan,
                    "speed_max_kmh": round(float(sub.max() * MPH_TO_KMH), 3) if len(sub) else np.nan,
                    "source_file": csv_path.name,
                }
            )
    return pd.DataFrame(rows), (pd.DataFrame(speed_rows) if speed_rows else None)


def main() -> None:
    ap = argparse.ArgumentParser(description="CitySim 轨迹 -> 项目 60 秒流量 CSV")
    ap.add_argument("--input", required=True, help="CitySim 数据根目录（递归查找 Trajectories/*.csv）或单个 csv")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--bin-seconds", type=int, default=60, help="聚合粒度秒数，默认 60")
    ap.add_argument("--with-speed", action="store_true", help="额外输出每桶平均/最高车速(km/h)")
    args = ap.parse_args()

    in_path = Path(args.input)
    csv_files = [in_path] if in_path.is_file() else sorted(in_path.rglob("*.csv"))
    # 排除可能的车道图/说明类 csv：要求表头含 frameNum 与 carId
    valid = []
    for f in csv_files:
        try:
            header = pd.read_csv(f, nrows=0)
            if {"frameNum", "carId"} <= set(header.columns):
                valid.append(f)
        except Exception:
            continue
    if not valid:
        raise SystemExit(f"在 {in_path} 下没有找到含 frameNum/carId 的 CitySim 轨迹 CSV")

    out = Path(args.out)
    per_series_dir = out / "per_series"
    per_series_dir.mkdir(parents=True, exist_ok=True)

    flow_frames, speed_frames = [], []
    for f in valid:
        flow, speed = aggregate_file(f, args.bin_seconds, args.with_speed)
        flow_frames.append(flow)
        if speed is not None:
            speed_frames.append(speed)
        sid = flow["series_id"].iloc[0]
        flow.to_csv(per_series_dir / f"{sid}_flow_{args.bin_seconds}s.csv", index=False, encoding="utf-8-sig")
        print(f"  {f.name} -> {sid}: {len(flow)} 桶, 流入合计 {int(flow['entering_vehicle_count'].sum())}")

    combined = pd.concat(flow_frames, ignore_index=True)
    combined_path = out / f"citysim_flow_{args.bin_seconds}s.csv"
    combined[FLOW_COLUMNS].to_csv(combined_path, index=False, encoding="utf-8-sig")
    print("=" * 60)
    print(f"文件数 {len(valid)}，序列数 {combined['series_id'].nunique()}，"
          f"时间桶 {len(combined)} -> {combined_path}")

    if speed_frames:
        speed_all = pd.concat(speed_frames, ignore_index=True)
        speed_path = out / f"citysim_speed_{args.bin_seconds}s.csv"
        speed_all.to_csv(speed_path, index=False, encoding="utf-8-sig")
        print(f"车速统计 -> {speed_path}")
    print("接入 LSTM：把该目录并入 backend/prediction 的聚合数据源（vehicle_count 或")
    print("entering_vehicle_count 均可作为目标列），与既有 *_flow_60s.csv 同构。")
    print("=" * 60)


if __name__ == "__main__":
    main()
