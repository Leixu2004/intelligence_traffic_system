"""Flink SQL 实时作业子系统（9/21 课件）。

真实运行依赖 Flink Standalone 集群；本包内的纯 Python 代码只负责
交付物一致性校验与窗口语义复算，不宣称等价于集群实测结果。
"""

from .config import FlinkJobSettings, load_flink_settings

__all__ = ["FlinkJobSettings", "load_flink_settings"]
