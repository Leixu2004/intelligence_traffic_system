"""集成层的数据入口：读取 Flink 侧的窗口结果与预警级别。

课件第 5 页把第一段定为「Flink → 预测」：实时车流经 Flink 落成窗口结果，集成层读的是
**窗口 + 预警**，不是原始卡口明细。所以这里有两个实现：

  * `DatabaseWindowReader` —— 真机路径，读 `speed_stats`（9/21 窗口作业）联 `traffic_alerts`（9/21 CEP 作业）；
  * `SimulationWindowReader` —— 无集群路径，用本仓库自己的参考实现
    （`backend/flink/window_semantics.py` + `cep_reference.py`）在 `test_data.json` 上复算窗口。

后者始终标 `source="simulation"`，只能用来验证链路连通，不得当作真实路况或集群跑通的证据。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import IntegrationSettings

LOGGER = logging.getLogger(__name__)

try:  # 与 backend/agent/repository.py 同样的可选依赖处理
    import psycopg2
except ImportError:  # pragma: no cover
    psycopg2 = None

TIMEOUT_MS = 2000
# 9/21 交付的确定性测试车流，本地复算的输入；文件在仓库里，不需要集群也不依赖环境变量。
DEFAULT_EVENTS_PATH = Path(__file__).resolve().parents[1] / "flink" / "test_data.json"
# 与 9/21 窗口作业一致：HOP(10 分钟, 5 分钟步长)。改窗口大小时这里的 INTERVAL 与
# SimulationWindowReader 的 size_seconds 要一起改，否则联表会错配时段。
WINDOW_INTERVAL_SQL = "INTERVAL '10' MINUTE"
WINDOW_SIZE_SECONDS = 600


@dataclass(frozen=True)
class WindowRow:
    camera_id: str
    checkpoint_id: str
    window_start: str
    avg_speed: float
    max_speed: float
    vehicle_count: int
    alert_level: str
    duration_min: float
    source: str  # timescaledb / simulation

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


class WindowReader:
    """接口约定：给最近的窗口行，可按相机过滤。"""

    def latest(self, *, limit: int = 20, camera_id: str | None = None) -> list[WindowRow]:
        raise NotImplementedError

    def newest_for_checkpoint(self, checkpoint_id: str) -> WindowRow | None:
        rows = self.latest(limit=50)
        for row in sorted(rows, key=lambda item: item.window_start, reverse=True):
            if row.checkpoint_id == checkpoint_id:
                return row
        return None

    @property
    def source(self) -> str:
        raise NotImplementedError


class DatabaseWindowReader(WindowReader):
    """真机路径：`speed_stats` 左连 `traffic_alerts`（同相机、预警时段与窗口重叠的最严重一条）。

    注意 `speed_stats` 里没有 checkpoint_id 列（9/21 作业的 Sink 只有 camera_id），
    所以 checkpoint_id 回落成 camera_id；预测段按 checkpoint 取数时会因此取不到历史，
    这一点必须体现在 degradations 里而不是被悄悄填平。
    """

    SQL = """
        SELECT s.camera_id,
               to_char(s.window_start AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS'),
               s.avg_speed,
               s.max_speed,
               s.vehicle_count,
               COALESCE(a.alert_level, 'GREEN'),
               COALESCE(a.duration_min, 0)
          FROM speed_stats s
     LEFT JOIN LATERAL (
               SELECT t.alert_level, t.duration_min
                 FROM traffic_alerts t
                WHERE t.camera_id = s.camera_id
                  AND t.start_time <= s.window_start + {interval}
                  AND t.end_time >= s.window_start
                ORDER BY CASE t.alert_level
                             WHEN 'PURPLE' THEN 3 WHEN 'RED' THEN 2
                             WHEN 'AMBER' THEN 1 ELSE 0 END DESC,
                         t.start_time DESC
                   LIMIT 1
           ) a ON TRUE
    """.format(interval=WINDOW_INTERVAL_SQL)

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn.strip()

    @property
    def source(self) -> str:
        return "timescaledb"

    def latest(self, *, limit: int = 20, camera_id: str | None = None) -> list[WindowRow]:
        if not self.dsn:
            return []
        if psycopg2 is None:
            raise RuntimeError("psycopg2 未安装，无法读取 Flink 结果表")
        limit = min(200, max(1, int(limit)))
        sql = self.SQL
        params: tuple[Any, ...]
        if camera_id:
            sql += " WHERE s.camera_id = %s"
            params = (camera_id, limit)
        else:
            params = (limit,)
        sql += " ORDER BY s.window_start DESC LIMIT %s"
        try:
            with psycopg2.connect(self.dsn, connect_timeout=3) as connection:
                connection.set_session(readonly=True, autocommit=True)
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('statement_timeout', %s, false)", (f"{TIMEOUT_MS}ms",))
                    cursor.execute(sql, params)
                    rows = cursor.fetchall()
        except Exception:
            LOGGER.exception("读取 speed_stats/traffic_alerts 失败")
            return []
        return [
            WindowRow(
                camera_id=str(row[0]),
                checkpoint_id=str(row[0]),  # speed_stats 无 checkpoint 列，见类注释
                window_start=str(row[1]),
                avg_speed=float(row[2]) if row[2] is not None else 0.0,
                max_speed=float(row[3]) if row[3] is not None else 0.0,
                vehicle_count=int(row[4] or 0),
                alert_level=str(row[5] or "GREEN"),
                duration_min=float(row[6] or 0.0),
                source=self.source,
            )
            for row in rows
        ]


class SimulationWindowReader(WindowReader):
    """无集群路径：把 `backend/flink/test_data.json` 过一遍本地窗口/CEP 参考实现。

    窗口来自 `window_semantics.aggregate_speed_stats`（10 分钟窗 / 5 分钟步长，与 9/21 作业一致）；
    级别按 `DatabaseWindowReader.SQL` 的同一条规则，从 `cep_reference.expected_alerts` 里取
    「同相机、时段与窗口重叠、最严重的一条」，取不到即 GREEN。
    所以这段与两条 SQL 交付物共用一套规则，可在无集群时验证「Flink→预测」这一段的接线；
    它只证明接线，不证明集群跑通。
    """

    def __init__(
        self,
        events_path: Path,
        *,
        slide_seconds: int = 300,
        size_seconds: int = WINDOW_SIZE_SECONDS,
        min_vehicle_count: int = 1,
    ) -> None:
        self.events_path = events_path
        self.slide_seconds = slide_seconds
        self.size_seconds = size_seconds
        self.min_vehicle_count = min_vehicle_count
        self._rows: list[WindowRow] | None = None

    @property
    def source(self) -> str:
        return "simulation"

    def _build(self) -> list[WindowRow]:
        from backend.flink.cep_reference import expected_alerts, load_events, parse_event_time
        from backend.flink.window_semantics import aggregate_speed_stats

        document = json.loads(self.events_path.read_text(encoding="utf-8"))
        records = document["events"] if isinstance(document, dict) else document
        checkpoints: dict[str, str] = {}
        events: list[dict[str, Any]] = []
        for record in records:
            if record.get("speed_kmh") is None:
                continue  # 与 DEFINE 遇 NULL 不匹配、聚合前剔除的口径一致
            camera_id = str(record.get("camera_id") or "")
            checkpoints.setdefault(camera_id, str(record.get("checkpoint_id") or camera_id))
            events.append(
                {
                    "camera_id": camera_id,
                    "time": parse_event_time(str(record["time"])).isoformat(),
                    "speed_kmh": float(record["speed_kmh"]),
                }
            )
        stats = aggregate_speed_stats(
            events,
            slide_seconds=self.slide_seconds,
            size_seconds=self.size_seconds,
            min_vehicle_count=self.min_vehicle_count,
        )
        observations, _skipped = load_events(self.events_path)
        alerts = expected_alerts(observations)
        rows: list[WindowRow] = []
        for stat in stats:
            row = stat.as_dict()
            window_start = _naive_utc_text(row["window_start"])
            window_end = _naive_utc_text(
                _naive_utc(row["window_start"]) + timedelta(seconds=self.size_seconds)
            )
            alert = _joined_alert(alerts, row["camera_id"], window_start, window_end)
            rows.append(
                WindowRow(
                    camera_id=row["camera_id"],
                    checkpoint_id=checkpoints.get(row["camera_id"], row["camera_id"]),
                    window_start=window_start,
                    avg_speed=float(row["avg_speed"]),
                    max_speed=float(row["max_speed"]),
                    vehicle_count=int(row["vehicle_count"]),
                    alert_level=alert.alert_level if alert else "GREEN",
                    duration_min=round(alert.duration_min, 1) if alert else 0.0,
                    source=self.source,
                )
            )
        rows.sort(key=lambda item: (item.window_start, item.camera_id))
        return rows

    def latest(self, *, limit: int = 20, camera_id: str | None = None) -> list[WindowRow]:
        if self._rows is None:
            self._rows = self._build() if self.events_path.is_file() else []
        rows = self._rows or []
        if camera_id:
            rows = [row for row in rows if row.camera_id == camera_id]
        return sorted(rows, key=lambda item: item.window_start, reverse=True)[:limit]


def _naive_utc(value: Any) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _naive_utc_text(value: Any) -> str:
    """两条读取路径统一成 `to_char(..., 'YYYY-MM-DD"T"HH24:MI:SS')` 的无时区文本。"""
    return _naive_utc(value).isoformat(timespec="seconds")


def _joined_alert(alerts: Sequence[Any], camera_id: str, window_start: str, window_end: str) -> Any | None:
    """复刻 LATERAL 联表：取与窗口时间段重叠、最严重的一条（同级取最新起点）。"""
    from .agent_pipeline import LEVEL_ORDER

    rank = {level: index for index, level in enumerate(LEVEL_ORDER)}
    candidates = [
        alert
        for alert in alerts
        if alert.camera_id == camera_id
        and _naive_utc_text(alert.start_time) <= window_end
        and _naive_utc_text(alert.end_time) >= window_start
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda alert: (rank.get(alert.alert_level, 0), alert.start_time))


def default_window_reader(settings: IntegrationSettings) -> WindowReader:
    """配了 DSN 且装了 psycopg2 才走真库，否则回落到本地复算（`source` 会写明 simulation）。"""
    if settings.timescaledb_dsn and psycopg2 is not None:
        return DatabaseWindowReader(settings.timescaledb_dsn)
    return SimulationWindowReader(DEFAULT_EVENTS_PATH)


def most_severe(rows: Sequence[WindowRow]) -> WindowRow | None:
    from .agent_pipeline import LEVEL_ORDER

    ranked = [row for row in rows if row.alert_level in LEVEL_ORDER]
    if not ranked:
        return None
    return max(ranked, key=lambda row: (LEVEL_ORDER.index(row.alert_level), row.window_start))


def utc_now_text() -> str:
    """UTC 分钟级时间戳，供大屏推送记录标注；`Z` 表示时区，不做本地时间换算。"""
    return _naive_utc_text(datetime.now(timezone.utc)) + "Z"
