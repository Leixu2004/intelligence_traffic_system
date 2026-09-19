"""Export YOLO perception weights for ONNX Runtime or TensorRT edge inference."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SUPPORTED_FORMATS = {"onnx", "engine"}
SUPPORTED_CALIBRATION_SUFFIXES = {".yaml", ".yml"}


def require_file(path: str | Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label}不存在: {resolved}")
    return resolved


def calibration_images(directory: str | Path) -> list[Path]:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"INT8 校准目录不存在: {root}")
    images = sorted(
        path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    )
    if not images:
        raise ValueError(f"INT8 校准目录没有可用图片: {root}")
    return images


def _normalize_device(device: str) -> str:
    """Normalize the small device contract accepted by this wrapper."""
    if not isinstance(device, (str, int)) or isinstance(device, bool):
        raise TypeError("device 必须是 'cpu'、GPU 序号或 Ultralytics 支持的设备字符串")
    normalized = str(device).strip()
    if not normalized:
        raise ValueError("device 不能为空")
    return "cpu" if normalized.lower() == "cpu" else normalized


def _validate_image_size(image_size: int) -> int:
    if isinstance(image_size, bool) or not isinstance(image_size, int) or image_size <= 0:
        raise ValueError("imgsz 必须是正整数")
    return image_size


def _get_yolo_class():
    """Load Ultralytics lazily so argument validation and unit tests need no GPU runtime."""
    from ultralytics import YOLO

    return YOLO


def _resolve_calibration_data(calibration_data: str | Path, int8: bool) -> Path:
    path = Path(calibration_data).expanduser().resolve()
    if path.is_dir():
        # Keep the image-directory check useful for callers while making the final
        # requirement explicit: Ultralytics needs a dataset YAML for INT8 export.
        calibration_images(path)
        raise ValueError("Ultralytics INT8 导出需要 dataset.yaml；请用该目录创建数据集配置文件")
    if not path.is_file():
        raise FileNotFoundError(f"校准数据不存在: {path}")
    if not int8:
        raise ValueError("calibration_data 只能与 --int8 一起使用")
    if path.suffix.lower() not in SUPPORTED_CALIBRATION_SUFFIXES:
        raise ValueError("INT8 校准数据必须是 Ultralytics dataset.yaml")
    return path


def _resolve_exported_path(exported: Any, output_format: str) -> Path:
    if isinstance(exported, (str, Path)):
        path = Path(exported).expanduser().resolve()
    else:
        raise RuntimeError(f"Ultralytics 导出返回了无法识别的制品路径: {type(exported).__name__}")
    if not path.is_file():
        raise FileNotFoundError(f"Ultralytics 导出未生成制品文件: {path}")
    expected_suffix = f".{output_format}"
    if path.suffix.lower() != expected_suffix:
        raise RuntimeError(f"Ultralytics 导出结果扩展名错误，期望 {expected_suffix}: {path}")
    return path


def export_yolo(
    model_path: str | Path,
    output_format: str,
    image_size: int = 640,
    device: str = "cpu",
    half: bool = False,
    int8: bool = False,
    calibration_data: str | Path | None = None,
    simplify: bool = False,
) -> Path:
    source = require_file(model_path, "YOLO 权重")
    if not isinstance(output_format, str):
        raise TypeError("output_format 必须是 'onnx' 或 'engine'")
    output_format = output_format.strip().lower()
    if output_format not in SUPPORTED_FORMATS:
        raise ValueError("output_format 必须是 'onnx' 或 'engine'")
    image_size = _validate_image_size(image_size)
    device = _normalize_device(device)
    if half and int8:
        raise ValueError("FP16 与 INT8 不能同时启用")
    if output_format == "engine" and device.lower() == "cpu":
        raise ValueError("TensorRT engine 必须在带 NVIDIA GPU/TensorRT 的目标设备上导出")
    if simplify and output_format != "onnx":
        raise ValueError("simplify 仅适用于 ONNX 导出")
    if int8 and calibration_data is None:
        raise ValueError("INT8 导出必须提供代表性 dataset.yaml 校准数据")
    if not int8 and calibration_data is not None:
        raise ValueError("calibration_data 只能与 --int8 一起使用")

    options = {
        "format": output_format,
        "imgsz": image_size,
        "device": device,
        "dynamic": output_format == "onnx",
        "simplify": simplify if output_format == "onnx" else False,
    }
    if half:
        options["quantize"] = 16
    elif int8:
        options["quantize"] = 8
    if calibration_data is not None:
        calibration_path = _resolve_calibration_data(calibration_data, int8)
        options["data"] = str(calibration_path)

    exported = _get_yolo_class()(str(source)).export(**options)
    return _resolve_exported_path(exported, output_format)


def export_project_models(
    models: Iterable[Path],
    output_format: str,
    image_size: int,
    device: str,
    half: bool,
    int8: bool,
    calibration_data: str | Path | None,
    simplify: bool,
) -> list[Path]:
    return [
        export_yolo(model, output_format, image_size, device, half, int8, calibration_data, simplify)
        for model in models
    ]


def _parse_args():
    parser = argparse.ArgumentParser(description="导出车辆/车牌 YOLO 模型到 ONNX 或 TensorRT")
    parser.add_argument("models", nargs="+", help="一个或多个 .pt 权重")
    parser.add_argument("--format", choices=["onnx", "engine"], default="onnx")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="cpu", help="cpu、0、0,1 等 Ultralytics 设备参数")
    parser.add_argument("--half", action="store_true", help="导出 FP16；TensorRT engine 需支持的 GPU")
    parser.add_argument("--int8", action="store_true", help="导出 INT8；必须提供校准数据")
    parser.add_argument("--calibration-data", help="代表性图片目录或 Ultralytics dataset.yaml")
    parser.add_argument("--simplify", action="store_true", help="使用 onnxslim 简化 ONNX 图")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    results = export_project_models(
        [Path(value) for value in args.models],
        args.format,
        args.imgsz,
        args.device,
        args.half,
        args.int8,
        args.calibration_data,
        args.simplify,
    )
    for result in results:
        print(result)
