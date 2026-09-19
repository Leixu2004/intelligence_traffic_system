# -*- coding: utf-8 -*-
"""
UA-DETRAC 官方标注 -> YOLO 数据集转换脚本
=========================================

适用数据（二选一，推荐 v3）：
  * DETRAC-Train-Annotations-XML     ：每个序列一个 XML，含框/遮挡/天气等，
                                       但【不含】每辆车的细分类别，默认按 car 处理。
  * DETRAC-Train-Annotations-XML-v3  ：在 v1 基础上补充了车型（Sedan/SUV/Van/
                                       Taxi/Bus/Truck 等）与颜色，推荐用于分类训练。

官方图片解压后目录形如：
  <images_root>/Ins-MVI_40011/img00001.jpg
  <images_root>/Ins-MVI_40012/img00002.jpg
标注 XML 形如：
  <xml_root>/MVI_40011.xml
      <sequence><sequence_name>MVI_40011</sequence_name>...
        <ignored_region><box x y w h/>...</ignored_region>   # 忽略区域，不产出标签
        <vehicle id="1">
          <box frame="1" occluded="0" out_of_view="0">
            <x>..</x><y>..</y><w>..</w><h>..</h>
          </box>
          <!-- v3 可能附带车型/颜色标签，脚本会自动识别；v1 没有则用默认类别 -->
        </vehicle>

产出标准 Ultralytics YOLO 目录：
  <out>/images/{train,val}/<seq>_img00001.jpg   （默认硬链接，不额外占空间）
  <out>/labels/{train,val}/<seq>_img00001.txt
  <out>/train.txt、val.txt、dataset.yaml

按“序列”而非“帧”划分 train/val，避免同一段视频的相邻帧同时进入训练集和验证集
（防止时序泄漏）。

许可提示：UA-DETRAC 为 CC BY-NC-SA 3.0，仅限署名、非商业、相同方式共享的教学/研究用途。

用法示例：
  python detrac_to_yolo.py \
      --xml-root  D:/datasets/DETRAC/Train-Annotations-XML-v3 \
      --images-root D:/datasets/DETRAC/DETRAC-train-data \
      --out D:/datasets/DETRAC/yolo_detrac \
      --classes project4 --val-ratio 0.1
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

# UA-DETRAC 原始分辨率（官方统一 960x540 @25fps）。无法读取真实图片时用它做归一化兜底。
DEFAULT_WIDTH = 960
DEFAULT_HEIGHT = 540

# 三种类别方案。键为统一后的内部类别，值用于匹配标注里出现的英文关键词。
CLASS_SCHEMES = {
    # 与本项目 config.VEHICLE_CLASSES 语义对齐的 4 类（推荐，v3 标注才能区分 truck/van）
    "project4": {
        "car": ["sedan", "suv", "hatchback", "police", "taxi", "car", "others", "other"],
        "van": ["van", "minivan", "mini van"],
        "bus": ["bus"],
        "truck": ["truck", "pickup", "flatbed", "box med", "box large", "util"],
    },
    # 官方 v1 检测基准的 4 类
    "detrac4": {
        "car": ["sedan", "suv", "hatchback", "police", "taxi", "car"],
        "bus": ["bus"],
        "van": ["van", "minivan"],
        "others": ["others", "other", "truck", "pickup", "flatbed", "misc"],
    },
    # 单类别：只做“车辆”检测，v1 标注即可，最省事
    "vehicle": {
        "vehicle": ["car", "sedan", "suv", "van", "bus", "truck", "taxi", "others", "other"],
    },
}


def normalize_text(text: str | None) -> str:
    return (text or "").strip().lower().replace("_", " ").replace("-", " ")


def build_keyword_index(scheme: dict[str, list[str]]) -> list[tuple[str, str]]:
    index: list[tuple[str, str]] = []
    for cls, keywords in scheme.items():
        for kw in keywords:
            index.append((normalize_text(kw), cls))
    # 长关键词优先匹配，避免 "truck box large" 被提前命中
    index.sort(key=lambda item: len(item[0]), reverse=True)
    return index


def classify_vehicle(vehicle_node: ET.Element, keyword_index, default_class: str) -> str:
    """从 <vehicle> 节点里尽力解析车型；v1 没有车型标签时回退到 default_class。"""
    candidates: list[str] = []
    # 1) 子标签：vehicle_type / type / category / class / color(仅用于识别，不参与) 等
    for child in vehicle_node:
        tag = normalize_text(child.tag)
        if any(k in tag for k in ("type", "category", "class", "model")):
            candidates.append(child.text or "")
    # 2) 属性：type / vehicle_type / category / class
    for attr_name, attr_val in vehicle_node.attrib.items():
        if any(k in normalize_text(attr_name) for k in ("type", "category", "class")):
            candidates.append(attr_val)
    haystack = normalize_text(" ".join(candidates))
    if haystack:
        for keyword, cls in keyword_index:
            if keyword and keyword in haystack:
                return cls
    return default_class


def find_image_dir(images_root: Path, sequence_name: str) -> Path | None:
    """官方图片目录可能叫 MVI_xxx 或 Ins-MVI_xxx，做后缀模糊匹配。"""
    direct = images_root / sequence_name
    if direct.is_dir():
        return direct
    matches = [p for p in images_root.iterdir() if p.is_dir() and p.name.endswith(sequence_name)]
    return matches[0] if matches else None


def frame_filename(frame: int) -> str:
    return f"img{frame:05d}.jpg"


def link_or_copy(src: Path, dst: Path, mode: str) -> str:
    """把图片放进标准 YOLO 目录，默认硬链接（同盘不占额外空间），失败再回退复制。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return "exists"
    if mode == "none":
        return "skipped"
    try:
        if mode == "symlink":
            os.symlink(src, dst)
        elif mode == "copy":
            shutil.copy2(src, dst)
        else:  # hardlink（默认）
            os.link(src, dst)
        return mode
    except OSError:
        # 跨盘硬链接 / 无权限建软链时，回退为真实复制
        try:
            shutil.copy2(src, dst)
            return "copy(fallback)"
        except OSError as exc:
            print(f"  [警告] 无法放置图片 {src.name}: {exc}")
            return "failed"


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def parse_sequence(xml_path: Path, keyword_index, class_names: list[str], default_class: str):
    """解析单个序列 XML，返回 {frame: [(cls_id, xc, yc, w, h), ...]}。"""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    seq_name_node = root.find("sequence_name")
    sequence_name = seq_name_node.text.strip() if seq_name_node is not None else xml_path.stem

    per_frame: dict[int, list[tuple]] = {}
    for vehicle_node in root.iter("vehicle"):
        cls_name = classify_vehicle(vehicle_node, keyword_index, default_class)
        if cls_name not in class_names:
            continue
        cls_id = class_names.index(cls_name)
        for box in vehicle_node.iter("box"):
            # ignored_region 里的 box 不在 vehicle 下，不会被遍历到，这里再保险排除
            frame = box.attrib.get("frame")
            if frame is None:
                continue
            frame = int(frame)
            out_of_view = box.attrib.get("out_of_view", "0")
            if out_of_view not in ("0", "false", "False"):
                continue  # 完全出画的框不参与训练
            try:
                x = float(box.find("x").text)
                y = float(box.find("y").text)
                w = float(box.find("w").text)
                h = float(box.find("h").text)
            except (AttributeError, TypeError, ValueError):
                continue
            if w <= 1 or h <= 1:
                continue
            per_frame.setdefault(frame, []).append((cls_name, cls_id, x, y, w, h))
    return sequence_name, per_frame


def main() -> None:
    parser = argparse.ArgumentParser(description="UA-DETRAC XML -> YOLO 数据集转换")
    parser.add_argument("--xml-root", required=True, help="解压后的序列 XML 目录（v1 或 v3）")
    parser.add_argument("--images-root", required=True, help="解压后的图片根目录（含 Ins-MVI_xxx）")
    parser.add_argument("--out", required=True, help="输出 YOLO 数据集目录")
    parser.add_argument(
        "--classes",
        choices=list(CLASS_SCHEMES.keys()),
        default="project4",
        help="类别方案：project4(默认,对齐本项目)/detrac4(官方四类)/vehicle(单类)",
    )
    parser.add_argument("--val-ratio", type=float, default=0.1, help="按序列划分验证集比例")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--link",
        choices=["hardlink", "symlink", "copy", "none"],
        default="hardlink",
        help="图片放置方式：hardlink(默认,省空间)/symlink/copy/none(只生成标签)",
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    args = parser.parse_args()

    xml_root = Path(args.xml_root)
    images_root = Path(args.images_root)
    out = Path(args.out)
    if not xml_root.is_dir():
        raise SystemExit(f"XML 目录不存在: {xml_root}")

    scheme = CLASS_SCHEMES[args.classes]
    # 固定类别顺序，保证 class id 稳定可复现
    class_names = list(scheme.keys())
    default_class = class_names[0]
    keyword_index = build_keyword_index(scheme)

    xml_files = sorted(xml_root.glob("*.xml"))
    if not xml_files:
        raise SystemExit(f"在 {xml_root} 下没有找到 *.xml")
    random.Random(args.seed).shuffle(xml_files)
    val_count = max(1, round(len(xml_files) * args.val_ratio)) if len(xml_files) > 1 else 0
    val_xml = set(p.stem for p in xml_files[:val_count])

    stats = {"sequences": 0, "frames": 0, "boxes": 0, "missing_image_dir": []}
    split_lists = {"train": [], "val": []}

    for xml_path in sorted(xml_root.glob("*.xml")):
        split = "val" if xml_path.stem in val_xml else "train"
        sequence_name, per_frame = parse_sequence(xml_path, keyword_index, class_names, default_class)
        image_dir = find_image_dir(images_root, sequence_name)
        if image_dir is None:
            stats["missing_image_dir"].append(sequence_name)
        stats["sequences"] += 1

        for frame, boxes in sorted(per_frame.items()):
            stem = f"{sequence_name}_{frame_filename(frame).removesuffix('.jpg')}"
            label_path = out / "labels" / split / f"{stem}.txt"
            label_path.parent.mkdir(parents=True, exist_ok=True)

            width, height = args.width, args.height
            src_image = image_dir / frame_filename(frame) if image_dir else None
            if src_image and src_image.exists():
                # 尝试读取真实分辨率（Pillow），失败则用默认 960x540
                try:
                    from PIL import Image

                    with Image.open(src_image) as im:
                        width, height = im.size
                except Exception:
                    pass
                dst_image = out / "images" / split / f"{stem}.jpg"
                link_or_copy(src_image, dst_image, args.link)
                split_lists[split].append(str(dst_image.resolve()))

            lines = []
            for _cls_name, cls_id, x, y, w, h in boxes:
                xc = clamp((x + w / 2.0) / width)
                yc = clamp((y + h / 2.0) / height)
                nw = clamp(w / width)
                nh = clamp(h / height)
                if nw <= 0 or nh <= 0:
                    continue
                lines.append(f"{cls_id} {xc:.6f} {yc:.6f} {nw:.6f} {nh:.6f}")
            if lines:
                label_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                stats["frames"] += 1
                stats["boxes"] += len(lines)

    # 生成图片清单与 dataset.yaml
    for split, items in split_lists.items():
        list_path = out / f"{split}.txt"
        list_path.write_text("\n".join(items) + ("\n" if items else ""), encoding="utf-8")

    yaml_text = (
        f"# 由 detrac_to_yolo.py 自动生成，类别方案={args.classes}\n"
        f"path: {out.resolve().as_posix()}\n"
        f"train: train.txt\n"
        f"val: val.txt\n"
        f"names:\n"
        + "".join(f"  {i}: {name}\n" for i, name in enumerate(class_names))
    )
    (out / "dataset.yaml").write_text(yaml_text, encoding="utf-8")

    print("=" * 60)
    print(f"序列数: {stats['sequences']}（验证序列 {len(val_xml)}）")
    print(f"产出带标注帧: {stats['frames']}，边界框: {stats['boxes']}")
    print(f"类别顺序(class id): {dict(enumerate(class_names))}")
    print(f"dataset.yaml: {out / 'dataset.yaml'}")
    if stats["missing_image_dir"]:
        print(f"[提示] {len(stats['missing_image_dir'])} 个序列未找到图片目录，仅生成了标签：")
        print("       " + ", ".join(stats["missing_image_dir"][:10]) + (" ..." if len(stats["missing_image_dir"]) > 10 else ""))
    print("=" * 60)
    print("下一步：python train_vehicle_detector.py --data " + str(out / "dataset.yaml"))


if __name__ == "__main__":
    main()
