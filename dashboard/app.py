"""Streamlit traffic-monitoring screen with a dark wide-screen layout."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys
import tomllib

import pandas as pd

try:
    import streamlit as st
except ImportError as exc:  # pragma: no cover - useful CLI error before dependencies are installed
    raise SystemExit(
        "未安装大屏依赖，请执行: python -m pip install -r dashboard/requirements.txt"
    ) from exc

try:
    import plotly.express as px
    import plotly.graph_objects as go
except ImportError:  # pragma: no cover - native chart fallbacks remain available
    px = None
    go = None

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:  # pragma: no cover - optional convenience dependency
    st_autorefresh = None


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.data_loader import (  # noqa: E402
    AREA_LABEL,
    aggregate_traffic,
    build_prediction_frame,
    calculate_metrics,
    checkpoint_points,
    filter_traffic,
    load_traffic_data,
)
from dashboard.map_component import render_amap_html  # noqa: E402
from dashboard.nav import render_nav  # noqa: E402


st.set_page_config(
    page_title="智慧交通流量监测大屏",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_data(ttl=60, show_spinner=False)
def cached_bundle(source_mode: str, api_url: str):
    return load_traffic_data(source_mode=source_mode, api_url=api_url)


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --screen-bg: #06111e;
          --panel-bg: #0b1b2b;
          --panel-border: #6f8198;
          --text-muted: #8ea8c0;
          --cyan: #16e4ef;
          --blue: #2e83ff;
          --green: #00e887;
          --orange: #ffb000;
          --red: #ff2f55;
        }
        .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
          background: var(--screen-bg);
        }
        [data-testid="stSidebar"] { display: none; }
        /* 顶部要留够空白：stHeader 是 60px 高的绝对定位条，压着它会盖住标题 */
        .block-container {
          max-width: 1920px;
          padding: 4.6rem 1.6rem 2.4rem;
        }
        .dashboard-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          min-height: 62px;
          padding: .8rem 1.2rem;
          border: 1px solid var(--cyan);
          border-bottom: 3px solid var(--cyan);
          background: linear-gradient(90deg, #0a2034 0%, #0b1727 62%, #09243a 100%);
          box-shadow: 0 0 22px rgba(0, 220, 255, .10);
        }
        .dashboard-title {
          color: var(--cyan);
          font-size: clamp(1.35rem, 2.2vw, 2.05rem);
          font-weight: 750;
          letter-spacing: .04em;
          line-height: 1.5;
        }
        .dashboard-meta {
          display: flex;
          align-items: center;
          gap: 1.1rem;
          color: #c7dced;
          font-size: .88rem;
        }
        .live-badge { color: var(--green); font-weight: 700; letter-spacing: .08em; }
        .live-dot {
          display: inline-block;
          width: 8px;
          height: 8px;
          margin-right: 5px;
          border-radius: 50%;
          background: var(--green);
          box-shadow: 0 0 10px var(--green);
        }
        .screen-status {
          display: flex;
          justify-content: space-between;
          gap: 1rem;
          margin: .6rem 0 .75rem;
          color: var(--text-muted);
          font-size: .78rem;
        }
        .screen-status strong { color: #d9efff; font-weight: 600; }
        .panel-heading {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin: 0 0 .35rem;
        }
        .panel-title {
          color: var(--cyan);
          font-size: 1rem;
          font-weight: 700;
          letter-spacing: .03em;
        }
        .panel-note { color: var(--text-muted); font-size: .72rem; }
        [data-testid="stVerticalBlockBorderWrapper"] {
          min-height: 100%;
          border: 1px solid var(--panel-border);
          border-radius: 0;
          background: var(--panel-bg);
          box-shadow: inset 0 0 0 1px rgba(0, 220, 255, .04);
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div { padding: .8rem .95rem; }
        .kpi-label { color: var(--text-muted); font-size: .84rem; margin-bottom: .36rem; }
        .kpi-value {
          font-size: clamp(1.35rem, 2.3vw, 2.15rem);
          font-weight: 800;
          line-height: 1.05;
        }
        .kpi-sub { margin-top: .32rem; color: #9eb5c9; font-size: .73rem; }
        .kpi-blue .kpi-value { color: var(--blue); }
        .kpi-green .kpi-value { color: var(--green); }
        .kpi-orange .kpi-value { color: var(--orange); }
        .kpi-red .kpi-value { color: var(--red); }
        .screen-footer { margin-top: .8rem; color: #708ba4; text-align: right; font-size: .72rem; }
        div[data-testid="stExpander"] {
          margin: .45rem 0 .7rem;
          border: 1px solid #294865;
          border-radius: 0;
          background: rgba(11, 27, 43, .72);
        }
        div[data-testid="stExpander"] summary { color: #b9d8ee; }
        div.js-plotly-plot .plotly .bg { fill: transparent !important; }
        div.js-plotly-plot .plotly .xtick text,
        div.js-plotly-plot .plotly .ytick text { fill: #90a9bd !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _dashboard_secret(name: str) -> str:
    """Read secrets without placing credentials in source code or logs."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        value = str(st.secrets.get(name, "")).strip()
        if value:
            return value
    except Exception:
        pass

    secrets_path = CURRENT_DIR / ".streamlit" / "secrets.toml"
    try:
        with secrets_path.open("rb") as handle:
            return str(tomllib.load(handle).get(name, "")).strip()
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def _panel_heading(title: str, note: str = "") -> None:
    note_html = f'<span class="panel-note">{note}</span>' if note else ""
    st.markdown(
        f'<div class="panel-heading"><span class="panel-title">{title}</span>{note_html}</div>',
        unsafe_allow_html=True,
    )


def _render_header(title: str) -> None:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    st.markdown(
        f"""
        <div class="dashboard-header">
          <div class="dashboard-title">{title}</div>
          <div class="dashboard-meta">
            <span>{now}</span>
            <span class="live-badge"><span class="live-dot"></span>实时监测</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_runtime_controls() -> tuple[str, str, bool, int]:
    with st.expander("运行设置", expanded=False):
        col_source, col_api, col_refresh, col_interval = st.columns([1, 2.4, 1, 1])
        with col_source:
            source_label = st.selectbox("数据源", ["自动切换", "实时 API", "本地数据"], index=0)
        with col_api:
            api_url = st.text_input(
                "大屏 API 地址",
                value=os.getenv("DASHBOARD_API_URL", "http://127.0.0.1:8000"),
            )
        with col_refresh:
            auto_refresh = st.checkbox("自动刷新", value=True)
        with col_interval:
            refresh_seconds = st.number_input("间隔（秒）", min_value=30, max_value=300, value=60, step=30)
    source_mode = {"自动切换": "auto", "实时 API": "api", "本地数据": "local"}[source_label]
    return source_mode, api_url, auto_refresh, int(refresh_seconds)


def _render_kpis(traffic: pd.DataFrame, detections: pd.DataFrame) -> None:
    metrics = calculate_metrics(traffic, detections)
    buckets = aggregate_traffic(traffic, "15min")
    total_flow = int(buckets["vehicle_count"].sum()) if not buckets.empty else 0
    latest_time = pd.to_datetime(traffic["time"], errors="coerce", utc=True).max() if not traffic.empty else pd.NaT
    if pd.isna(latest_time):
        current_flow = 0
    else:
        recent = traffic[traffic["time"] >= latest_time - pd.Timedelta(minutes=1)]
        current_flow = int(recent["vehicle_count"].sum())
    checkpoint_count = int(traffic["checkpoint_id"].nunique()) if not traffic.empty else 0

    cards = [
        ("累计总流量", f"{total_flow:,}", f"{checkpoint_count} 个监测卡口", "blue"),
        ("实时车流", f"{current_flow:,}", "车辆 / 分钟窗口", "green"),
        ("高峰路口", metrics["hot_checkpoint"], f"平均速度 {metrics['average_speed']:.1f} km/h", "orange"),
        ("异常告警", f"{metrics['alerts']:,}", "超速与识别告警", "red"),
    ]
    cols = st.columns(4, gap="medium")
    for col, (label, value, sub, color) in zip(cols, cards):
        with col:
            with st.container(border=True):
                st.markdown(
                    f'<div class="kpi-{color}"><div class="kpi-label">{label}</div>'
                    f'<div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>',
                    unsafe_allow_html=True,
                )


def _render_flow_chart(traffic: pd.DataFrame, api_url: str) -> None:
    with st.container(border=True):
        _panel_heading("时段流量趋势", "历史流量与短时预测")
        trend = build_prediction_frame(traffic, frequency="15min", steps=4, api_url=api_url)
        if trend.empty:
            st.info("当前筛选条件没有流量记录")
            return
        if go is None:
            st.bar_chart(trend.set_index("time")["vehicle_count"])
            return

        history = trend[trend["kind"].eq("历史")]
        future = trend[trend["kind"].eq("预测")]
        prediction_source = (
            str(future["prediction_source"].iloc[0])
            if not future.empty and "prediction_source" in future.columns
            else "未知"
        )
        prediction_error = (
            str(future["prediction_error"].iloc[0])
            if not future.empty and "prediction_error" in future.columns
            else ""
        )
        if prediction_source == "趋势兜底" and prediction_error:
            st.warning(f"LSTM 当前未启用，图中使用趋势兜底：{prediction_error}")
        elif not future.empty:
            st.caption(f"预测模型：{prediction_source}")
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=history["time"],
                y=history["vehicle_count"],
                name="历史流量",
                marker_color="#1f73c9",
                hovertemplate="%{x|%H:%M}<br>车辆数：%{y}<extra></extra>",
            )
        )
        if not future.empty:
            fig.add_trace(
                go.Scatter(
                    x=future["time"],
                    y=future["vehicle_count"],
                    name=prediction_source,
                    mode="lines+markers",
                    line={"color": "#ffb000", "width": 2, "dash": "dash"},
                    marker={"size": 7},
                    hovertemplate="%{x|%H:%M}<br>预测：%{y}<extra></extra>",
                )
            )
        fig.update_layout(
            height=245,
            margin={"l": 8, "r": 8, "t": 6, "b": 8},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"color": "#9eb5c9", "size": 10},
            showlegend=False,
            bargap=.25,
            xaxis={"showgrid": False, "zeroline": False, "tickformat": "%H:%M"},
            yaxis={"showgrid": True, "gridcolor": "#203a55", "zeroline": False, "title": ""},
        )
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def _render_vehicle_mix(traffic: pd.DataFrame, detections: pd.DataFrame) -> None:
    with st.container(border=True):
        _panel_heading("车型分布", "识别记录")
        if not detections.empty and "vehicle_type" in detections.columns:
            mix = detections["vehicle_type"].replace(
                {"car": "小客车", "truck": "货车", "bus": "客车"}
            ).value_counts()
        else:
            mix = pd.Series({"机动车": max(len(traffic), 1)})
        if px is None:
            st.bar_chart(mix)
            return
        fig = px.pie(
            values=mix.values,
            names=mix.index,
            hole=.64,
            color=mix.index,
            color_discrete_sequence=["#2e83ff", "#00e887", "#ffb000", "#ff2f55"],
        )
        fig.update_layout(
            height=245,
            margin={"l": 2, "r": 2, "t": 2, "b": 2},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"color": "#dcecff", "size": 10},
            legend={"orientation": "h", "y": -.08, "x": .5, "xanchor": "center"},
            showlegend=True,
        )
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def _render_road_heatmap(traffic: pd.DataFrame) -> None:
    with st.container(border=True):
        _panel_heading("路口热力图", "近 1 小时分时流量")
        if traffic.empty or go is None:
            st.info("暂无路口流量数据")
            return
        frame = aggregate_traffic(traffic, "15min")
        if frame.empty:
            st.info("暂无路口流量数据")
            return
        end = frame["time"].max().floor("15min")
        periods = pd.date_range(end=end, periods=4, freq="15min", tz="UTC")
        checkpoints = sorted(frame["checkpoint_id"].astype(str).unique().tolist())
        grid = (
            frame.assign(checkpoint_id=frame["checkpoint_id"].astype(str))
            .pivot_table(index="checkpoint_id", columns="time", values="vehicle_count", aggfunc="sum")
            .reindex(index=checkpoints, columns=periods, fill_value=0)
            .fillna(0)
        )
        max_value = max(float(grid.to_numpy().max()), 1.0)
        fig = go.Figure(
            go.Heatmap(
                z=grid.to_numpy(),
                x=[timestamp.strftime("%H:%M") for timestamp in periods],
                y=[name.replace("CP-", "") for name in checkpoints],
                zmin=0,
                zmax=max_value,
                colorscale=[
                    [0.0, "#00c879"],
                    [0.35, "#00e887"],
                    [0.60, "#ffb000"],
                    [0.82, "#ff6b00"],
                    [1.0, "#ff2f55"],
                ],
                showscale=False,
                xgap=5,
                ygap=5,
                hovertemplate="路口 %{y}<br>%{x}<br>车辆数：%{z}<extra></extra>",
            )
        )
        fig.update_layout(
            height=245,
            margin={"l": 28, "r": 4, "t": 4, "b": 18},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font={"color": "#9eb5c9", "size": 9},
            xaxis={"showgrid": False, "side": "top"},
            yaxis={"showgrid": False, "autorange": "reversed"},
        )
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


def _render_map(traffic: pd.DataFrame, checkpoint: str) -> None:
    with st.container(border=True):
        points = checkpoint_points(filter_traffic(traffic, checkpoint))
        _panel_heading(
            f"实时路况地图 · {AREA_LABEL}",
            f"{len(points)} 个监测卡口｜车流与速度沿用数据源原值，卡口坐标按天河地标演示化落点",
        )
        amap_key = _dashboard_secret("AMAP_KEY")
        security_code = _dashboard_secret("AMAP_SECURITY_CODE")
        if amap_key:
            try:
                import streamlit.components.v1 as components

                components.html(render_amap_html(points, amap_key, security_code), height=405, scrolling=False)
                return
            except Exception as exc:
                st.warning(f"高德地图加载失败，已切换本地地图：{exc}")

        map_df = points.rename(columns={"gps_lat": "lat", "gps_lng": "lon"})[["lat", "lon"]]
        if map_df.empty:
            st.info("暂无可用经纬度数据")
        else:
            st.map(map_df, zoom=12, width="stretch")
            st.caption(
                "未配置 AMAP_KEY，当前使用 Streamlit 本地地图兜底，"
                "因此没有高德实时路况图层（红黄绿路网着色）。"
            )


def _render_evidence(detections: pd.DataFrame) -> None:
    with st.expander("最新识别与违章取证", expanded=False):
        if detections.empty:
            st.info("暂无抓拍记录")
            return
        columns = [
            column
            for column in [
                "time", "camera_id", "plate", "vehicle_type", "violation_type", "confidence"
            ]
            if column in detections.columns
        ]
        st.dataframe(detections[columns].head(20), width="stretch", hide_index=True)
        evidence_dir = PROJECT_ROOT / "intel-transportation" / "data" / "violations"
        images = []
        if "image_path" in detections.columns:
            for raw_path in detections["image_path"].dropna().astype(str):
                candidate = Path(raw_path)
                if candidate.exists() and candidate.is_file():
                    images.append(candidate)
                    break
        if not images and evidence_dir.exists():
            images = sorted(evidence_dir.glob("*.jpg"), key=lambda path: path.stat().st_mtime, reverse=True)
        if images:
            st.image(str(images[0]), caption=f"最新取证：{images[0].name}", width="stretch")


def render_dashboard(page_title: str = "智慧交通流量监测大屏") -> None:
    _inject_styles()
    _render_header(page_title)
    render_nav()
    source_mode, api_url, auto_refresh, refresh_seconds = _render_runtime_controls()
    if auto_refresh and st_autorefresh is not None:
        st_autorefresh(interval=refresh_seconds * 1000, key="traffic-dashboard-refresh")

    bundle = cached_bundle(source_mode, api_url)
    checkpoint_options = ["全部卡口"] + sorted(
        bundle.traffic["checkpoint_id"].dropna().astype(str).unique().tolist()
    )
    with st.expander(f"监测范围 · {AREA_LABEL}", expanded=False):
        checkpoint = st.selectbox("选择监测卡口", checkpoint_options, label_visibility="collapsed")
    traffic = filter_traffic(bundle.traffic, checkpoint)

    source_text = bundle.source
    warning_text = bundle.warning or "数据链路正常"
    st.markdown(
        f'<div class="screen-status"><span>数据状态：<strong>{source_text}</strong></span>'
        f'<span>{warning_text}</span></div>',
        unsafe_allow_html=True,
    )
    _render_kpis(traffic, bundle.detections)

    chart_col, mix_col, heat_col = st.columns([1.35, 1, 1.1], gap="medium")
    with chart_col:
        _render_flow_chart(traffic, api_url)
    with mix_col:
        _render_vehicle_mix(traffic, bundle.detections)
    with heat_col:
        _render_road_heatmap(traffic)

    _render_map(traffic, checkpoint)
    _render_evidence(bundle.detections)
    st.markdown(
        f'<div class="screen-footer">YOLO/OCR 感知 · FastAPI/ONNX 预测 · TimescaleDB/CSV 兜底 · 最近读取 {bundle.loaded_at or "未知"}</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    render_dashboard()
