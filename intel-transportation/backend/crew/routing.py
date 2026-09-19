"""绕行路线规划：优先真实高德路径 API，失败或无密钥时降级到演示拓扑。

两条路径的产物都用 source / verified 显式区分，避免把演示数据当成核验结果。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

AMAP_DRIVING_URL = "https://restapi.amap.com/v5/direction/driving"
STATIC_AVERAGE_SPEED_KMH = 55.0


@dataclass(frozen=True)
class Corridor:
    corridor_id: str
    name: str
    waypoints: tuple[tuple[float, float], ...]
    note: str = ""
    declared_distance_km: float | None = None
    declared_eta_minutes: float | None = None


@dataclass(frozen=True)
class RoutePlan:
    ok: bool
    source: str
    verified: bool
    name: str = ""
    distance_km: float = 0.0
    eta_minutes: float = 0.0
    waypoints: list[list[float]] = field(default_factory=list)
    note: str = ""
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "verified": self.verified,
            "name": self.name,
            "distance_km": round(self.distance_km, 2),
            "eta_minutes": round(self.eta_minutes, 1),
            "waypoints": self.waypoints,
            "note": self.note,
            "error": self.error,
        }


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lng1, lat1 = a
    lng2, lat2 = b
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    chord = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(chord))


def _polyline_length(points: list[tuple[float, float]]) -> float:
    return sum(haversine_km(points[i], points[i + 1]) for i in range(len(points) - 1))


class RoutePlanner:
    def __init__(
        self,
        corridors: list[Corridor] | None = None,
        *,
        mode: str = "auto",
        amap_key: str = "",
        timeout_seconds: float = 8.0,
    ):
        self.corridors = corridors or []
        self.mode = mode
        self.amap_key = amap_key
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_config(cls, config: dict[str, Any], *, mode: str, amap_key: str, timeout_seconds: float) -> RoutePlanner:
        corridors: list[Corridor] = []
        raw = (config.get("route_fallback") or {}).get("corridors") or []
        for item in raw:
            points = [tuple(map(float, pair)) for pair in (item.get("waypoints") or [])]
            if len(points) < 2:
                continue
            corridors.append(
                Corridor(
                    corridor_id=str(item.get("id") or points[0][0]),
                    name=str(item.get("name") or "未命名走廊"),
                    waypoints=tuple(points),
                    note=str(item.get("note") or ""),
                    declared_distance_km=_optional_float(item.get("distance_km")),
                    declared_eta_minutes=_optional_float(item.get("eta_minutes")),
                )
            )
        return cls(corridors, mode=mode, amap_key=amap_key, timeout_seconds=timeout_seconds)

    def plan(
        self,
        origin: tuple[float, float] | None,
        destination: tuple[float, float] | None = None,
    ) -> RoutePlan:
        if self.mode in {"amap", "auto"} and self.amap_key and origin and destination:
            amap = self._plan_amap(origin, destination)
            if amap.ok or self.mode == "amap":
                return amap
            static = self._plan_static(origin)
            return replace(static, note=f"高德调用失败（{amap.error}）；{static.note}")
        if origin is None and not self.corridors:
            return RoutePlan(False, "none", False, error="缺少起终点坐标且无可用演示走廊")
        return self._plan_static(origin)

    def _plan_amap(self, origin: tuple[float, float], destination: tuple[float, float]) -> RoutePlan:
        try:
            import requests
        except ImportError:
            return RoutePlan(False, "amap_v5", False, error="requests 未安装")
        params = {
            "key": self.amap_key,
            "origin": f"{origin[0]:.6f},{origin[1]:.6f}",
            "destination": f"{destination[0]:.6f},{destination[1]:.6f}",
            "show_cost": "true",
        }
        try:
            response = requests.get(AMAP_DRIVING_URL, params=params, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # 网络与解析异常一律降级，不让调度链路中断
            return RoutePlan(False, "amap_v5", False, error=f"{type(exc).__name__}: {exc}")
        paths = ((payload.get("route") or {}).get("paths") or [])
        if str(payload.get("status")) != "1" or not paths:
            return RoutePlan(
                False,
                "amap_v5",
                False,
                error=str(payload.get("info") or payload.get("infocode") or "无路径结果"),
            )
        first = paths[0]
        distance = _float_or(first.get("distance"), 0.0) / 1000.0
        duration = _float_or(first.get("duration"), 0.0) / 60.0
        steps = first.get("steps") or []
        advice = "; ".join(str(step.get("instruction") or "") for step in steps[:6] if step.get("instruction"))
        return RoutePlan(
            ok=True,
            source="amap_v5",
            verified=True,
            name=str(first.get("path") or first.get("title") or "高德驾车路径"),
            distance_km=distance,
            eta_minutes=duration,
            waypoints=[list(origin), list(destination)],
            note=advice[:400] or "高德未返回分段指引",
        )

    def _plan_static(self, origin: tuple[float, float] | None) -> RoutePlan:
        if not self.corridors:
            return RoutePlan(False, "demonstration_topology", False, error="配置中没有可用演示走廊")
        usable = [corridor for corridor in self.corridors if len(corridor.waypoints) >= 2]
        if not usable:
            return RoutePlan(False, "demonstration_topology", False, error="演示走廊坐标点不足，无法给出路线")
        if origin is None:
            chosen = usable[0]
        else:
            chosen = min(usable, key=lambda c: haversine_km(origin, c.waypoints[0]))
        points = [tuple(pair) for pair in chosen.waypoints]
        distance = chosen.declared_distance_km or _polyline_length(points)
        eta = chosen.declared_eta_minutes or distance / STATIC_AVERAGE_SPEED_KMH * 60.0
        return RoutePlan(
            ok=True,
            source="demonstration_topology",
            verified=False,
            name=chosen.name,
            distance_km=distance,
            eta_minutes=eta,
            waypoints=[[lng, lat] for lng, lat in points],
            note=(chosen.note + "；演示拓扑，里程与耗时未经真实路网核验").strip("；"),
        )


def _optional_float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
