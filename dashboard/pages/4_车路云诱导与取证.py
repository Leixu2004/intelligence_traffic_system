"""车路云诱导与取证页：把「AI 问答 / 路径规划 / 车牌识别」三件事聚合成一个可操作界面。

与桌面端 PyQt 演示稿的区别：这里全部复用项目自己的服务与模型接口——
大模型走 backend/agent 的 OpenAI 兼容客户端（百炼 / vLLM 由 .env 切换，不写死厂商），
路线走 backend/crew/routing（高德 v5 优先，失败降级演示走廊并保留 verified 标记），
车牌走 backend/vision/plate_service（YOLO 车牌检测 + 透视校正 + PaddleOCR，而非通用目标检测）。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.agent_client import (
    DEFAULT_API_URL,
    AgentClientError,
    get_agent_health,
    plan_route,
    query_assistant,
)
from dashboard.data_loader import AREA_CENTER, TIANHE_LANDMARKS
from dashboard.nav import render_nav
from dashboard.vision_client import (
    VisionClientError,
    get_plate_health,
    recognize_plate_upload,
)

st.set_page_config(
    page_title="车路云诱导与取证",
    page_icon=":material/navigation:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("车路云诱导与取证")
st.caption("诱导问答 · 路径规划 · 车牌取证｜全部复用项目自身服务，模型端点由 .env 决定")
render_nav()

with st.expander("运行设置", expanded=False):
    api_url = st.text_input("FastAPI 地址", value=DEFAULT_API_URL)

LANDMARKS = {name: (lng, lat) for name, lng, lat in TIANHE_LANDMARKS}
CUSTOM = "自定义坐标"

answer_tab, route_tab, plate_tab = st.tabs(
    [":material/question_answer: 诱导问答", ":material/route: 路径规划", ":material/license: 车牌取证"]
)

# ---------------------------------------------------------------- 诱导问答
with answer_tab:
    try:
        health = get_agent_health(api_url)
    except AgentClientError as exc:
        health = None
        st.error(f"Agent 服务不可用：{exc}")
    else:
        if health.available:
            st.success(f"模型端点：{health.provider} / {health.model}（OpenAI 兼容，可切 vLLM）")
        else:
            st.warning(health.error or "Agent 尚未配置")

    question = st.text_area(
        "问题",
        value="预测今晚天河区交通状况并给出疏导方案",
        height=80,
    )
    if st.button("发送给助手", type="primary", disabled=not (health and health.available)):
        with st.spinner("正在调用工具链"):
            try:
                reply = query_assistant(
                    question=question.strip(),
                    thread_id=f"screen-{api_url}",
                    api_url=api_url,
                )
            except AgentClientError as exc:
                st.error(f"问答失败：{exc}")
            else:
                st.markdown(reply.answer or "（模型未返回内容）")
                if reply.tool_evidence:
                    st.dataframe(
                        [
                            {
                                "工具": item.get("name", ""),
                                "来源": item.get("source", ""),
                                "结果": item.get("summary", ""),
                                "耗时(ms)": item.get("elapsed_ms", 0),
                            }
                            for item in reply.tool_evidence
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                if reply.limitations:
                    st.caption("限制：" + " ".join(reply.limitations))
                st.caption(f"trace_id: {reply.trace_id}")
    st.caption("多轮会话与历史请在「交通指挥助手」页进行。")

# ---------------------------------------------------------------- 路径规划
with route_tab:
    left, right = st.columns([1, 1])
    with left:
        st.subheader("起点 / 终点")
        origin_name = st.selectbox("起点地标", [CUSTOM, *LANDMARKS])
        with st.expander("自定义经纬度（覆盖上面的选择）"):
            origin_text = st.text_input(
                "起点 经度,纬度",
                value=f"{AREA_CENTER[0]},{AREA_CENTER[1]}",
            )
        destination_name = st.selectbox("终点地标", ["（由备选走廊决定）", CUSTOM, *LANDMARKS])
        with st.expander("终点自定义经纬度"):
            destination_text = st.text_input("终点 经度,纬度", value="")
        requested = st.button("规划路线", type="primary")

    def _parse(text: str) -> tuple[float, float] | None:
        try:
            lng, lat = (part.strip() for part in text.split(","))
            return (float(lng), float(lat))
        except (ValueError, AttributeError):
            return None

    with right:
        st.subheader("结果")
        if requested:
            origin = _parse(origin_text) if origin_name == CUSTOM else LANDMARKS[origin_name]
            destination = None
            if destination_name == CUSTOM and destination_text:
                destination = _parse(destination_text)
            elif destination_name not in {CUSTOM, "（由备选走廊决定）"}:
                destination = LANDMARKS[destination_name]
            if not origin:
                st.error("起点经纬度格式应为「经度,纬度」")
            else:
                try:
                    plan = plan_route(origin=origin, destination=destination, api_url=api_url)
                except AgentClientError as exc:
                    st.error(f"路径规划失败：{exc}")
                else:
                    metric_a, metric_b = st.columns(2)
                    metric_a.metric("距离", f"{plan.distance_km:.2f} km")
                    metric_b.metric("预计", f"{plan.eta_minutes:.1f} min")
                    st.markdown(f"**路线**：{plan.name or '未命名'}")
                    if plan.verified:
                        st.success(f"来源 {plan.source}（实时路网，已核验）")
                    else:
                        st.warning(f"来源 {plan.source}（演示拓扑，未经实时核验）")
                    if plan.note:
                        st.caption(plan.note)
                    if len(plan.waypoints) >= 2:
                        # RoutePlan.waypoints 是 [经度, 纬度]，st.map 要 lat/lon 两列。
                        points = pd.DataFrame(plan.waypoints, columns=["lon", "lat"])
                        st.map(points[["lat", "lon"]], zoom=11)

# ---------------------------------------------------------------- 车牌取证
with plate_tab:
    try:
        plate_health = get_plate_health(api_url)
    except VisionClientError as exc:
        plate_health = {}
        st.error(f"车牌取证服务不可用：{exc}")
    else:
        if plate_health.get("available"):
            loaded = "模型已加载" if plate_health.get("model_loaded") else "模型将在首次识别时加载"
            st.success(f"LPR 链路就绪（{loaded}）")
        else:
            st.warning(plate_health.get("error") or "车牌取证不可用")

    upload = st.file_uploader("上传车辆图片", type=["jpg", "jpeg", "png"], key="plate_uploader")
    # Streamlit 的 200MB 是上传组件上限，后端对单张车牌图另有 8MB 限制（health 里带 max_bytes）。
    st.caption(
        f"识别在后端进程完成，单张上限 "
        f"{int(plate_health.get('max_bytes', 8 * 1024 * 1024) // (1024 * 1024))} MB；"
        "结果只作人工核对，不构成识别正确性结论。"
    )
    if upload is not None and st.button("识别车牌", type="primary"):
        payload = upload.getvalue()
        with st.spinner("检测 → 校正 → OCR → 校验"):
            try:
                result = recognize_plate_upload(
                    filename=upload.name, payload=payload, api_url=api_url
                )
            except VisionClientError as exc:
                st.error(f"识别失败：{exc}")
            else:
                plates = result.get("plates") or []
                st.write(
                    f"检出 {result.get('detected_plate_count', 0)} 个车牌框，"
                    f"其中 {result.get('plate_count', 0)} 个识别出有效文本"
                )
                if plates:
                    st.dataframe(
                        pd.json_normalize(plates).rename(
                            columns={
                                "text": "号牌",
                                "ocr_conf": "OCR 置信度",
                                "det_conf": "检测置信度",
                                "is_valid": "通过校验",
                                "plate_type": "类型",
                            }
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
                    try:
                        from PIL import Image, ImageDraw

                        image = Image.open(io.BytesIO(payload)).convert("RGB")
                        draw = ImageDraw.Draw(image)
                        for item in plates:
                            box = item.get("box") or []
                            if len(box) == 4:
                                draw.rectangle(box, outline=(255, 0, 0), width=3)
                        st.image(image, caption="识别框叠加", use_container_width=True)
                    except Exception as exc:  # noqa: BLE001 - PIL 缺失或图片异常都不该让页面崩
                        st.caption(f"叠加图生成失败：{type(exc).__name__}")
                for limitation in result.get("limitations") or []:
                    st.caption(f"限制：{limitation}")
