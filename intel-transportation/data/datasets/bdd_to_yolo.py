# -*- coding: utf-8 -*-
"""
BDD100K 检测标注 JSON -> YOLO 数据集转换脚本
===========================================

适用官方文件：
  图片：bdd100k/images/100k/{train,val,test}/*.jpg        （分辨率多为 1280x720）
  标注：bdd100k/labels/bdd100k_labels_images_train.json
        bdd100k/labels/bdd100k_labels_images_val.json
  每条记录形如：
    {"name": "xxxx.jpg",
     "attributes": {"weather": "rainy", "timeofday": "night", "scene": "highway"},
     "labels": [{"category": "car",
                 "box2d": {"x1":..,"y1":..,"x2":..,"y2":..},
                 "attributes": {"occluded": false, "truncated": false}}, ...]}

价值：BDD100K 是车载前视、但天气/光照极丰富。本项目可用它：
  * --timeofday night --weather rainy 抽“夜间/雨天”子集做难例增强；
  * 直接用 traffic light / traffic sign 类别扩展红绿灯、标志检测。
注意：它与本项目“路侧固定机位”存在视角域差，建议作为增强/泛化数据，按比例混入，
不要替代 UA-DETRAC 作为主力域数据。

许可：教育/研究/非营利免费；商业用途需加入 BDD/BAIR Commons。

用法：
  python bdd_to_yolo.py \
      --labels D:/datasets/bdd100k/labels/bdd100k_labels_images_train.json \
      --images-root D:/datasets/bdd100k/images/100k \
      --out D:/datasets/bdd100k/yolo_vehicle --vehicle-only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 720

# 官方检测任务 10 类（顺序即 class id）
OFFICIAL_CLASSES = [
    "pedestrian", "rider", "car", "truck", "bus", "train",
    "motorcycle", "bicycle", "traffic light", "traffic sign",
]
# 面向本项目的机动车/两轮车子集
VEHICLE_CLASSES = ["car", "bus", "truck", "motorcycle", "bicycle"]


def link_or_copy(src: Path, dst: Path, mode: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or mode == "none":
        return
    try:
        if mode == "symlink":
            os.symlink(src, dst)
        elif mode == "copy":
            shutil.copy2(src, dst)
        else:
            os.link(src, dst)
    except OSError:
        try:
            shutil.copy2(src, dst)
        except OSError:
            pass


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def guess_split(json_path: Path) -> str:
    name = json_path.stem.lower()
    for token in ("train", "val", "test"):
        if token in name:
            return token
    return "train"


def find_image(images_root: Path, split: str, name: str) -> Path | None:
    candidates = [
        images_root / split / name,
        images_root / "100k" / split / name,
        images_root / name,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="BDD100K JSON -> YOLO 数据集转换")
    ap.add_argument("--labels", required=True, help="bdd100k_labels_images_*.json 路径，可多次传", action="append")
    ap.add_argument("--images-root", required=True, help="图片根目录（含 train/val 或 100k/train）")
    ap.add_argument("--out", required=True, help="输出 YOLO 数据集目录")
    ap.add_argument("--vehicle-only", action="store_true", help="只保留机动车/两轮车类别")
    ap.add_argument("--timeofday", default="", help="过滤光照：night/daytime/dawn/dusk，留空不过滤")
    ap.add_argument("--weather", default="", help="过滤天气：rainy/snow/foggy/clear/overcast 等，留空不过滤")
    ap.add_argument("--link", choices=["hardlink", "symlink", "copy", "none"], default="hardlink")
    args = ap.parse_args()

    class_names = VEHICLE_CLASSES if args.vehicle_only else OFFICIAL_CLASSES
    images_root = Path(args.images_root)
    out = Path(args.out)

    stats = {"records": 0, "kept": 0, "boxes": 0, "missing_image": 0}
    split_images: dict[str, list[str]] = {}

    for labels_path in map(Path, args.labels):
        records = json.loads(labels_path.read_text(encoding="utf-8"))
        split = guess_split(labels_path)
        for rec in records:
            stats["records"] += 1
            attrs = rec.get("attributes", {}) or {}
            if args.timeofday and attrs.get("timeofday", "").lower() != args.timeofday.lower():
                continue
            if args.weather and attrs.get("weather", "").lower() != args.weather.lower():
                continue

            name = rec.get("name", "")
            width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
            src = find_image(images_root, split, name)
            dst = out / "images" / split / name
            if src is not None:
                link_or_copy(src, dst, args.link)
                try:
                    from PIL import Image

                    with Image.open(src) as im:
                        width, height = im.size
                except Exception:
                    pass
                split_images.setdefault(split, []).append(str(dst.resolve()))
            else:
                stats["missing_image"] += 1

            lines = []
            for lab in rec.get("labels", []) or []:
                cat = (lab.get("category") or "").strip().lower()
                if cat not in class_names:
                    continue
                box = lab.get("box2d")
                if not box:
                    continue
                try:
                    x1, y1, x2, y2 = box["x1"], box["y1"], box["x2"], box["y2"]
                except (KeyError, TypeError):
                    continue
                xc = clamp(((x1 + x2) / 2.0) / width)
                yc = clamp(((y1 + y2) / 2.0) / height)
                w = clamp(abs(x2 - x1) / width)
                h = clamp(abs(y2 - y1) / height)
                if w <= 0 or h <= 0:
                    continue
                lines.append(f"{class_names.index(cat)} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}")

            if lines:
                stem = Path(name).stem
                (out / "labels" / split).mkdir(parents=True, exist_ok=True)
                (out / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
                stats["kept"] += 1
                stats["boxes"] += len(lines)

    # 生成清单与 dataset.yaml（train/val 至少给出存在的划分）
    for split, items in split_images.items():
        (out / f"{split}.txt").write_text("\n".join(items) + ("\n" if items else ""), encoding="utf-8")
    train_ref = "train.txt" if (out / "train.txt").exists() else f"{next(iter(split_images), 'train')}.txt"
    val_ref = "val.txt" if (out / "val.txt").exists() else train_ref
    yaml_text = (
        f"# 由 bdd_to_yolo.py 自动生成（vehicle_only={args.vehicle_only}, "
        f"timeofday='{args.timeofday}', weather='{args.weather}'）\n"
        f"path: {out.resolve().as_posix()}\n"
        f"train: {train_ref}\nval: {val_ref}\nnames:\n"
        + "".join(f"  {i}: {n}\n" for i, n in enumerate(class_names))
    )
    (out / "dataset.yaml").write_text(yaml_text, encoding="utf-8")

    print("=" * 60)
    print(f"读取记录 {stats['records']}，保留(有框) {stats['kept']}，边界框 {stats['boxes']}")
    print(f"类别顺序: {dict(enumerate(class_names))}")
    if stats["missing_image"]:
        print(f"[提示] {stats['missing_image']} 条记录未找到对应图片，仅生成标签")
    print(f"dataset.yaml: {out / 'dataset.yaml'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
