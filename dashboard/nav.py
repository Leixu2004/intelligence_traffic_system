"""页面顶部的四页导航条。

各页都用 CSS 把 stSidebar 整条隐藏掉了（见 app.py 的 `_inject_styles`），
Streamlit 自带的 PAGES 侧边栏导航因此点不到；`st.page_link("app.py")` 在这个版本
（1.64.0）会把链接解析成当前页自己。所以导航只能放在页面内，并且目标要写 pages/ 下的文件名。
"""

from __future__ import annotations

import streamlit as st

# (目标文件, 按钮文案, 图标)
NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("pages/1_交通流量大屏.py", "流量大屏", ":material/monitoring:"),
    ("pages/2_交通指挥助手.py", "交通指挥助手", ":material/smart_toy:"),
    ("pages/3_视频智能解说.py", "视频智能解说", ":material/play_circle:"),
    ("pages/4_车路云诱导与取证.py", "车路云诱导取证", ":material/route:"),
)


def render_nav() -> None:
    """渲染一行右对齐的页签按钮；路径相对 Streamlit 入口脚本（dashboard/app.py）解析。"""
    columns = st.columns([3.0, 1.2, 1.3, 1.2, 1.4], gap="small")
    for column, (target, label, icon) in zip(columns[1:], NAV_ITEMS):
        with column:
            st.page_link(
                target,
                label=label,
                icon=icon,
                use_container_width=True,
            )
