"""Validate ONNX edge artifacts and emit CI-friendly JSON reports.

The checker intentionally keeps static ONNX validation independent from device
runtime validation.  A CPU-only machine can therefore inspect an artifact,
while a requested CUDA/TensorRT provider is reported explicitly instead of
being silently treated as a successful deployment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "edge-artifact-report/v1"
MANIFEST_VERSION = "edge-artifact-manifest/v1"
_PROVIDER_ALIASES = {
    "cpu": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "tensorrt": "TensorrtExecutionProvider",
    "tensorrt_execution_provider": "TensorrtExecutionProvider",
}
_QUANTIZATION_OPS = {
    "QuantizeLinear",
    "DequantizeLinear",
    "DynamicQuantizeLinear",
    "QLinearConv",
    "QLinearMatMul",
    "MatMulInteger",
    "ConvInteger",
}


class ArtifactValidationError(ValueError):
    """Raised for invalid checker arguments or malformed manifests."""


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    """Return a streaming SHA-256 digest for *path*."""

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"ONNX 制品不存在: {resolved}")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dtype_name(elem_type: int, onnx_module: Any) -> str:
    try:
        return str(onnx_module.TensorProto.DataType.Name(elem_type)).lower()
    except (AttributeError, ValueError):
        return f"onnx_type_{elem_type}"


def _shape_value(dim: Any) -> int | str | None:
    if dim.HasField("dim_value"):
        return int(dim.dim_value)
    if dim.HasField("dim_param"):
        return str(dim.dim_param)
    return None


def _value_info(value_info: Any, onnx_module: Any) -> dict[str, Any]:
    tensor_type = value_info.type.tensor_type
    shape = [_shape_value(dim) for dim in tensor_type.shape.dim]
    return {
        "name": value_info.name,
        "dtype": _dtype_name(tensor_type.elem_type, onnx_module),
        "shape": shape,
        "rank": len(shape),
    }


def _normalize_provider(value: str) -> str:
    stripped = value.strip()
    return _PROVIDER_ALIASES.get(stripped.lower(), stripped)


def normalize_providers(providers: str | Iterable[str] | None) -> list[str]:
    """Normalize provider aliases while preserving order and uniqueness."""

    if providers is None:
        values: list[str] = ["CPUExecutionProvider"]
    elif isinstance(providers, str):
        values = [item for item in providers.split(",") if item.strip()]
    else:
        values = []
        for item in providers:
            values.extend(part for part in str(item).split(",") if part.strip())
    normalized = [_normalize_provider(item) for item in values]
    return list(dict.fromkeys(normalized)) or ["CPUExecutionProvider"]


def _load_manifest(manifest: str | Path | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if manifest is None:
        return None
    if isinstance(manifest, Mapping):
        return dict(manifest)
    path = Path(manifest).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"manifest 不存在: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(f"manifest 不是有效 JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError("manifest 顶层必须是 JSON 对象")
    value["_manifest_path"] = str(path)
    return value


def _manifest_entry(manifest: Mapping[str, Any] | None, artifact: Path) -> dict[str, Any] | None:
    if not manifest:
        return None
    entries = manifest.get("artifacts")
    if isinstance(entries, Mapping):
        entries = [dict(value, path=key) if isinstance(value, Mapping) else {"path": key} for key, value in entries.items()]
    if not isinstance(entries, list):
        # A single-artifact manifest may omit the artifacts wrapper.
        if any(key in manifest for key in ("sha256", "inputs", "outputs", "opset")):
            return dict(manifest)
        return None
    candidates = {str(artifact), str(artifact.resolve()), artifact.name}
    for raw in entries:
        if not isinstance(raw, Mapping) or "path" not in raw:
            continue
        raw_path = str(raw["path"])
        if raw_path in candidates or Path(raw_path).name == artifact.name:
            return dict(raw)
    return None


def _manifest_io(entry: Mapping[str, Any], key: str) -> list[dict[str, Any]] | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise ArtifactValidationError(f"manifest {key} 必须是数组")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or "name" not in item:
            raise ArtifactValidationError(f"manifest {key} 的每项必须包含 name")
        result.append(dict(item))
    return result


def _compare_io(actual: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    actual_by_name = {str(item.get("name")): item for item in actual}
    expected_names = {str(item.get("name")) for item in expected}
    actual_names = set(actual_by_name)
    if actual_names != expected_names:
        errors.append(f"I/O 名称不匹配: actual={sorted(actual_names)}, expected={sorted(expected_names)}")
    for item in expected:
        name = str(item.get("name"))
        current = actual_by_name.get(name)
        if current is None:
            continue
        expected_dtype = item.get("dtype", item.get("type"))
        if expected_dtype is not None and str(current.get("dtype")).lower() != str(expected_dtype).lower():
            errors.append(f"{name} dtype 不匹配: actual={current.get('dtype')}, expected={expected_dtype}")
        if "shape" in item and list(current.get("shape", [])) != list(item["shape"]):
            errors.append(f"{name} shape 不匹配: actual={current.get('shape')}, expected={item['shape']}")
    return errors


def _provider_check(path: Path, providers: Sequence[str], require_provider: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requested": list(providers),
        "available": [],
        "selected": [],
        "loaded": False,
        "fallback": False,
        "status": "skipped",
        "error": None,
    }
    try:
        import onnxruntime as ort
    except ImportError as exc:
        result["error"] = f"onnxruntime 未安装，无法验证 Provider: {exc}"
        result["status"] = "error" if require_provider else "unavailable"
        return result
    try:
        available = list(ort.get_available_providers())
        result["available"] = available
        missing = [name for name in providers if name not in available]
        if missing:
            result["error"] = f"请求的 Provider 不可用: {missing}; available={available}"
            result["fallback"] = True
            if require_provider:
                result["status"] = "error"
                return result
        session = ort.InferenceSession(str(path), providers=list(providers))
        selected = list(session.get_providers())
        result["selected"] = selected
        result["loaded"] = True
        result["fallback"] = bool(providers and selected and selected[0] != providers[0]) or bool(missing)
        if require_provider and (not selected or selected[0] != providers[0]):
            result["status"] = "error"
            result["error"] = result["error"] or f"Provider 发生回退: requested={providers}, selected={selected}"
        else:
            result["status"] = "ok" if not result["fallback"] else "warning"
    except Exception as exc:  # Runtime/provider errors vary by platform.
        result["error"] = f"ONNX Runtime 加载失败: {type(exc).__name__}: {exc}"
        result["status"] = "error" if require_provider else "unavailable"
    return result


def validate_artifact(
    path: str | Path,
    *,
    manifest: str | Path | Mapping[str, Any] | None = None,
    providers: str | Iterable[str] | None = None,
    require_provider: bool = False,
    expected_opset: int | None = None,
) -> dict[str, Any]:
    """Validate one ONNX artifact and return a JSON-serializable report."""

    artifact = Path(path).expanduser().resolve()
    report: dict[str, Any] = {
        "path": str(artifact),
        "exists": artifact.is_file(),
        "size_bytes": artifact.stat().st_size if artifact.is_file() else None,
        "sha256": None,
        "checks": {},
        "errors": [],
        "warnings": [],
        "ok": False,
    }
    if not artifact.is_file():
        report["errors"].append(f"ONNX 制品不存在: {artifact}")
        return report

    report["sha256"] = sha256_file(artifact)
    try:
        import onnx
    except ImportError as exc:
        report["errors"].append(f"onnx 未安装，无法执行结构检查: {exc}")
        return report

    try:
        model = onnx.load(str(artifact))
        onnx.checker.check_model(model)
        report["checks"]["onnx_checker"] = {"status": "ok"}
    except Exception as exc:
        report["checks"]["onnx_checker"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        report["errors"].append(f"ONNX checker 失败: {type(exc).__name__}: {exc}")
        return report

    opsets = {entry.domain or "ai.onnx": int(entry.version) for entry in model.opset_import}
    default_opset = opsets.get("ai.onnx")
    report["opset"] = {"default": default_opset, "imports": opsets}
    report["checks"]["opset"] = {"status": "ok", "value": default_opset}
    manifest_obj = _load_manifest(manifest)
    entry = _manifest_entry(manifest_obj, artifact)
    if expected_opset is None and entry is not None and entry.get("opset") is not None:
        expected_opset = int(entry["opset"])
    if expected_opset is not None and default_opset != expected_opset:
        message = f"opset 不匹配: actual={default_opset}, expected={expected_opset}"
        report["checks"]["opset"] = {"status": "error", "value": default_opset, "expected": expected_opset}
        report["errors"].append(message)

    inputs = [_value_info(value, onnx) for value in model.graph.input if value.name not in {init.name for init in model.graph.initializer}]
    outputs = [_value_info(value, onnx) for value in model.graph.output]
    report["inputs"] = inputs
    report["outputs"] = outputs
    io_errors: list[str] = []
    if entry is not None:
        expected_inputs = _manifest_io(entry, "inputs")
        expected_outputs = _manifest_io(entry, "outputs")
        if expected_inputs is not None:
            io_errors.extend(f"输入 {error}" for error in _compare_io(inputs, expected_inputs))
        if expected_outputs is not None:
            io_errors.extend(f"输出 {error}" for error in _compare_io(outputs, expected_outputs))
    report["checks"]["io"] = {"status": "error" if io_errors else "ok", "errors": io_errors}
    report["errors"].extend(io_errors)

    quant_ops = sorted({node.op_type for node in model.graph.node if node.op_type in _QUANTIZATION_OPS or "Quant" in node.op_type})
    report["quantization"] = {"detected": bool(quant_ops), "node_types": quant_ops, "node_count": sum(1 for node in model.graph.node if node.op_type in quant_ops)}
    report["checks"]["quantization"] = {"status": "ok", "detected": bool(quant_ops)}

    if entry is not None and entry.get("sha256"):
        expected_hash = str(entry["sha256"]).lower()
        if report["sha256"].lower() != expected_hash:
            report["checks"]["hash"] = {"status": "error", "actual": report["sha256"], "expected": expected_hash}
            report["errors"].append(f"SHA-256 不匹配: actual={report['sha256']}, expected={expected_hash}")
        else:
            report["checks"]["hash"] = {"status": "ok", "value": report["sha256"]}
    else:
        report["checks"]["hash"] = {"status": "ok", "value": report["sha256"], "expected": False}

    normalized_providers = normalize_providers(providers)
    provider_report = _provider_check(artifact, normalized_providers, require_provider)
    report["provider"] = provider_report
    if provider_report["status"] == "error":
        report["errors"].append(provider_report["error"])
    elif provider_report["status"] in {"unavailable", "warning"} and provider_report["error"]:
        report["warnings"].append(provider_report["error"])
    report["ok"] = not report["errors"]
    return report


def create_manifest(paths: Iterable[str | Path], reports: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Create a portable manifest from validated or directly inspected artifacts."""

    report_by_path = {str(Path(item["path"]).resolve()): item for item in (reports or []) if item.get("path")}
    artifacts: list[dict[str, Any]] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        report = report_by_path.get(str(path))
        if report is None:
            report = validate_artifact(path, providers=["CPUExecutionProvider"])
        item: dict[str, Any] = {
            "path": path.name,
            "sha256": report.get("sha256"),
            "size_bytes": report.get("size_bytes"),
            "opset": report.get("opset", {}).get("default"),
            "inputs": report.get("inputs", []),
            "outputs": report.get("outputs", []),
            "quantization": report.get("quantization", {}),
        }
        artifacts.append(item)
    return {"manifest_version": MANIFEST_VERSION, "artifacts": artifacts}


def validate_artifacts(
    paths: Iterable[str | Path],
    *,
    manifest: str | Path | Mapping[str, Any] | None = None,
    providers: str | Iterable[str] | None = None,
    require_provider: bool = False,
    expected_opset: int | None = None,
) -> dict[str, Any]:
    """Validate multiple artifacts and return a machine-readable report."""

    reports = [validate_artifact(path, manifest=manifest, providers=providers, require_provider=require_provider, expected_opset=expected_opset) for path in paths]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifacts": reports,
        "summary": {
            "total": len(reports),
            "passed": sum(bool(item.get("ok")) for item in reports),
            "failed": sum(not bool(item.get("ok")) for item in reports),
        },
        "ok": all(bool(item.get("ok")) for item in reports),
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验 ONNX 边缘制品并输出机器可读 JSON")
    parser.add_argument("artifacts", nargs="+", help="一个或多个 ONNX 文件")
    parser.add_argument("--manifest", help="期望 SHA-256、opset 和 I/O 契约的 JSON manifest")
    parser.add_argument("--providers", nargs="+", default=["CPUExecutionProvider"], help="Execution Provider 列表，支持 cpu/cuda/tensorrt 别名")
    parser.add_argument("--require-provider", action="store_true", help="Provider 不可用或发生回退时令校验失败")
    parser.add_argument("--expected-opset", type=int, help="所有制品要求的默认 ONNX opset")
    parser.add_argument("--write-manifest", help="将当前结构和哈希写入该 JSON 文件")
    parser.add_argument("--json-output", help="将校验报告写入该 JSON 文件")
    parser.add_argument("--pretty", action="store_true", help="格式化 JSON")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report = validate_artifacts(args.artifacts, manifest=args.manifest, providers=args.providers, require_provider=args.require_provider, expected_opset=args.expected_opset)
    if args.write_manifest:
        manifest = create_manifest(args.artifacts, report["artifacts"])
        Path(args.write_manifest).expanduser().resolve().write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["manifest_path"] = str(Path(args.write_manifest).expanduser().resolve())
    rendered = json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=not args.pretty)
    if args.json_output:
        Path(args.json_output).expanduser().resolve().write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
