"""Compare YOLO detection outputs across PyTorch, ONNX, or TensorRT backends."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass(frozen=True)
class Detection:
    box: tuple[float, float, float, float]
    confidence: float
    class_id: int


def require_file(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label}不存在: {resolved}")
    return resolved


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_images(inputs: Iterable[str | Path]) -> list[Path]:
    images: set[Path] = set()
    for value in inputs:
        path = Path(value).expanduser().resolve()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
            images.add(path)
        elif path.is_dir():
            images.update(
                child.resolve()
                for child in path.rglob("*")
                if child.is_file() and child.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
            )
        else:
            raise FileNotFoundError(f"图片或目录不存在: {path}")
    if not images:
        raise ValueError("没有找到可比较的图片")
    return sorted(images)


def box_iou(
    first: Sequence[float],
    second: Sequence[float],
) -> float:
    if len(first) != 4 or len(second) != 4:
        raise ValueError("检测框必须包含 [x1, y1, x2, y2]")
    ax1, ay1, ax2, ay2 = map(float, first)
    bx1, by1, bx2, by2 = map(float, second)
    if ax2 < ax1 or ay2 < ay1 or bx2 < bx1 or by2 < by1:
        raise ValueError("检测框坐标顺序无效")

    intersection_width = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    first_area = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    second_area = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = first_area + second_area - intersection
    return intersection / union if union > 0.0 else 0.0


def match_detections(
    reference: Sequence[Detection],
    candidate: Sequence[Detection],
    iou_threshold: float = 0.5,
) -> list[dict[str, float | int]]:
    if not 0.0 <= iou_threshold <= 1.0:
        raise ValueError("iou_threshold 必须位于 0 到 1")

    possible_matches: list[tuple[float, int, int]] = []
    for reference_index, reference_detection in enumerate(reference):
        for candidate_index, candidate_detection in enumerate(candidate):
            if reference_detection.class_id != candidate_detection.class_id:
                continue
            iou = box_iou(reference_detection.box, candidate_detection.box)
            if iou >= iou_threshold:
                possible_matches.append((iou, reference_index, candidate_index))

    # 先选 IoU 最高的配对，确保一个框不会重复匹配多个候选框。
    possible_matches.sort(reverse=True)
    used_reference: set[int] = set()
    used_candidate: set[int] = set()
    matches: list[dict[str, float | int]] = []
    for iou, reference_index, candidate_index in possible_matches:
        if reference_index in used_reference or candidate_index in used_candidate:
            continue
        used_reference.add(reference_index)
        used_candidate.add(candidate_index)
        reference_detection = reference[reference_index]
        candidate_detection = candidate[candidate_index]
        matches.append(
            {
                "reference_index": reference_index,
                "candidate_index": candidate_index,
                "class_id": reference_detection.class_id,
                "iou": iou,
                "confidence_delta": abs(
                    reference_detection.confidence - candidate_detection.confidence
                ),
            }
        )
    return matches


def summarise_image(
    image: Path,
    reference: Sequence[Detection],
    candidate: Sequence[Detection],
    iou_threshold: float,
) -> dict[str, Any]:
    matches = match_detections(reference, candidate, iou_threshold)
    reference_count = len(reference)
    candidate_count = len(candidate)
    matched_count = len(matches)
    return {
        "image": str(image),
        "reference_count": reference_count,
        "candidate_count": candidate_count,
        "matched_count": matched_count,
        "reference_match_rate": matched_count / reference_count if reference_count else 1.0,
        "candidate_match_rate": matched_count / candidate_count if candidate_count else 1.0,
        "mean_iou": (
            float(np.mean([match["iou"] for match in matches])) if matches else None
        ),
        "mean_confidence_delta": (
            float(np.mean([match["confidence_delta"] for match in matches]))
            if matches
            else None
        ),
        "reference": [asdict(detection) for detection in reference],
        "candidate": [asdict(detection) for detection in candidate],
        "matches": matches,
    }


def aggregate_summaries(summaries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    reference_count = sum(int(item["reference_count"]) for item in summaries)
    candidate_count = sum(int(item["candidate_count"]) for item in summaries)
    matched_count = sum(int(item["matched_count"]) for item in summaries)
    matches = [match for item in summaries for match in item["matches"]]
    return {
        "image_count": len(summaries),
        "images_with_reference_detections": sum(
            int(item["reference_count"] > 0) for item in summaries
        ),
        "reference_count": reference_count,
        "candidate_count": candidate_count,
        "matched_count": matched_count,
        "reference_match_rate": matched_count / reference_count if reference_count else 1.0,
        "candidate_match_rate": matched_count / candidate_count if candidate_count else 1.0,
        "mean_iou": float(np.mean([match["iou"] for match in matches])) if matches else None,
        "mean_confidence_delta": (
            float(np.mean([match["confidence_delta"] for match in matches]))
            if matches
            else None
        ),
        "max_confidence_delta": (
            float(max(match["confidence_delta"] for match in matches)) if matches else None
        ),
    }


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def extract_detections(result: Any) -> list[Detection]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return []
    coordinates = _to_numpy(boxes.xyxy)
    confidences = _to_numpy(boxes.conf).reshape(-1)
    classes = _to_numpy(boxes.cls).reshape(-1)
    return [
        Detection(
            box=tuple(float(value) for value in coordinates[index]),
            confidence=float(confidences[index]),
            class_id=int(classes[index]),
        )
        for index in range(len(coordinates))
    ]


def compare_models(
    reference_model: str | Path,
    candidate_model: str | Path,
    image_inputs: Iterable[str | Path],
    confidence: float = 0.25,
    nms_iou: float = 0.7,
    match_iou: float = 0.5,
    image_size: int = 640,
    reference_device: str = "cpu",
    candidate_device: str = "cpu",
) -> dict[str, Any]:
    reference_path = require_file(reference_model, "参考模型")
    candidate_path = require_file(candidate_model, "候选模型")
    images = collect_images(image_inputs)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence 必须位于 0 到 1")
    if not 0.0 <= nms_iou <= 1.0:
        raise ValueError("nms_iou 必须位于 0 到 1")
    if not 0.0 <= match_iou <= 1.0:
        raise ValueError("match_iou 必须位于 0 到 1")
    if image_size < 1:
        raise ValueError("image_size 必须为正整数")

    from ultralytics import YOLO

    reference = YOLO(str(reference_path))
    candidate = YOLO(str(candidate_path))
    summaries: list[dict[str, Any]] = []
    for image in images:
        common = {
            "source": str(image),
            "conf": confidence,
            "iou": nms_iou,
            "imgsz": image_size,
            "verbose": False,
        }
        reference_result = reference.predict(device=reference_device, **common)[0]
        candidate_result = candidate.predict(device=candidate_device, **common)[0]
        summaries.append(
            summarise_image(
                image,
                extract_detections(reference_result),
                extract_detections(candidate_result),
                match_iou,
            )
        )

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "comparison_scope": "post_nms_detection_equivalence",
        "reference_model": {
            "path": str(reference_path),
            "sha256": sha256_file(reference_path),
        },
        "candidate_model": {
            "path": str(candidate_path),
            "sha256": sha256_file(candidate_path),
        },
        "settings": {
            "confidence": confidence,
            "nms_iou": nms_iou,
            "match_iou": match_iou,
            "image_size": image_size,
            "reference_device": reference_device,
            "candidate_device": candidate_device,
        },
        "summary": aggregate_summaries(summaries),
        "images": summaries,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比较两个 YOLO 后端的检测输出等价性")
    parser.add_argument("reference_model", help="参考模型，通常为 .pt")
    parser.add_argument("candidate_model", help="候选模型，通常为 .onnx 或 .engine")
    parser.add_argument("images", nargs="+", help="一个或多个图片/目录")
    parser.add_argument("--output", required=True, help="JSON 报告输出路径")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.7)
    parser.add_argument("--match-iou", type=float, default=0.5)
    parser.add_argument("--imgsz", type=int, default=640, help="两个后端共用的 YOLO 推理尺寸")
    parser.add_argument("--reference-device", default="cpu")
    parser.add_argument("--candidate-device", default="cpu")
    parser.add_argument("--min-reference-match-rate", type=float)
    parser.add_argument("--min-mean-iou", type=float)
    parser.add_argument("--max-mean-confidence-delta", type=float)
    return parser.parse_args()


def _threshold_failures(report: dict[str, Any], args: argparse.Namespace) -> list[str]:
    summary = report["summary"]
    failures: list[str] = []
    if (
        args.min_reference_match_rate is not None
        and summary["reference_match_rate"] < args.min_reference_match_rate
    ):
        failures.append(
            f"reference_match_rate={summary['reference_match_rate']:.4f} "
            f"< {args.min_reference_match_rate:.4f}"
        )
    if args.min_mean_iou is not None:
        mean_iou = summary["mean_iou"]
        if mean_iou is None or mean_iou < args.min_mean_iou:
            failures.append(f"mean_iou={mean_iou} < {args.min_mean_iou:.4f}")
    if args.max_mean_confidence_delta is not None:
        delta = summary["mean_confidence_delta"]
        if delta is None or delta > args.max_mean_confidence_delta:
            failures.append(
                f"mean_confidence_delta={delta} > {args.max_mean_confidence_delta:.4f}"
            )
    return failures


def main() -> None:
    args = _parse_args()
    report = compare_models(
        args.reference_model,
        args.candidate_model,
        args.images,
        confidence=args.conf,
        nms_iou=args.nms_iou,
        match_iou=args.match_iou,
        image_size=args.imgsz,
        reference_device=args.reference_device,
        candidate_device=args.candidate_device,
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    failures = _threshold_failures(report, args)
    if failures:
        raise SystemExit("模型等价性门槛未通过: " + "; ".join(failures))


if __name__ == "__main__":
    main()
