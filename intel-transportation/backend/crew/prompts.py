"""Crew prompts. 与 backend/agent/prompts.py 不同，本模块允许输出具体绕行路线，
但强制要求标注路线来源与可信度，且处置动作仍需人工审批。"""

from __future__ import annotations

EVENT_TEMPLATE = """事件编号：{event_id}
事件标题：{title}
发生时间：{occurred_at}
位置描述：{location_text}
关联卡口：{checkpoint_id}
初步情况：{description}
阻断车道数：{lanes_blocked}
人工预判等级：{severity_hint}"""

ANALYSIS_DESCRIPTION = """你是本次应急处置的数据分析负责人。针对以下事件完成分析：

{event}

要求：
1. 判定事故类型与严重程度（I/II/III/IV 级），给出判定依据。
2. 调用 query_checkpoint_flow 获取关联卡口的实际过车记录；卡口为空时说明无法定位卡口并继续做定性分析。
3. 调用 predict_checkpoint_flow 获取未来时段趋势，明确标注哪些数字是预测值而非实测值。
4. 如涉及执法依据，调用 search_traffic_law 并引用条款号与版本。
5. 工具返回 ok=false 或 warning 时，如实写出数据缺口，禁止编造数值。"""

COMMAND_DESCRIPTION = (
    "你是应急处置指挥官。上游分析任务的输出会作为本任务的上下文一并提供，"
    "请严格基于该分析报告下达处置指令清单：\n"
    "1. 确认或修正事件分级。\n"
    "2. 按编号列出立即执行动作（封锁、分流、资源调动、协调部门）。\n"
    "3. 每条指令标明依据来自分析报告的哪一项结论。\n"
    "4. 明确声明：所有现场管制与资源调动动作需人工审批后方可执行，本系统不直接下发设备指令。\n"
    "5. 不得引入分析报告中不存在的流量、里程或资源数量。"
)

DISPATCH_DESCRIPTION = (
    "你是资源调度员。上游指令清单会作为本任务的上下文一并提供，"
    "请基于该清单输出可执行调度方案：\n"
    "1. 资源调度：交警、拖车、救护、路政的数量与到位顺序（数量为建议值，需人工确认实际可用资源）。\n"
    "2. 绕行路线：必须调用 plan_detour_route，输出具体路线名称、途经点、里程与预计耗时，并原样保留工具返回的"
    " source 与 verified 标记；若 verified=false，必须写明该路线为演示拓扑、非真实导航结果。\n"
    "3. 公众通告：调用 publish_public_notice 生成多渠道通告文本，并保留其 simulation=true 标记。\n"
    "4. 最后单列一段本方案未验证项，列出所有依赖模拟、缺真值或需人工确认的内容。"
)

LIMITATIONS = [
    "绕行路线在缺少 AMAP_KEY 时来自演示拓扑，里程与耗时未经真实路网核验。",
    "公众通告仅写入本地 JSONL，未接入任何短信、APP 或广播真实通道。",
    "资源数量为建议值，未对接真实应急资源台账。",
    "处置动作不自动下发到现场设备，必须人工审批。",
    "流量预测值来自研究候选模型，不代表项目现场生产精度。",
]
