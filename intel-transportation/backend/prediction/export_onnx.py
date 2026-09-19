"""Export the location1 trend predictor to an ONNX model."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from .model import TrendForecastModel, torch


def export_model(output_path: Path, time_steps: int = 20) -> Path:
    if torch is None:
        raise RuntimeError("导出 ONNX 需要安装 PyTorch")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model = TrendForecastModel().eval()
    dummy = torch.ones((1, time_steps, 1), dtype=torch.float32)
    torch.onnx.export(
        model,
        dummy,
        str(output_path),
        input_names=["features"],
        output_names=["forecast"],
        dynamic_axes={"features": {0: "batch", 1: "time"}, "forecast": {0: "batch"}},
        opset_version=14,
        do_constant_folding=True,
        dynamo=False,
    )
    return output_path


def validate_model(model_path: Path, time_steps: int = 20) -> float:
    import onnxruntime as ort

    model = TrendForecastModel().eval()
    sample = np.linspace(1, 20, time_steps, dtype=np.float32).reshape(1, time_steps, 1)
    with torch.no_grad():
        expected = model(torch.from_numpy(sample)).numpy()
    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    actual = session.run(None, {session.get_inputs()[0].name: sample})[0]
    error = float(np.max(np.abs(expected - actual)))
    if error >= 0.01:
        raise AssertionError(f"ONNX 与 PyTorch 输出误差过大：{error}")
    return error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("models") / "location1_trend_v1.onnx")
    parser.add_argument("--time-steps", type=int, default=20)
    args = parser.parse_args()
    path = export_model(args.output, args.time_steps)
    print(f"导出完成：{path}")
    try:
        print(f"最大误差：{validate_model(path, args.time_steps):.8f}")
    except ImportError:
        print("未安装 onnxruntime，已跳过一致性校验")


if __name__ == "__main__":
    main()
