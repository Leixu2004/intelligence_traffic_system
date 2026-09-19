"""Trainable PyTorch LSTM for direct multi-step traffic-flow forecasting."""

from __future__ import annotations

from collections.abc import Sequence

try:
    import torch
    from torch import nn
except ImportError:  # pragma: no cover - training dependency
    torch = None
    nn = object  # type: ignore[assignment]


class LSTMForecastModel(nn.Module if torch is not None else object):
    """LSTM forecaster with legacy and teacher-compatible output contracts.

    The legacy three-argument constructor returns ``[current, future...]`` so
    existing training and inference code remains compatible. Supplying
    ``hidden_sizes`` selects independent LSTM stages and returns only future
    predictions unless ``include_current`` is explicitly enabled.
    """

    def __init__(
        self,
        hidden_size: int = 32,
        num_layers: int = 2,
        forecast_steps: int = 15,
        *,
        input_size: int = 1,
        hidden_sizes: Sequence[int] | None = None,
        dropout: float | None = None,
        fc_hidden_size: int | None = None,
        include_current: bool | None = None,
    ) -> None:
        if torch is None:  # pragma: no cover - guarded by the training entry point
            raise RuntimeError("PyTorch is required for LSTM training")
        super().__init__()
        if input_size < 1 or hidden_size < 1 or num_layers < 1 or forecast_steps < 1:
            raise ValueError("input_size、hidden_size、num_layers 和 forecast_steps 必须为正整数")
        if dropout is not None and not 0.0 <= dropout < 1.0:
            raise ValueError("dropout 必须满足 0 <= dropout < 1")
        if fc_hidden_size is not None and fc_hidden_size < 1:
            raise ValueError("fc_hidden_size 必须为正整数")

        staged_hidden_sizes = tuple(hidden_sizes or ())
        if staged_hidden_sizes and any(size < 1 for size in staged_hidden_sizes):
            raise ValueError("hidden_sizes 中的每个值都必须为正整数")
        self.input_size = input_size
        self.forecast_steps = forecast_steps
        self.include_current = hidden_sizes is None if include_current is None else include_current

        if staged_hidden_sizes:
            # 不同隐藏维度无法由单个多层 nn.LSTM 表达，因此逐层显式连接。
            layer_input_size = input_size
            layers = []
            for layer_hidden_size in staged_hidden_sizes:
                layers.append(
                    nn.LSTM(
                        input_size=layer_input_size,
                        hidden_size=layer_hidden_size,
                        num_layers=1,
                        batch_first=True,
                    )
                )
                layer_input_size = layer_hidden_size
            self.lstm = None
            self.lstm_layers = nn.ModuleList(layers)
            output_hidden_size = staged_hidden_sizes[-1]
            self.output_dropout = nn.Dropout(0.0 if dropout is None else dropout)
        else:
            legacy_dropout = 0.1 if dropout is None and num_layers > 1 else (dropout or 0.0)
            self.lstm = nn.LSTM(
                input_size=input_size,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=legacy_dropout,
                batch_first=True,
            )
            self.lstm_layers = nn.ModuleList()
            output_hidden_size = hidden_size
            self.output_dropout = nn.Identity()

        dense_hidden_size = fc_hidden_size or output_hidden_size
        self.head = nn.Sequential(
            nn.Linear(output_hidden_size, dense_hidden_size),
            nn.ReLU(),
            nn.Linear(dense_hidden_size, forecast_steps),
        )

    @classmethod
    def teacher_compat(
        cls,
        *,
        input_size: int = 4,
        hidden_sizes: Sequence[int] = (64, 32),
        dropout: float = 0.2,
        fc_hidden_size: int = 16,
        forecast_steps: int = 1,
    ) -> "LSTMForecastModel":
        """Build the teacher's two-stage, single-step forecasting model."""
        return cls(
            forecast_steps=forecast_steps,
            input_size=input_size,
            hidden_sizes=hidden_sizes,
            dropout=dropout,
            fc_hidden_size=fc_hidden_size,
            include_current=False,
        )

    def forward(self, features):  # type: ignore[no-untyped-def]
        if not torch.jit.is_tracing() and (features.ndim != 3 or features.shape[2] != self.input_size):
            raise ValueError(f"features 必须具有 [batch, time, {self.input_size}] 形状")
        sequence = features
        if self.lstm is not None:
            sequence, _ = self.lstm(sequence)
        else:
            for layer in self.lstm_layers:
                sequence, _ = layer(sequence)
        future = self.head(self.output_dropout(sequence[:, -1, :]))
        if not self.include_current:
            return future
        current = features[:, -1, 0].reshape(-1, 1)
        return torch.cat([current, future], dim=1)
