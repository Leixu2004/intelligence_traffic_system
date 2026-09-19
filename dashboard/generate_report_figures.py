"""Generate report figures for the 2026-09-10 dashboard integration record."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np
from PIL import Image

from dashboard.data_loader import (
    _synthetic_traffic,
    build_prediction_frame,
    calculate_metrics,
    checkpoint_points,
    load_traffic_data,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "assets" / "dashboard"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans", "Arial"]
plt.rcParams["axes.unicode_minus"] = False


def _style_axis(ax, face="#0f1d30"):
    ax.set_facecolor(face)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors="#9fb4cc", length=0)
    ax.grid(axis="y", color="#223753", alpha=0.75)


def render_dashboard_result() -> Path:
    bundle = load_traffic_data("local")
    # The repository CSV is intentionally tiny. Use the dashboard's own
    # deterministic fallback set for a readable full-screen report figure.
    traffic = _synthetic_traffic()
    metrics = calculate_metrics(traffic, bundle.detections)
    trend = build_prediction_frame(traffic)
    points = checkpoint_points(traffic)

    fig = plt.figure(figsize=(13.33, 7.5), dpi=150, facecolor="#0b1320")
    grid = fig.add_gridspec(12, 12, left=0.035, right=0.965, top=0.91, bottom=0.08, hspace=1.05, wspace=0.9)

    fig.text(0.035, 0.955, "智慧交通流量监测大屏", color="#f2f8ff", fontsize=20, fontweight="bold")
    fig.text(0.035, 0.925, "Streamlit + FastAPI + TimescaleDB / 本地降级 + WebGIS", color="#91a6bd", fontsize=9.5)
    fig.text(0.965, 0.95, "数据状态：内置示例数据（冷启动验收）", color="#b6c9dc", fontsize=9, ha="right")

    cards = [
        ("监测车辆", f"{metrics['vehicle_count']:,}", "#58a6ff"),
        ("平均速度", f"{metrics['average_speed']:.1f} km/h", "#4dd5b6"),
        ("高流量卡口", metrics["hot_checkpoint"], "#f59e0b"),
        ("异常 / 告警", f"{metrics['alerts']:,}", "#ef6a6a"),
    ]
    for index, (label, value, color) in enumerate(cards):
        ax = fig.add_subplot(grid[0:2, index * 3 : index * 3 + 3])
        ax.axis("off")
        box = FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.02,rounding_size=0.03", transform=ax.transAxes, facecolor="#13243a", edgecolor="#26476e", linewidth=1.0)
        ax.add_patch(box)
        ax.text(0.08, 0.70, label, color="#9fb4cc", fontsize=9, transform=ax.transAxes)
        ax.text(0.08, 0.30, value, color=color, fontsize=18, fontweight="bold", transform=ax.transAxes)

    trend_ax = fig.add_subplot(grid[2:8, 0:7])
    _style_axis(trend_ax)
    if not trend.empty:
        history = trend[trend["kind"].eq("历史")]
        future = trend[trend["kind"].eq("预测")]
        trend_ax.plot(history["time"], history["vehicle_count"], color="#58a6ff", marker="o", linewidth=2, label="历史流量")
        if not future.empty:
            trend_ax.plot(future["time"], future["vehicle_count"], color="#f59e0b", marker="o", linestyle="--", linewidth=2, label="短时预测")
    trend_ax.set_title("车流趋势与短时预测", loc="left", color="#e5eef8", fontsize=11, pad=12)
    trend_ax.set_ylabel("车辆数", color="#9fb4cc", fontsize=8)
    trend_ax.legend(frameon=False, labelcolor="#cbd5e1", fontsize=8, loc="upper left")
    trend_ax.tick_params(axis="x", labelrotation=25, labelsize=7)

    mix_ax = fig.add_subplot(grid[2:8, 7:10])
    mix_ax.set_facecolor("#0f1d30")
    if not bundle.detections.empty:
        counts = bundle.detections["vehicle_type"].value_counts()
        labels = ["小客车" if x == "car" else "货车" if x == "truck" else "客车" if x == "bus" else str(x) for x in counts.index]
        mix_ax.pie(counts.values, labels=labels, autopct="%1.0f%%", textprops={"color": "#cbd5e1", "fontsize": 8}, colors=["#58a6ff", "#f59e0b", "#4dd5b6", "#ef6a6a"], wedgeprops={"width": 0.42, "edgecolor": "#0f1d30"})
    mix_ax.set_title("车辆与告警概览", loc="left", color="#e5eef8", fontsize=11, pad=12)

    map_ax = fig.add_subplot(grid[2:8, 10:12])
    map_ax.set_facecolor("#111f31")
    map_ax.scatter(points["gps_lng"], points["gps_lat"], s=np.maximum(points["vehicle_count"].to_numpy() * 13, 45), c=points["average_speed"], cmap="RdYlGn", alpha=0.9, edgecolors="#dbeafe", linewidths=0.6)
    for _, point in points.iterrows():
        map_ax.text(point["gps_lng"], point["gps_lat"], str(point["checkpoint_id"]).replace("CP-", ""), color="#dbeafe", fontsize=6.5, ha="center", va="bottom")
    map_ax.set_title("WebGIS 卡口态势", loc="left", color="#e5eef8", fontsize=11, pad=12)
    map_ax.set_xticks([])
    map_ax.set_yticks([])
    for spine in map_ax.spines.values():
        spine.set_visible(True)
        spine.set_color("#26476e")

    flow_ax = fig.add_subplot(grid[9:12, 0:12])
    flow_ax.set_facecolor("#0b1320")
    flow_ax.axis("off")
    steps = [
        ("FastAPI\nTimescaleDB", "#1d4ed8"),
        ("CSV / Parquet\n离线数据", "#0f766e"),
        ("data_loader\n60 秒缓存", "#7c3aed"),
        ("Streamlit\nKPI + 图表", "#b45309"),
        ("WebGIS\n高德 / 本地地图", "#be123c"),
    ]
    start_x = 0.01
    width = 0.17
    for index, (label, color) in enumerate(steps):
        x = start_x + index * 0.205
        box = FancyBboxPatch((x, 0.26), width, 0.44, boxstyle="round,pad=0.01,rounding_size=0.02", transform=flow_ax.transAxes, facecolor=color, edgecolor="#94a3b8", linewidth=0.6, alpha=0.95)
        flow_ax.add_patch(box)
        flow_ax.text(x + width / 2, 0.48, label, transform=flow_ax.transAxes, ha="center", va="center", color="white", fontsize=8.5, fontweight="bold")
        if index < len(steps) - 1:
            flow_ax.add_patch(FancyArrowPatch((x + width + 0.008, 0.48), (x + 0.198, 0.48), transform=flow_ax.transAxes, arrowstyle="->", mutation_scale=12, linewidth=1.2, color="#94a3b8"))
    flow_ax.text(0.01, 0.88, "本次任务的展示层数据链路", transform=flow_ax.transAxes, color="#e5eef8", fontsize=10, fontweight="bold")

    path = OUTPUT_DIR / "20260910_streamlit_dashboard_result.png"
    fig.savefig(path, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    image = Image.open(path)
    width, height = image.size
    image.crop((0, 0, width, int(height * 0.78))).save(OUTPUT_DIR / "20260910_streamlit_dashboard_runtime.png")
    image.crop((0, int(height * 0.76), width, height)).save(OUTPUT_DIR / "20260910_streamlit_dashboard_dataflow.png")
    return path


def main() -> None:
    print(render_dashboard_result())


if __name__ == "__main__":
    main()
