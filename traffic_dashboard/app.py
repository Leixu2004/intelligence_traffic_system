# -*- coding: utf-8 -*-
import streamlit as st
import yaml
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
import os

# 1. 页面基础配置
st.set_page_config(page_title="交通流量可视化大屏", layout="wide", page_icon="🚦")

# 2. 加载用户配置
config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
with open(config_path, "r", encoding="utf-8") as file:
    config = yaml.load(file, Loader=SafeLoader)

# 3. 初始化认证器
authenticator = stauth.Authenticate(
    config["credentials"],
    config["cookie"]["name"],
    config["cookie"]["key"],
    config["cookie"]["expiry_days"]
)

# 4. 执行登录逻辑
login_result = authenticator.login("main")
if isinstance(login_result, tuple) and len(login_result) == 3:
    name, authentication_status, username = login_result
else:
    name = st.session_state.get("name")
    authentication_status = st.session_state.get("authentication_status")
    username = st.session_state.get("username")

# 5. 鉴权状态处理
if authentication_status:
    authenticator.logout("退出登录", "sidebar")
    st.sidebar.success(f"欢迎回来, {name}!")
    st.sidebar.info("👈 请点击侧边栏【1_🚦交通流量大屏】查看实时大屏")
    
    st.title("🚦 城市交通流量监测可视化平台")
    st.caption("系统采用 Streamlit + 高德 WebGIS + Plotly 架构构建")
    st.markdown("---")
    
    st.success(f"🎉 登录成功！欢迎管理员 **{name}** (账号: `{username}`) 进入系统。")
    
    col_left, col_right = st.columns([3, 2])
    with col_left:
        st.markdown("""
        ### 📋 平台主要功能与导航
        - **核心路网 GIS 地图**：基于高德地图 JS API v2.0 接入实时路况图层、拥堵点与事故标注。
        - **24小时车流趋势**：动态时序面积图，呈现城市主干道早晚高峰双峰波动规律。
        - **实时态势感知指标**：当前拥堵指数、全网平均车速、实时异常事件告警数及通行量统计。
        - **重点路段与车型结构**：深度剖析干道拥堵排行与不同车辆类型配比。
        """)
        
        st.markdown("")
        if st.button("🚀 立即跳转进入【1_🚦交通流量大屏】", type="primary", use_container_width=True):
            st.switch_page("pages/1_🚦交通流量大屏.py")
            
    with col_right:
        st.markdown("""
        ### ⚙️ 环境与接入凭证状态
        """)
        st.info("""
        - **高德地图服务**：Web端 (JS API v2.0)
        - **凭据来源**：本地环境变量或 Streamlit Secrets
        - **页面展示**：不显示 Key 与安全密钥具体值
        - **密码安全**：已启用 Bcrypt 安全加密哈希
        - **默认测试凭据**：`admin` / `admin`
        """)

elif authentication_status is False:
    st.error("❌ 用户名或密码错误，请重新输入")
elif authentication_status is None:
    st.warning("👉 请输入您的账号和密码（默认演示账号: admin / admin）")
