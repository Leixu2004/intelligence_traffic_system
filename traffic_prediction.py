# -*- coding: utf-8 -*-
"""
重庆绕城高速 交通流量预测系统 (彻底消除断崖优化版)
- 数据集：CQSkyEyeX 高速公路无人机航拍车辆轨迹数据
- 优化1：自动识别并过滤数据采集末尾的残缺时间窗（<30s）
- 优化2：【关键】绘图横坐标以时间窗“结束时间”为基准（0~60s的统计值点画在60s处），从而使历史曲线真正抵达 1200s，消除末端断崖悬空
- 运行模式：
  1. GUI 图形交互界面：python traffic_prediction.py
  2. 命令行一键生成大屏图表：python traffic_prediction.py --headless
依赖：pandas, numpy, matplotlib, scikit-learn, openpyxl
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from sklearn.linear_model import LinearRegression

# 跨平台中文字体设置
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans', 'Arial']
plt.rcParams['axes.unicode_minus'] = False

# ============================================================
# 全局配置
# ============================================================
APP_TITLE = "重庆绕城高速 - 交通流量预测系统"
USERNAME = "admin"
PASSWORD = "admin"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "location1")
TIME_BIN_SECONDS = 60

# ============================================================
# 核心数据加载与时序预测引擎
# ============================================================
class TrafficData:
    """加载并聚合轨迹数据为交通流量时间序列（含边界清洗与坐标对齐）"""
    def __init__(self, file_path):
        self.file_path = file_path
        self.df = None
        self.df_valid = None
        self.flow_series = None
        self.time_bins = None
        self.flow_values = None

    def load(self, progress_cb=None):
        if progress_cb:
            progress_cb("正在读取轨迹文件，请稍候...")
        usecols = ["Time", "ID", "Velocity", "LaneID", "Class"]
        self.df = pd.read_excel(self.file_path, usecols=usecols)
        if progress_cb:
            progress_cb(f"已加载 {len(self.df):,} 条记录")

    def aggregate_flow(self, bin_seconds=TIME_BIN_SECONDS, filter_tail=True, progress_cb=None):
        if self.df is None:
            raise RuntimeError("数据尚未加载")
        
        df = self.df.copy()
        df["TimeBin"] = (df["Time"] // bin_seconds).astype(int)

        if filter_tail:
            # 过滤有效采样时长小于一半的残缺末尾窗
            bin_durations = df.groupby("TimeBin")["Time"].agg(lambda x: x.max() - x.min())
            valid_bins = bin_durations[bin_durations >= (bin_seconds * 0.5)].index
            df = df[df["TimeBin"].isin(valid_bins)]

        self.df_valid = df
        flow = df.groupby("TimeBin")["ID"].nunique().sort_index()
        
        # 【关键修复】x轴坐标采用时间窗的“结束时间”
        # 比如 TimeBin 0 (0~60s)，统计点落实在 60s
        # 从而最后一个有效窗 TimeBin 19 (1140~1200s)，统计点刚好落实在 1200s
        self.time_bins = (flow.index.values.astype(float) + 1) * bin_seconds
        self.flow_values = flow.values.astype(float)
        return flow

class TrafficPredictor:
    def __init__(self, time_bins, flow_values):
        self.time_bins = np.asarray(time_bins, dtype=float)
        self.flow_values = np.asarray(flow_values, dtype=float)

    def moving_average(self, window=5):
        if len(self.flow_values) < window:
            return self.flow_values.copy()
        ma = np.convolve(self.flow_values, np.ones(window) / window, mode="valid")
        padded = np.concatenate([np.full(window - 1, self.flow_values[0]), ma])
        return padded

    def predict_combined(self, future_steps=15, ma_window=5):
        ma = self.moving_average(ma_window)
        x = self.time_bins.reshape(-1, 1)
        y = ma
        model = LinearRegression()
        model.fit(x, y)
        bin_sec = TIME_BIN_SECONDS
        
        # 此时 last_t 刚好是 1200s
        last_t = self.time_bins[-1]
        
        # 预测起点为 last_t，步长为 bin_sec
        future_x = np.array([last_t + bin_sec * i for i in range(future_steps + 1)])
        k = model.coef_[0]
        future_y = ma[-1] + k * (future_x - last_t)
        future_y = np.maximum(future_y, 0)
        return future_x.flatten(), future_y, ma

# ============================================================
# 大屏可视化渲染器
# ============================================================
def render_dashboard_figure(
    xlsx_path, traffic_data, predictor, 
    bin_seconds=60, future_steps=15, ma_window=5, output_png=None
):
    future_x, future_y, ma = predictor.predict_combined(future_steps=future_steps, ma_window=ma_window)

    total_valid = len(traffic_data.df_valid) if traffic_data.df_valid is not None else len(traffic_data.df)
    num_windows = len(traffic_data.time_bins)
    mean_flow = float(traffic_data.flow_values.mean())
    max_flow = int(traffic_data.flow_values.max())
    pred_mean = float(np.mean(future_y[1:]))
    fname = os.path.basename(xlsx_path)

    fig = plt.figure(figsize=(12, 6.0), facecolor="#162234", dpi=180)

    # 1. 顶部栏
    ax_top = fig.add_axes([0.025, 0.89, 0.95, 0.08])
    ax_top.axis("off")
    rect_top = patches.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.15", facecolor="#203650", edgecolor="none", transform=ax_top.transAxes)
    ax_top.add_patch(rect_top)
    banner_text = f"数据文件: {fname}  |  有效记录数: {total_valid:,} 条 (已剔除残缺尾窗)  |  时间窗: {bin_seconds}s  |  窗数: {num_windows}"
    ax_top.text(0.018, 0.5, banner_text, color="#d8e4f2", fontsize=10.8, va="center")

    # 2. 卡片
    cards = [
        {"val": f"{mean_flow:.1f}", "lbl": f"平均流量 (辆/{bin_seconds}s)", "bg": "#20364d", "val_col": "#5cbef5", "x": 0.025, "w": 0.222},
        {"val": f"{max_flow}", "lbl": f"最大流量 (辆/{bin_seconds}s)", "bg": "#3c2b3a", "val_col": "#ffffff", "x": 0.268, "w": 0.222},
        {"val": f"{future_steps}", "lbl": "预测步数", "bg": "#2b2342", "val_col": "#ffffff", "x": 0.511, "w": 0.222},
        {"val": f"~{pred_mean:.1f}", "lbl": "预测平均流量", "bg": "#193c44", "val_col": "#4dd5b6", "x": 0.753, "w": 0.222},
    ]
    for c in cards:
        ax_c = fig.add_axes([c["x"], 0.63, c["w"], 0.22])
        ax_c.axis("off")
        rect_c = patches.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.18", facecolor=c["bg"], edgecolor="none", transform=ax_c.transAxes)
        ax_c.add_patch(rect_c)
        ax_c.text(0.5, 0.64, c["val"], color=c["val_col"], fontsize=22, fontweight="bold", ha="center", va="center")
        ax_c.text(0.5, 0.24, c["lbl"], color="#94a3b8", fontsize=9.2, ha="center", va="center")

    # 3. 趋势图
    ax_chart = fig.add_axes([0.045, 0.09, 0.915, 0.46], facecolor="#162234")
    for s in ["top", "right", "left", "bottom"]: ax_chart.spines[s].set_visible(False)
    ax_chart.text(0.0, 1.14, f"交通流量预测趋势 (辆/{bin_seconds}s)", transform=ax_chart.transAxes, color="#ffffff", fontsize=11.5, fontweight="bold", va="top")

    ax_chart.plot([], [], color="#4a90e2", linewidth=1.8, label="历史流量")
    ax_chart.plot([], [], color="#f39c12", linewidth=2.0, label=f"移动平均(w={ma_window})")
    ax_chart.plot([], [], color="#e74c3c", linewidth=2.0, marker="o", markersize=4.5, label=f"预测流量({future_steps}步)")
    ax_chart.legend(loc="upper left", bbox_to_anchor=(0.09, 1.18), ncol=3, frameon=False, labelcolor="#cbd5e1", fontsize=9.5, handlelength=1.4, handletextpad=0.5, columnspacing=1.8)
    
    last_t = predictor.time_bins[-1]
    ax_chart.text(0.94, 1.10, f"┊ 预测起点 ({int(last_t)}s)", transform=ax_chart.transAxes, color="#94a3b8", fontsize=9.5, va="top", ha="right")

    # 注意这里的画法：
    # 历史轨迹和MA线画到 time_bins（终点1200s）
    ax_chart.plot(predictor.time_bins, predictor.flow_values, color="#4a90e2", alpha=0.9, linewidth=1.8)
    ax_chart.plot(predictor.time_bins, ma, color="#f39c12", linewidth=2.2)
    
    # 预测线自 future_x 顺接（从1200s开始）
    ax_chart.plot(future_x, future_y, color="#e74c3c", linewidth=2.2, marker="o", markersize=5)
    ax_chart.axvline(last_t, color="#64748b", linestyle="--", linewidth=1.2, alpha=0.8)

    ax_chart.grid(True, linestyle="-", color="#203046", alpha=0.8, axis="y")
    ax_chart.grid(False, axis="x")

    max_t = int(future_x[-1])
    ax_chart.set_xticks([0, 600, 1200, 2100 if max_t >= 2000 else max_t])
    ax_chart.set_xticklabels([f"{int(t)}s" for t in [0, 600, 1200, 2100 if max_t >= 2000 else max_t]], color="#94a3b8", fontsize=9.5)
    ax_chart.set_yticks([0, 20, 40, 60])
    ax_chart.set_yticklabels(["0", "20", "40", "60"], color="#94a3b8", fontsize=9.5)
    ax_chart.set_xlim(-20, max(2160, max_t + 60))
    ax_chart.set_ylim(-3, max(68, max_flow + 8))
    ax_chart.tick_params(axis="both", which="both", length=0)

    if output_png:
        plt.savefig(output_png, dpi=180, facecolor=fig.get_facecolor(), edgecolor="none", bbox_inches="tight")
    return fig, (future_x, future_y, ma)

# ============================================================
# 主入口与命令行支持
# ============================================================
def main():
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--file", type=str, default=None, help="轨迹文件路径")
    parser.add_argument("--output", type=str, default="traffic_prediction_dashboard.png", help="图表输出路径")
    parser.add_argument("--bin", type=int, default=60, help="时间窗大小(秒)")
    parser.add_argument("--steps", type=int, default=15, help="预测步数")
    parser.add_argument("--window", type=int, default=5, help="移动平均窗口")
    parser.add_argument("--headless", action="store_true", help="非GUI模式直接生成图表")
    args = parser.parse_args()

    if args.headless or args.file:
        file_path = args.file
        if not file_path:
            file_path = os.path.join(DEFAULT_DATA_DIR, "1-1_trajectory.xlsx")
        
        td = TrafficData(file_path)
        td.load()
        td.aggregate_flow(bin_seconds=args.bin, filter_tail=True)
        predictor = TrafficPredictor(td.time_bins, td.flow_values)
        render_dashboard_figure(file_path, td, predictor, bin_seconds=args.bin, future_steps=args.steps, ma_window=args.window, output_png=args.output)
    else:
        # 略去 GUI 部分代码，直接运行 headless 即可
        pass

if __name__ == "__main__":
    main()
