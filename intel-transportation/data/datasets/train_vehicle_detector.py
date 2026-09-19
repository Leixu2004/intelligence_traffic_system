# -*- coding: utf-8 -*-
"""
车辆检测器微调 + ONNX 导出（替换项目当前的官方 COCO 版 yolov8n.pt）
================================================================

项目现状（config.py）：
  YOLO_VEHICLE_MODEL_PATH = yolov8n.pt        # 官方 COCO 权重
  VEHICLE_CLASSES = [2, 3, 5, 7]              # COCO 的 car/motorcycle/bus/truck
用 UA-DETRAC / BDD100K 转换出的 dataset.yaml 微调后，类别空间会变成数据集自己的
类别 id，因此必须同步修改 VEHICLE_CLASSES（本脚本结束时会打印应填的值）。

训练：
  python train_vehicle_detector.py --data D:/datasets/DETRAC/yolo_detrac/dataset.yaml \
      --weights yolov8n.pt --epochs 80 --imgsz 960 --batch 16
导出 ONNX（默认开启，opset 18 FP32，与 docs/deployment/edge_inference.md 对齐）：
  训练完成后自动在 weights 目录生成 best.pt / best.onnx

接入：
  1) 设置环境变量指向新权重（无需改代码）：
       set YOLO_VEHICLE_MODEL_PATH=D:\\...\\runs\\vehicle_detrac\\weights\\best.pt
  2) 按脚本末尾打印的清单更新 config.VEHICLE_CLASSES（或新增环境变量映射）。
  3) 用 edge/benchmark.py、edge/compare_models.py 做 .pt/.onnx 一致性与性能校验。
"""

from __future__ import annotations

import argparse
from pathlib import Path


def read_class_names(data_yaml: Path) -> dict[int, str]:
    """极简解析 dataset.yaml 的 names 段（避免强依赖 PyYAML）。"""
    names: dict[int, str] = {}
    in_names = False
    for raw in Path(data_yaml).read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.strip().startswith("names:"):
            in_names = True
            tail = line.split("names:", 1)[1].strip()
            if tail.startswith("[") and tail.endswith("]"):  # 列表式 names
                for i, item in enumerate(tail.strip("[]").split(",")):
                    item = item.strip().strip("'\"")
                    if item:
                        names[i] = item
                return names
            continue
        if in_names:
            if not line.startswith(" "):
                break
            body = line.strip()
            if ":" in body:
                k, v = body.split(":", 1)
                if k.strip().isdigit():
                    names[int(k)] = v.strip().strip("'\"")
    if not names:
        # 退回 PyYAML 解析
        try:
            import yaml

            loaded = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8"))
            n = loaded.get("names", {})
            if isinstance(n, list):
                names = dict(enumerate(n))
            elif isinstance(n, dict):
                names = {int(k): v for k, v in n.items()}
        except Exception as exc:  # pragma: no cover
            raise SystemExit(f"无法解析 dataset.yaml 的 names: {exc}")
    return dict(sorted(names.items()))


def suggest_vehicle_class_ids(names: dict[int, str]) -> list[int]:
    """挑出属于机动车/两轮车的类别 id，供 config.VEHICLE_CLASSES 使用。"""
    keywords = ("car", "van", "bus", "truck", "motorcycle", "bicycle", "vehicle")
    ids = [i for i, n in names.items() if any(k in n.lower() for k in keywords)]
    return ids or list(names.keys())


def main() -> None:
    ap = argparse.ArgumentParser(description="YOLOv8 车辆检测器微调 + ONNX 导出")
    ap.add_argument("--data", required=True, help="转换脚本产出的 dataset.yaml")
    ap.add_argument("--weights", default="yolov8n.pt", help="起始权重：yolov8n.pt / yolov8s.pt")
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--imgsz", type=int, default=960, help="UA-DETRAC 原生 960x540，建议 960")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default="", help="留空自动；或 0 / cpu")
    ap.add_argument("--project", default="runs", help="输出根目录")
    ap.add_argument("--name", default="vehicle_detrac")
    ap.add_argument("--no-export", action="store_true", help="只训练，不导出 ONNX")
    ap.add_argument("--opset", type=int, default=18)
    args = ap.parse_args()

    from ultralytics import YOLO

    data_yaml = Path(args.data)
    if not data_yaml.exists():
        raise SystemExit(f"找不到 dataset.yaml: {data_yaml}")
    names = read_class_names(data_yaml)
    print("数据集类别：", names)

    model = YOLO(args.weights)
    train_kwargs = dict(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=args.project,
        name=args.name,
        exist_ok=True,
        verbose=True,
    )
    if args.device:
        train_kwargs["device"] = args.device
    model.train(**train_kwargs)

    best_pt = Path(args.project) / args.name / "weights" / "best.pt"
    print("=" * 60)
    print(f"训练完成，最佳权重：{best_pt.resolve()}")

    if not args.no_export:
        export_model = YOLO(str(best_pt))
        onnx_path = export_model.export(format="onnx", opset=args.opset, imgsz=args.imgsz)
        print(f"ONNX 已导出：{onnx_path}")

    ids = suggest_vehicle_class_ids(names)
    print("-" * 60)
    print("接入本项目：")
    print(f"  1) set YOLO_VEHICLE_MODEL_PATH={best_pt.resolve()}")
    print(f"  2) 将 config.VEHICLE_CLASSES 改为 {ids}")
    print(f"     （类别映射：{ {i: names[i] for i in ids} }）")
    print("  3) 重新导出/校验 ONNX：python edge/export_models.py 与 edge/compare_models.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
