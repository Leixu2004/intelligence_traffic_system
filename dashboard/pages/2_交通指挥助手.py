"""Traffic command assistant page backed by the unified FastAPI service."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.agent_client import (
    DEFAULT_API_URL,
    AgentClientError,
    get_agent_health,
    query_assistant,
)

st.set_page_config(
    page_title="交通指挥助手",
    page_icon=":material/smart_toy:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    [data-testid="stSidebar"] { display: none; }
    .block-container { max-width: 1180px; padding-top: 1.4rem; }
    [data-testid="stChatMessage"] { border-radius: 6px; }
    </style>
    """,
    unsafe_allow_html=True,
)

header, nav = st.columns([5, 1])
with header:
    st.title("交通指挥助手")
with nav:
    st.page_link(
        "app.py",
        label="流量大屏",
        icon=":material/monitoring:",
        use_container_width=True,
    )

if "traffic_agent_thread_id" not in st.session_state:
    st.session_state.traffic_agent_thread_id = f"dashboard-{uuid4().hex}"
if "traffic_agent_messages" not in st.session_state:
    st.session_state.traffic_agent_messages = []

with st.expander("运行设置", expanded=False):
    api_url = st.text_input("FastAPI 地址", value=DEFAULT_API_URL)
    checkpoint_id = st.text_input("默认卡口", value="")
    if st.button("新建会话", icon=":material/refresh:"):
        st.session_state.traffic_agent_thread_id = f"dashboard-{uuid4().hex}"
        st.session_state.traffic_agent_messages = []
        st.rerun()

try:
    health = get_agent_health(api_url)
except AgentClientError as exc:
    health = None
    st.error(f"Agent 服务不可用：{exc}")
else:
    if health.available:
        st.success(f"{health.provider} / {health.model} 已就绪")
    else:
        st.warning(health.error or "Agent 尚未配置")

for message in st.session_state.traffic_agent_messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("evidence"):
            with st.expander("工具证据", expanded=False):
                st.dataframe(message["evidence"], use_container_width=True, hide_index=True)
        if message.get("limitations"):
            st.caption("限制：" + " ".join(message["limitations"]))

question = st.chat_input("输入交通数据或预测问题", disabled=not (health and health.available))
if question:
    st.session_state.traffic_agent_messages.append(
        {"role": "user", "content": question}
    )
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("正在查询项目数据"):
        try:
            reply = query_assistant(
                question=question,
                thread_id=st.session_state.traffic_agent_thread_id,
                checkpoint_id=checkpoint_id.strip() or None,
                api_url=api_url,
            )
        except AgentClientError as exc:
            st.error(f"问答失败：{exc}")
        else:
            st.markdown(reply.answer)
            evidence = [
                {
                    "工具": item.get("name", ""),
                    "来源": item.get("source", ""),
                    "结果": item.get("summary", ""),
                    "耗时(ms)": item.get("elapsed_ms", 0),
                }
                for item in reply.tool_evidence
            ]
            if evidence:
                with st.expander("工具证据", expanded=False):
                    st.dataframe(evidence, use_container_width=True, hide_index=True)
            if reply.limitations:
                st.caption("限制：" + " ".join(reply.limitations))
            st.caption(f"trace_id: {reply.trace_id}")
            st.session_state.traffic_agent_messages.append(
                {
                    "role": "assistant",
                    "content": reply.answer,
                    "evidence": evidence,
                    "limitations": reply.limitations,
                }
            )
