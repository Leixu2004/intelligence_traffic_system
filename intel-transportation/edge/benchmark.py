"""Reproducible ONNX Runtime model-level benchmark for edge artifacts."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import time
from typing import Any

import numpy as np


REPORT_SCHEMA_VERSION = "edge-onnx-benchmark-v1"
DEFAULT_IMAGE_SIZE = 640

PROVIDER_ALIASES = {
    "cpu": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "tensorrt": "TensorrtExecutionProvider",
    "tensorrt_execution_provider": "TensorrtExecutionProvider",
}

ORT_TO_NUMPY_DTYPE: dict[str, np.dtype[Any]] = {
    "tensor(float)": np.dtype(np.float32),
    "tensor(float16)": np.dtype(np.float16),
    "tensor(double)": np.dtype(np.float64),
    "tensor(int8)": np.dtype(np.int8),
    "tensor(int16)": np.dtype(np.int16),
    "tensor(int32)": np.dtype(np.int32),
    "tensor(int64)": np.dtype(np.int64),
    "tensor(uint8)": np.dtype(np.uint8),
    "tensor(uint16)": np.dtype(np.uint16),
    "tensor(uint32)": np.dtype(np.uint32),
    "tensor(uint64)": np.dtype(np.uint64),
    "tensor(bool)": np.dtype(np.bool_),
    "tensor(string)": np.dtype(object),
}


def normalize_provider_names(providers: Sequence[str]) -> list[str]:
    """Expand friendly Provider aliases and preserve the requested priority."""

    normalized: list[str] = []
    for raw_provider in providers:
        provider = str(raw_provider).strip()
        if not provider:
            raise ValueError("Execution Provider name must not be empty")
        resolved = PROVIDER_ALIASES.get(provider.lower(), provider)
        if resolved not in normalized:
            normalized.append(resolved)
    return normalized


def percentile(values: Sequence[float], percent: float) -> float:
    """Return a linearly interpolated percentile without timing dependencies."""

    if not values:
        raise ValueError("latency samples must not be empty")
    if not 0.0 <= percent <= 100.0:
        raise ValueError("percent must be between 0 and 100")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percent / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_latencies(latencies_seconds: Sequence[float], batch_size: int) -> dict[str, float | int]:
    """Summarize synchronous model-run durations using milliseconds and samples/s."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if not latencies_seconds:
        raise ValueError("latency samples must not be empty")
    if any(value <= 0 for value in latencies_seconds):
        raise ValueError("latency samples must be positive")

    latency_ms = [float(value) * 1000.0 for value in latencies_seconds]
    total_seconds = float(sum(latencies_seconds))
    avg_ms = statistics.fmean(latency_ms)
    runs_per_second = len(latencies_seconds) / total_seconds
    samples_per_second = batch_size * runs_per_second
    return {
        "iterations": len(latencies_seconds),
        "batch_size": batch_size,
        "total_time_ms": total_seconds * 1000.0,
        "avg_ms": avg_ms,
        "p50_ms": percentile(latency_ms, 50.0),
        "p95_ms": percentile(latency_ms, 95.0),
        "min_ms": min(latency_ms),
        "max_ms": max(latency_ms),
        "runs_per_second": runs_per_second,
        "samples_per_second": samples_per_second,
        "fps": samples_per_second,
    }


def parse_shape_overrides(values: Sequence[str] | None) -> dict[str, tuple[int, ...]]:
    """Parse repeatable ``INPUT=DIM,DIM`` command-line values."""

    overrides: dict[str, tuple[int, ...]] = {}
    for value in values or []:
        name, separator, raw_shape = value.partition("=")
        if not separator or not name.strip() or not raw_shape.strip():
            raise ValueError(f"invalid --shape value: {value!r}; expected INPUT=DIM,DIM")
        try:
            shape = tuple(int(dimension.strip()) for dimension in raw_shape.split(","))
        except ValueError as exc:
            raise ValueError(f"invalid --shape dimensions: {value!r}") from exc
        if not shape or any(dimension < 1 for dimension in shape):
            raise ValueError(f"--shape dimensions must be positive: {value!r}")
        input_name = name.strip()
        if input_name in overrides:
            raise ValueError(f"duplicate --shape for input: {input_name}")
        overrides[input_name] = shape
    return overrides


def resolve_input_shape(
    input_name: str,
    model_shape: Sequence[Any],
    batch_size: int,
    shape_override: Sequence[int] | None = None,
    image_size: int = DEFAULT_IMAGE_SIZE,
) -> tuple[int, ...]:
    """Resolve an ONNX input shape while preserving every static dimension."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if image_size < 1:
        raise ValueError("image_size must be positive")
    if shape_override is not None:
        resolved = tuple(int(dimension) for dimension in shape_override)
        if len(resolved) != len(model_shape):
            raise ValueError(
                f"input {input_name!r} expects rank {len(model_shape)}, "
                f"but --shape supplied rank {len(resolved)}"
            )
        if any(dimension < 1 for dimension in resolved):
            raise ValueError(f"input {input_name!r} shape dimensions must be positive")
        if resolved and resolved[0] != batch_size:
            raise ValueError(
                f"input {input_name!r} --shape batch {resolved[0]} does not match "
                f"batch_size {batch_size}"
            )
        for index, (declared, supplied) in enumerate(zip(model_shape, resolved)):
            if isinstance(declared, int) and declared > 0 and declared != supplied:
                raise ValueError(
                    f"input {input_name!r} dimension {index} is fixed at {declared}, "
                    f"but --shape supplied {supplied}"
                )
        return resolved

    resolved_dimensions: list[int] = []
    for index, dimension in enumerate(model_shape):
        if isinstance(dimension, int) and dimension > 0:
            if index == 0 and dimension != batch_size:
                raise ValueError(
                    f"input {input_name!r} has fixed batch {dimension}; "
                    f"requested batch_size is {batch_size}"
                )
            resolved_dimensions.append(dimension)
        elif index == 0:
            resolved_dimensions.append(batch_size)
        elif len(model_shape) == 4 and index in {2, 3}:
            resolved_dimensions.append(image_size)
        else:
            raise ValueError(
                f"input {input_name!r} has unresolved dynamic dimension {index} "
                f"in {list(model_shape)!r}; provide --shape {input_name}=..."
            )
    return tuple(resolved_dimensions)


def _random_tensor(rng: np.random.Generator, shape: tuple[int, ...], ort_type: str) -> np.ndarray:
    dtype = ORT_TO_NUMPY_DTYPE.get(ort_type)
    if dtype is None:
        raise ValueError(f"unsupported ONNX Runtime input type: {ort_type}")
    if dtype == np.dtype(object):
        return np.full(shape, "", dtype=object)
    if np.issubdtype(dtype, np.floating):
        return rng.standard_normal(shape).astype(dtype)
    if np.issubdtype(dtype, np.bool_):
        return rng.integers(0, 2, size=shape, dtype=np.int8).astype(dtype)
    return rng.integers(0, 2, size=shape, dtype=dtype)


def build_random_inputs(
    session: Any,
    batch_size: int,
    shape_overrides: Mapping[str, Sequence[int]] | None = None,
    image_size: int = DEFAULT_IMAGE_SIZE,
    seed: int = 0,
) -> tuple[dict[str, np.ndarray], list[dict[str, Any]]]:
    """Build deterministic synthetic inputs from the session's real contract."""

    overrides = dict(shape_overrides or {})
    input_metadata = list(session.get_inputs())
    known_names = {item.name for item in input_metadata}
    unknown_names = sorted(set(overrides) - known_names)
    if unknown_names:
        raise ValueError(f"--shape references unknown inputs: {', '.join(unknown_names)}")

    rng = np.random.default_rng(seed)
    feeds: dict[str, np.ndarray] = {}
    contracts: list[dict[str, Any]] = []
    for item in input_metadata:
        shape = resolve_input_shape(
            item.name,
            item.shape,
            batch_size=batch_size,
            shape_override=overrides.get(item.name),
            image_size=image_size,
        )
        value = _random_tensor(rng, shape, item.type)
        feeds[item.name] = value
        contracts.append(
            {
                "name": item.name,
                "onnx_type": item.type,
                "model_shape": list(item.shape),
                "benchmark_shape": list(shape),
                "numpy_dtype": str(value.dtype),
            }
        )
    return feeds, contracts


def benchmark_session(
    session: Any,
    feeds: Mapping[str, np.ndarray],
    warmup_iterations: int = 10,
    measured_iterations: int = 100,
    batch_size: int = 1,
    clock: Callable[[], float] = time.perf_counter,
    synchronize: Callable[[], None] | None = None,
) -> dict[str, float | int]:
    """Run warmup and measured synchronous ``InferenceSession.run`` calls."""

    if warmup_iterations < 0:
        raise ValueError("warmup_iterations must not be negative")
    if measured_iterations < 1:
        raise ValueError("measured_iterations must be positive")
    sync = synchronize or (lambda: None)

    for _ in range(warmup_iterations):
        session.run(None, dict(feeds))
        sync()

    latencies: list[float] = []
    for _ in range(measured_iterations):
        sync()
        started = clock()
        session.run(None, dict(feeds))
        sync()
        elapsed = clock() - started
        if elapsed <= 0:
            raise ValueError("clock must produce a positive duration for every iteration")
        latencies.append(elapsed)
    return summarize_latencies(latencies, batch_size=batch_size)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_environment() -> dict[str, Any]:
    """Collect portable hardware and software facts without vendor CLIs."""

    processor = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or "unknown"
    result: dict[str, Any] = {
        "hostname": platform.node(),
        "os": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": processor,
        "logical_cpu_count": os.cpu_count(),
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
    }
    try:
        import psutil

        memory = psutil.virtual_memory()
        result.update(
            {
                "physical_cpu_count": psutil.cpu_count(logical=False),
                "memory_total_bytes": int(memory.total),
            }
        )
    except ImportError:
        result.update({"physical_cpu_count": None, "memory_total_bytes": None})
    return result


def collect_memory_snapshot() -> dict[str, int | None]:
    """Return process RSS when psutil is available; never claim native peak RSS."""

    try:
        import psutil

        process = psutil.Process()
        return {"process_rss_bytes": int(process.memory_info().rss)}
    except ImportError:
        return {"process_rss_bytes": None}


def _create_session(model_path: Path, providers: Sequence[str], intra_op_threads: int) -> tuple[Any, str, list[str]]:
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - dependency is present in project environments
        raise RuntimeError("onnxruntime is required to benchmark ONNX models") from exc

    requested = normalize_provider_names(providers)
    available = list(ort.get_available_providers())
    unavailable = [provider for provider in requested if provider not in available]
    if unavailable:
        raise RuntimeError(
            f"requested Execution Provider is unavailable: {', '.join(unavailable)}; "
            f"available providers: {', '.join(available)}"
        )
    options = ort.SessionOptions()
    if intra_op_threads > 0:
        options.intra_op_num_threads = intra_op_threads
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=requested)
    return session, ort.__version__, available


def run_benchmark(
    model_path: str | Path,
    *,
    providers: Sequence[str] = ("CPUExecutionProvider",),
    batch_size: int = 1,
    warmup_iterations: int = 10,
    measured_iterations: int = 100,
    shape_overrides: Mapping[str, Sequence[int]] | None = None,
    image_size: int = DEFAULT_IMAGE_SIZE,
    seed: int = 0,
    intra_op_threads: int = 0,
    clock: Callable[[], float] = time.perf_counter,
    session_factory: Callable[[Path, Sequence[str], int], tuple[Any, str, list[str]]] = _create_session,
) -> dict[str, Any]:
    """Load one ONNX model, benchmark it, and return a JSON-serializable report."""

    model = Path(model_path).expanduser().resolve()
    if not model.is_file():
        raise FileNotFoundError(f"ONNX model does not exist: {model}")
    if model.suffix.lower() != ".onnx":
        raise ValueError(f"benchmark expects an .onnx model: {model}")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if intra_op_threads < 0:
        raise ValueError("intra_op_threads must not be negative")
    if not providers:
        raise ValueError("at least one Execution Provider must be requested")

    requested_providers = normalize_provider_names(providers)

    memory_before = collect_memory_snapshot()
    load_started = clock()
    session, runtime_version, available_providers = session_factory(model, requested_providers, intra_op_threads)
    load_time_ms = (clock() - load_started) * 1000.0
    feeds, input_contracts = build_random_inputs(
        session,
        batch_size=batch_size,
        shape_overrides=shape_overrides,
        image_size=image_size,
        seed=seed,
    )
    metrics = benchmark_session(
        session,
        feeds,
        warmup_iterations=warmup_iterations,
        measured_iterations=measured_iterations,
        batch_size=batch_size,
        clock=clock,
    )
    memory_after = collect_memory_snapshot()

    active_providers = list(session.get_providers())
    if not active_providers or active_providers[0] != requested_providers[0]:
        raise RuntimeError(
            f"primary Execution Provider mismatch: requested {requested_providers[0]!r}, "
            f"active providers are {active_providers!r}"
        )
    outputs = [
        {"name": item.name, "onnx_type": item.type, "model_shape": list(item.shape)}
        for item in session.get_outputs()
    ]
    before_rss = memory_before["process_rss_bytes"]
    after_rss = memory_after["process_rss_bytes"]
    rss_delta = None if before_rss is None or after_rss is None else after_rss - before_rss

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "model_only_onnxruntime_session_run",
        "model": {
            "path": str(model),
            "size_bytes": model.stat().st_size,
            "sha256": _sha256(model),
        },
        "configuration": {
            "warmup_iterations": warmup_iterations,
            "measured_iterations": measured_iterations,
            "batch_size": batch_size,
            "image_size_for_dynamic_rank4_inputs": image_size,
            "seed": seed,
            "intra_op_threads": intra_op_threads,
            "requested_providers": requested_providers,
        },
        "runtime": {
            "onnxruntime_version": runtime_version,
            "available_providers": available_providers,
            "active_providers": active_providers,
            "provider_options": session.get_provider_options(),
            "model_load_time_ms": load_time_ms,
        },
        "inputs": input_contracts,
        "outputs": outputs,
        "metrics": metrics,
        "memory": {
            "process_rss_before_bytes": before_rss,
            "process_rss_after_bytes": after_rss,
            "process_rss_delta_bytes": rss_delta,
            "native_peak_rss_bytes": None,
        },
        "environment": collect_environment(),
        "limitations": [
            "Synthetic random tensors measure model execution only; they do not validate accuracy.",
            "Preprocessing, decoding, postprocessing, I/O, temperature, and power are not measured.",
            "Process RSS snapshots are not native peak memory measurements.",
        ],
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark an ONNX model with ONNX Runtime")
    parser.add_argument("model", type=Path, help="path to one .onnx model")
    parser.add_argument(
        "--provider",
        action="append",
        dest="providers",
        help="requested Execution Provider; repeat to define priority",
    )
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--image-size", type=int, default=DEFAULT_IMAGE_SIZE)
    parser.add_argument(
        "--shape",
        action="append",
        help="explicit input shape as INPUT=DIM,DIM; repeat for multiple inputs",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--intra-op-threads", type=int, default=0)
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report = run_benchmark(
        args.model,
        providers=args.providers or ["CPUExecutionProvider"],
        batch_size=args.batch_size,
        warmup_iterations=args.warmup,
        measured_iterations=args.iterations,
        shape_overrides=parse_shape_overrides(args.shape),
        image_size=args.image_size,
        seed=args.seed,
        intra_op_threads=args.intra_op_threads,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
