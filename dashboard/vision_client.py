"""HTTP client for the Qwen-VL multimodal vision API (9/18 courseware) and the
single-image plate forensics endpoint."""

from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_API_URL = os.getenv("DASHBOARD_API_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("PREDICTION_API_KEY", "").strip()

# 视频要逐帧过模型，超时给到分钟级；图片/历史轻量。
VIDEO_TIMEOUT_SECONDS = float(os.getenv("VISION_CLIENT_VIDEO_TIMEOUT", "300"))
IMAGE_TIMEOUT_SECONDS = float(os.getenv("VISION_CLIENT_IMAGE_TIMEOUT", "120"))


class VisionClientError(RuntimeError):
    """Stable dashboard-facing error for vision API failures."""


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


def _detail_or(response: requests.Response, fallback: str) -> str:
    try:
        return str(response.json().get("detail") or fallback)
    except ValueError:
        return fallback


def _get_json(url: str, *, timeout: float, error_message: str) -> dict[str, Any]:
    try:
        response = requests.get(url, headers=_headers(), timeout=timeout)
    except requests.RequestException as exc:
        raise VisionClientError(error_message) from exc
    if response.status_code >= 400:
        raise VisionClientError(_detail_or(response, f"HTTP {response.status_code}"))
    try:
        return dict(response.json().get("data") or {})
    except ValueError as exc:
        raise VisionClientError("视觉 API 返回了无效 JSON") from exc


def get_vision_health(api_url: str = DEFAULT_API_URL, timeout: float = 5.0) -> dict[str, Any]:
    return _get_json(
        f"{api_url.rstrip('/')}/api/v1/vision/health",
        timeout=timeout,
        error_message="无法读取视觉服务健康状态",
    )


def _post_upload(
    *,
    endpoint: str,
    filename: str,
    payload: bytes,
    api_url: str,
    timeout: float,
    extra_form: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = requests.post(
            f"{api_url.rstrip('/')}/api/v1/vision/{endpoint}",
            files={"file": (filename, payload)},
            data=extra_form or {},
            headers=_headers(),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise VisionClientError(f"无法连接视觉 {endpoint} API") from exc
    if response.status_code >= 400:
        raise VisionClientError(_detail_or(response, f"视觉 API 返回 HTTP {response.status_code}"))
    try:
        return dict(response.json().get("data") or {})
    except ValueError as exc:
        raise VisionClientError("视觉 API 返回了无效 JSON") from exc


def analyze_video_upload(
    *,
    filename: str,
    payload: bytes,
    api_url: str = DEFAULT_API_URL,
    timeout: float = VIDEO_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    return _post_upload(
        endpoint="video",
        filename=filename,
        payload=payload,
        api_url=api_url,
        timeout=timeout,
    )


def analyze_image_upload(
    *,
    filename: str,
    payload: bytes,
    question: str = "",
    api_url: str = DEFAULT_API_URL,
    timeout: float = IMAGE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    return _post_upload(
        endpoint="analyze",
        filename=filename,
        payload=payload,
        api_url=api_url,
        timeout=timeout,
        extra_form={"question": question},
    )


def get_analysis_results(
    *,
    limit: int = 20,
    alerts_only: bool = False,
    api_url: str = DEFAULT_API_URL,
    timeout: float = 15.0,
) -> dict[str, Any]:
    return _get_json(
        f"{api_url.rstrip('/')}/api/v1/vision/results",
        timeout=timeout,
        error_message="无法读取历史分析结果",
    )


def get_plate_health(api_url: str = DEFAULT_API_URL, timeout: float = 5.0) -> dict[str, Any]:
    """车牌取证链路状态（available/model_loaded/error）。"""
    return _get_json(
        f"{api_url.rstrip('/')}/api/v1/vision/plate/health",
        timeout=timeout,
        error_message="无法读取车牌取证服务状态",
    )


def recognize_plate_upload(
    *,
    filename: str,
    payload: bytes,
    api_url: str = DEFAULT_API_URL,
    timeout: float = IMAGE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """单图车牌取证：走项目自己的 LPR 流水线，不调用多模态大模型。"""
    return _post_upload(
        endpoint="plate",
        filename=filename,
        payload=payload,
        api_url=api_url,
        timeout=timeout,
    )
