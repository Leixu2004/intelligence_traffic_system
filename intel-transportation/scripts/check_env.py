#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键环境检查脚本 —— 迁移到新机器 / 调试运行时使用。

用法（在 intel-transportation 项目根目录）：
    python scripts/check_env.py

它会逐项检测：Python 版本、关键依赖、模型文件、OCR 缓存、
Kafka/Docker/GPU 可选能力，并在最后给出结论。

退出码：0 = 核心可运行；1 = 存在阻断项（缺少核心模型或依赖）。
"""
import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
os.chdir(BASE_DIR)

# ---- 基础约定 ----
PY_MIN = (3, 10)
PY_MAX_INCLUDE = (3, 13)      # 项目在 3.13 验证通过
REQUIRED_MODULES = [
    ("ultralytics", "车辆/车牌检测"),
    ("paddleocr", "车牌 OCR"),
    ("onnxruntime", "OCR 推理引擎"),
    ("cv2", "OpenCV 视频/图像"),
    ("kafka", "消息队列客户端(可缺省)"),
]
MODEL_FILES = [
    ("yolov8n.pt", "车辆检测模型"),
    ("models/exp-7.pt", "车牌检测模型"),
]
OCR_CACHE_DIRS = [
    ("data/paddlex_cache/official_models/PP-OCRv6_medium_det_onnx", "OCR 文本检测"),
    ("data/paddlex_cache/official_models/PP-OCRv6_medium_rec_onnx", "OCR 文本识别"),
]

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
results = []  # (level, message)


def check_python():
    v = sys.version_info
    major_minor = (v.major, v.minor)
    ok = major_minor >= PY_MIN and major_minor <= PY_MAX_INCLUDE
    results.append((
        PASS if ok else FAIL,
        f"Python {v.major}.{v.minor}.{v.micro} @ {sys.executable} "
        f"(要求 {'.'.join(map(str, PY_MIN))}~{'.'.join(map(str, PY_MAX_INCLUDE))})"
    ))


def check_modules():
    for mod, desc in REQUIRED_MODULES:
        try:
            __import__(mod)
            results.append((PASS, f"依赖 [{mod}]（{desc}）已安装"))
        except ImportError:
            # kafka 为可选，缺了只 WARN（会自动回落离线 JSONL）
            level = WARN if mod == "kafka" else FAIL
            results.append((level, f"依赖 [{mod}]（{desc}）缺失"))


def check_model_files():
    for rel, desc in MODEL_FILES:
        p = BASE_DIR / rel
        if p.is_file():
            size_mb = p.stat().st_size / 1024 / 1024
            results.append((PASS, f"模型 [{desc}] {rel}（{size_mb:.1f} MB）"))
        else:
            results.append((FAIL, f"模型 [{desc}] 缺失：{p}"))


def check_ocr_cache():
    for rel, desc in OCR_CACHE_DIRS:
        p = BASE_DIR / rel
        has_onnx = (p / "inference.onnx").is_file()
        if has_onnx:
            results.append((PASS, f"OCR 缓存 [{desc}] {rel}"))
        else:
            # 缺失不阻断：首次运行会自动下载（需网络）
            results.append((WARN, f"OCR 缓存 [{desc}] 暂缺（首次运行将自动下载）：{rel}"))


def check_inputs():
    # 默认 VIDEO_SOURCE 指向的 D 盘路径通常不存在，提醒显式传参
    default_video = os.getenv("VIDEO_SOURCE", r"D:\test_vedio\traffic.mp4")
    if not Path(default_video).is_file():
        results.append((WARN, f"默认视频 VIDEO_SOURCE 不存在（{default_video}）；请用命令行参数显式传入视频路径"))
    # 是否有可用的本地演示视频
    candidates = ["data/vision/traffic.mp4", "data/vision/traffic_demo_60s.mp4", "data/vision/demo/road_demo.mp4"]
    have = next((c for c in candidates if (BASE_DIR / c).is_file()), None)
    results.append((PASS if have else WARN, f"本机检测到可演示视频：{have or '未找到，请自行准备视频'}"))


def check_optional():
    # Kafka 端口
    sock = __import__("socket")
    kafka_ok = sock.socket(sock.AF_INET, sock.SOCK_STREAM).connect_ex(("127.0.0.1", 9092)) == 0
    results.append((PASS if kafka_ok else WARN, f"Kafka localhost:9092 {'可达' if kafka_ok else '不可达（将回落离线 JSONL，不影响识别）'}"))

    # Docker
    docker = shutil.which("docker")
    results.append((PASS if docker else WARN, f"Docker CLI {'已找到' if docker else '未找到（数据库/Kafka/Flink 相关功能依赖 Docker）'}:{docker or ''}"))

    # GPU (torch)
    try:
        import torch
        gpu = torch.cuda.is_available()
        if gpu:
            results.append((PASS, f"GPU 可用：{torch.cuda.get_device_name(0)}"))
        else:
            results.append((WARN, "未检测到可用 CUDA GPU，将使用 CPU（全量视频较慢）"))
    except Exception:
        results.append((WARN, "torch 不可用，无法检测 GPU"))


def main():
    print("=" * 64)
    print("智慧交通系统 - 环境检查")
    print(f"项目目录: {BASE_DIR}")
    print("=" * 64)

    check_python()
    check_modules()
    check_model_files()
    check_ocr_cache()
    check_inputs()
    check_optional()

    fails = [m for lvl, m in results if lvl == FAIL]
    warns = [m for lvl, m in results if lvl == WARN]
    passes = [m for lvl, m in results if lvl == PASS]

    print("\n--- 检查明细 ---")
    for lvl, msg in results:
        print(f"  [{lvl:4s}] {msg}")

    print("\n--- 汇总 ---")
    print(f"  通过 {len(passes)} | 警告 {len(warns)} | 阻断 {len(fails)}")

    if fails:
        print("\n[阻断] 存在必须修复的问题，核心功能无法运行：")
        for m in fails:
            print(f"   - {m}")
        print("\n  提示：模型权重不在 git 里，迁移时务必从原机拷贝 yolov8n.pt / models/。")
    else:
        print("\n[结论] 核心检测/识别链路所需环境已就绪，可直接运行：")
        print("  python main.py <视频路径> --headless --recognition-mode all")
        if warns:
            print("\n有少量警告（不影响核心识别，可忽略或按需处理）。")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())