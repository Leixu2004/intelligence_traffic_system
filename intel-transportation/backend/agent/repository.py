"""Bounded, read-only TimescaleDB queries for Agent tools."""

from __future__ import annotations

import logging
from typing import Any

try:
    import psycopg2
except ImportError:  # pragma: no cover - backend requirements include psycopg2
    psycopg2 = None


LOGGER = logging.getLogger(__name__)


class TrafficReadRepository:
    def __init__(self, dsn: str, *, statement_timeout_ms: int = 2000):
        self.dsn = dsn.strip()
        self.statement_timeout_ms = min(10_000, max(250, statement_timeout_ms))

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        if not self.dsn:
            return []
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed")
        with psycopg2.connect(self.dsn, connect_timeout=3) as connection:
            connection.set_session(readonly=True, autocommit=True)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, false)",
                    (f"{self.statement_timeout_ms}ms",),
                )
                cursor.execute(sql, params)
                return cursor.fetchall()

    def traffic_records(
        self, checkpoint_id: str | None = None, limit: int = 300
    ) -> tuple[list[dict[str, Any]], str, str]:
        if not self.dsn:
            return [], "unavailable", "未配置 TIMESCALEDB_DSN"
        limit = min(1000, max(1, int(limit)))
        where = "WHERE checkpoint_id = %s" if checkpoint_id else ""
        params: tuple[Any, ...] = (checkpoint_id, limit) if checkpoint_id else (limit,)
        try:
            rows = self._query(
                f"""
                SELECT bucket, checkpoint_id, total_vehicles,
                       round(avg_speed::numeric, 2)
                FROM checkpoint_traffic_1m
                {where}
                ORDER BY bucket DESC
                LIMIT %s
                """,
                params,
            )
        except Exception:
            LOGGER.exception("Traffic Agent read-only flow query failed")
            return [], "TimescaleDB", "实时流量查询失败"
        records = [
            {
                "time": str(row[0]),
                "checkpoint_id": row[1],
                "vehicle_count": int(row[2]),
                "average_speed": float(row[3]) if row[3] is not None else None,
            }
            for row in rows
        ]
        warning = "" if records else "TimescaleDB 暂无流量记录"
        return records, "TimescaleDB", warning

    def detection_records(
        self, checkpoint_id: str | None = None, limit: int = 300
    ) -> tuple[list[dict[str, Any]], str, str]:
        if not self.dsn:
            return [], "unavailable", "未配置 TIMESCALEDB_DSN"
        limit = min(1000, max(1, int(limit)))
        where = "WHERE checkpoint_id = %s" if checkpoint_id else ""
        params: tuple[Any, ...] = (checkpoint_id, limit) if checkpoint_id else (limit,)
        try:
            rows = self._query(
                f"""
                SELECT time, camera_id, checkpoint_id, plate, vehicle_type,
                       ocr_confidence, image_path
                FROM plate_recognitions
                {where}
                ORDER BY time DESC
                LIMIT %s
                """,
                params,
            )
        except Exception:
            LOGGER.exception("Traffic Agent read-only recognition query failed")
            return [], "TimescaleDB", "实时识别查询失败"
        records = [
            {
                "time": str(row[0]),
                "camera_id": row[1],
                "checkpoint_id": row[2],
                "plate": row[3],
                "vehicle_type": row[4],
                "confidence": float(row[5]) if row[5] is not None else None,
                "image_path": row[6],
            }
            for row in rows
        ]
        warning = "" if records else "TimescaleDB 暂无识别记录"
        return records, "TimescaleDB", warning
