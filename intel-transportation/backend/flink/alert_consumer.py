"""预警消费端的升降级与去重（9/21 课件第 7 页的降级规则）。

为什么放在消费端：课件的「连续 5 分钟 speed > 30 自动降一级」要读上一条已推送预警的
级别才能改写，而 sql/alert_level.sql 里的 CASE WHEN 是**无状态**的——它只看单个匹配段的
均速与时长，看不到「现在处于几级」。把它塞进 SQL 只会得到一条永远不成立的分支。
所以本模块承担这部分语义，输入是 traffic_alerts 表按 (camera_id, start_time) 排序的行。

规则（与 README「已知限制」一致）：
  1. 更严重 → 立即推送升级；
  2. 同级且时间上重叠/相邻 → 视为重复，不重复推送（对应课件「预警去重」）；
  3. 连续 RECOVERY_MINUTES 个窗口分支分钟均速 > RECOVERY_SPEED_KMH → 降一级，
     每次只降一级（紫→红→黄→绿），降级后重新计数。

这里不连数据库、不发消息，纯函数便于离线测试；接入推送时把 emit 的记录交给通知模块即可。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .cep_reference import Alert, expected_alerts, load_db_file, load_events
from .config import ALERT_LEVELS

# 级序按严重程度上行，索引即「级别高低」
LEVEL_RANK = {level: rank for rank, level in enumerate(ALERT_LEVELS)}  # GREEN=0 ... PURPLE=3
RECOVERY_MINUTES = 5
RECOVERY_SPEED_KMH = 30.0


@dataclass(frozen=True)
class Push:
    """一条对外推送的预警动作。"""

    camera_id: str
    action: str  # escalate 升级 / downgrade 降级 / recur 同级新一段
    level: str
    previous_level: str
    start_time: datetime
    avg_speed: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "start_time": self.start_time.isoformat(timespec="seconds")}


def downgrade(level: str) -> str:
    """降一级；已经是绿级则保持绿级。"""
    rank = LEVEL_RANK.get(level, 0)
    return ALERT_LEVELS[max(0, rank - 1)]


def _overlaps(candidate: Alert, active: Alert) -> bool:
    return candidate.start_time <= active.end_time and active.start_time <= candidate.end_time


def consume(
    alerts: Iterable[Alert],
    *,
    recovery_minutes: int = RECOVERY_MINUTES,
    recovery_speed_kmh: float = RECOVERY_SPEED_KMH,
) -> list[Push]:
    """把落库的预警序列换算成对外推送动作（含升级、降级、去重）。"""
    grouped: dict[str, list[Alert]] = {}
    for alert in alerts:
        grouped.setdefault(alert.camera_id, []).append(alert)

    pushes: list[Push] = []
    for camera_id in sorted(grouped):
        # 同一起点可能同时有 RED 与 PURPLE 两行，按 (时间, 严重程度倒序) 排，先看最严重的
        series = sorted(grouped[camera_id], key=lambda item: (item.start_time, -LEVEL_RANK[item.alert_level]))
        level = "GREEN"  # 当前已推送到的级别
        active: Alert | None = None  # 触发该级别的那条 CEP 记录，降级判定要拿它做去重锚点
        calm_streak = 0
        for alert in series:
            if alert.source == "window" and active is not None:
                # 恢复观察只看窗口分支的分钟均速：CEP 段负责「持续低速」，
                # 而恢复信号只能来自后续分钟的实测均速。
                calm_streak = calm_streak + 1 if alert.avg_speed > recovery_speed_kmh else 0
                if level != "GREEN" and calm_streak >= recovery_minutes:
                    lower = downgrade(level)
                    pushes.append(
                        Push(
                            camera_id=camera_id,
                            action="downgrade",
                            level=lower,
                            previous_level=level,
                            start_time=alert.end_time,
                            avg_speed=alert.avg_speed,
                            source="recovery",
                        )
                    )
                    level = lower
                    calm_streak = 0
                    if lower == "GREEN":
                        active = None
                continue

            if alert.alert_level == "GREEN":
                continue  # 绿级即正常通行，课件口径里不推送

            if LEVEL_RANK[alert.alert_level] > LEVEL_RANK[level]:
                pushes.append(_escalate(camera_id, level, alert))
                level = alert.alert_level
                active = alert
                calm_streak = 0
                continue

            # 同级且与当前段重叠 = 同一段拥堵的延续，不重复推送
            if alert.alert_level == level and active is not None and _overlaps(alert, active):
                continue
            if alert.alert_level == level and active is not None:
                # 同级但不重叠 = 新的一段拥堵，仍要推送，只是级别没变
                pushes.append(_push(camera_id, "recur", level, level, alert))
            active = alert
    return pushes


def _push(camera_id: str, action: str, level: str, previous_level: str, alert: Alert) -> Push:
    return Push(
        camera_id=camera_id,
        action=action,
        level=level,
        previous_level=previous_level,
        start_time=alert.start_time,
        avg_speed=alert.avg_speed,
        source=alert.source,
    )


def _escalate(camera_id: str, previous_level: str, alert: Alert) -> Push:
    return _push(camera_id, "escalate", alert.alert_level, previous_level, alert)


def main() -> int:
    parser = argparse.ArgumentParser(description="把 CEP 预警序列换算成推送动作（纯离线复算）")
    parser.add_argument("--file", default=str(Path(__file__).resolve().parent / "test_data.json"))
    parser.add_argument("--db", dest="db_dump", default="", help="psql 导出的 traffic_alerts 实际结果，按库中行复算推送动作")
    parser.add_argument("--json", dest="json_out", default="")
    args = parser.parse_args()

    if args.db_dump:
        alerts = sorted(load_db_file(Path(args.db_dump)), key=lambda item: (item.camera_id, item.start_time, item.alert_level))
        origin = f"库中结果 {args.db_dump}"
    else:
        events, _ = load_events(Path(args.file))
        alerts = expected_alerts(events)
        origin = f"本地复算 {args.file}"
    pushes = consume(alerts)
    by_action: dict[str, int] = {}
    for push in pushes:
        by_action[push.action] = by_action.get(push.action, 0) + 1
    print(f"{origin}：预警 {len(alerts)} 条 → 推送动作 {len(pushes)} 条（{by_action}）；模拟数据，非真实路况")
    for push in pushes[:20]:
        print(f"  {push.camera_id} {push.start_time:%H:%M} {push.action:<9} {push.previous_level}→{push.level}")

    if args.json_out:
        payload = {
            "simulation": True,
            "note": f"{origin} → 推送动作复算，未连接推送通道",
            "pushes": [push.to_dict() for push in pushes],
        }
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[写出] {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
