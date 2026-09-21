"""视频智能解说页：Qwen-VL 多模态分析（9/18 课件），后端为统一 FastAPI 的 /api/v1/vision/*。

诚实口径：verified=false（未配置密钥 / 本地无模型 / 部分帧失败）时页面如实展示降级原因，
不把占位描述包装成模型结论。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.vision_client import (  # noqa: E402
    DEFAULT_API_URL,
    VisionClientError,
    analyze_image_upload,
    analyze_video_upload,
    get_analysis_results,
    get_vision_health,
)

VIDEO_TYPES = ["mp4", "avi", "mkv", "mov", "flv"]
IMAGE_TYPES = ["jpg", "jpeg", "png", "bmp", "webp"]

st.set_page_config(
    page_title="视频智能解说",
    page_icon=":material/videocam:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] { display: none; }
    .block-container { max-width: 1180px; padding-top: 1.4rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

header, nav = st.columns([5, 1])
with header:
    st.title("视频智能解说")
    st.caption("Qwen-VL 多模态：抽帧 → 关键帧筛选 → 逐帧解读 → 解说与告警（9/18 课件）")
with nav:
    st.page_link("app.py", label="流量大屏", icon=":material/monitoring:", use_container_width=True)

with st.expander("运行设置", expanded=False):
    api_url = st.text_input("FastAPI 地址", value=DEFAULT_API_URL)

health: dict = {}
try:
    health = get_vision_health(api_url)
except VisionClientError as exc:
    st.error(f"视觉服务不可用：{exc}")
else:
    if health.get("available"):
        st.success(
            f"后端 `{health.get('backend', '')}` · 模型 `{health.get('model', '')}` 已就绪"
            f"（抽帧间隔 {health.get('frame_interval_seconds', '?')}s，关键帧阈值 {health.get('keyframe_threshold', '?')}）"
        )
    else:
        st.warning(health.get("error") or "多模态服务未启用（配 TRAFFIC_VISION_ENABLED=true 与密钥后重启后端）")

available = bool(health.get("available"))


def render_report(data: dict) -> None:
    """视频与图片共用同构报告（后端已整形），这里只认一种结构。"""
    col_model, col_frames, col_time, col_verify = st.columns(4)
    extraction = data.get("extraction") or {}
    col_model.metric("模型", str(data.get("model") or "—"))
    col_frames.metric(
        "分析帧",
        f"{extraction.get('analyzed_frames', len(data.get('timeline') or []))}"
        f" / {extraction.get('frames_extracted', '?')} 帧",
    )
    col_time.metric("总耗时", f"{round((data.get('elapsed_ms') or 0) / 1000, 1)} s")
    verified = bool(data.get("verified"))
    col_verify.metric("verified", "是" if verified else "否")

    if data.get("accident"):
        st.error("⚠ 检出疑似事故画面，请人工复核（模型判定不作为执法依据）")
    if not verified:
        st.caption("本次结果未达验收口径（占位/部分失败），详见降级说明。")

    narration = str(data.get("narration") or "")
    if narration:
        st.subheader("连续解说")
        st.markdown(narration)

    timeline = data.get("timeline") or []
    if timeline:
        st.subheader("逐帧时间线")
        rows = [
            {
                "时间": str(item.get("time") or ""),
                "状态": str(item.get("status") or ""),
                "车辆数": item.get("vehicles") if item.get("vehicles") is not None else "—",
                "车辆口径": str(item.get("vehicle_source") or ""),
                "描述": str(item.get("description") or item.get("error") or ""),
                "追问回答": str(item.get("answer") or ""),
            }
            for item in timeline
        ]
        st.dataframe(rows, width="stretch", hide_index=True)

    alerts = data.get("alerts") or []
    if alerts:
        st.subheader("告警流水")
        st.dataframe(alerts, width="stretch", hide_index=True)

    degradations = data.get("degradations") or []
    if degradations:
        with st.expander(f"降级说明（{len(degradations)} 条）", expanded=not verified):
            for item in degradations:
                st.markdown(f"- {item}")
    with st.expander("原始结果 JSON", expanded=False):
        st.json(data, expanded=False)


tab_video, tab_image, tab_history = st.tabs(["视频解说", "图片理解 · 追问", "历史结果"])

with tab_video:
    if not available:
        st.info("视觉服务未就绪：配置好密钥并启用后，这里可上传监控视频做连续解说。")
    uploaded_video = st.file_uploader("上传监控视频", type=VIDEO_TYPES, key="video_uploader")
    if st.button("开始分析", icon=":material/play_circle:", disabled=uploaded_video is None or not available, key="run_video"):
        with st.spinner("抽帧 → 关键帧筛选 → 逐帧解读，分钟级耗时，请稍候…"):
            try:
                report = analyze_video_upload(
                    filename=uploaded_video.name,
                    payload=uploaded_video.getvalue(),
                    api_url=api_url,
                )
            except VisionClientError as exc:
                st.error(f"视频分析失败：{exc}")
            else:
                st.session_state["vision_video_report"] = report
    if st.session_state.get("vision_video_report"):
        render_report(st.session_state["vision_video_report"])

with tab_image:
    if not available:
        st.info("视觉服务未就绪：配置好密钥并启用后，这里可做图片描述与视觉问答（VQA）。")
    uploaded_image = st.file_uploader("上传路口图片", type=IMAGE_TYPES, key="image_uploader")
    question = st.text_input("追问（可留空 = 仅图片描述 + 结构化标签）", placeholder="例如：画面里有几辆车？是否存在逆行？")
    if st.button("分析图片", icon=":material/image_search:", disabled=uploaded_image is None or not available, key="run_image"):
        with st.spinner("模型解读中…"):
            try:
                image_report = analyze_image_upload(
                    filename=uploaded_image.name,
                    payload=uploaded_image.getvalue(),
                    question=question.strip(),
                    api_url=api_url,
                )
            except VisionClientError as exc:
                st.error(f"图片分析失败：{exc}")
            else:
                st.session_state["vision_image_report"] = image_report
    if st.session_state.get("vision_image_report"):
        render_report(st.session_state["vision_image_report"])

with tab_history:
    col_alerts, col_limit, col_refresh = st.columns([2, 1, 1], vertical_alignment="bottom")
    alerts_only = col_alerts.toggle("只看告警流水", value=False)
    limit = col_limit.slider("条数", min_value=5, max_value=50, value=20, step=5)
    if col_refresh.button("读取", icon=":material/refresh:") or st.session_state.get("vision_history"):
        try:
            history = get_analysis_results(limit=limit, alerts_only=alerts_only, api_url=api_url)
        except VisionClientError as exc:
            st.error(f"历史结果读取失败：{exc}")
        else:
            st.session_state["vision_history"] = history
    history = st.session_state.get("vision_history") or {}
    items = history.get("items") or []
    if not items:
        st.caption(f"{history.get('source', 'runs.jsonl')} 暂无记录" + ("（后端跑过一次分析后即有）" if not history.get("exists", True) else ""))
    else:
        st.caption(f"来源：{history.get('source', '')}，共 {len(items)} 条（最新在前）")
        for item in items[:limit]:
            with st.expander(f"{item.get('time') or item.get('source', '')} · {item.get('model', '')}"):
                st.json(item, expanded=False)
