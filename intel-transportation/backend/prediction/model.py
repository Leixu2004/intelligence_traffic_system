"""ONNX-exportable forecasting kernel derived from the location1 demo.

The original demo has no trained neural-network checkpoint. It computes a
moving average and extrapolates its linear trend. This module preserves that
validated algorithm in a small tensor model so the same computation can run
through ONNX Runtime in the service layer.
"""

from __future__ import annotations

try:
    import torch
    from torch import nn
except ImportError:  # pragma: no cover - export-only dependency
    torch = None
    nn = object  # type: ignore[assignment]


class TrendForecastModel(nn.Module if torch is not None else object):
    """Forecast future values from an already smoothed [B, T, 1] sequence."""

    def __init__(self, forecast_points: int = 16) -> None:
        if torch is not None:
            super().__init__()
        self.forecast_points = forecast_points

    def forward(self, features):  # type: ignore[no-untyped-def]
        if torch is None:  # pragma: no cover - guarded by exporter
            raise RuntimeError("PyTorch is required to run the export model")

        values = features[:, :, 0]
        time_index = torch.arange(values.shape[1], dtype=values.dtype, device=values.device)
        time_index = time_index.reshape(1, -1)
        time_mean = time_index.mean(dim=1, keepdim=True)
        value_mean = values.mean(dim=1, keepdim=True)
        centered_time = time_index - time_mean
        centered_values = values - value_mean
        denominator = (centered_time * centered_time).sum(dim=1, keepdim=True).clamp_min(1e-6)
        slope = (centered_time * centered_values).sum(dim=1, keepdim=True) / denominator
        offsets = torch.arange(self.forecast_points, dtype=values.dtype, device=values.device)
        forecast = values[:, -1:].add(slope * offsets.reshape(1, -1))
        return torch.clamp(forecast, min=0.0)
