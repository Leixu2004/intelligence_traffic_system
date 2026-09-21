"""Render the optional Gaode WebGIS iframe component."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from .data_loader import AREA_CENTER, AREA_LABEL


TEMPLATE_PATH = Path(__file__).with_name("map_component.html")


def render_amap_html(points: pd.DataFrame, api_key: str, security_code: str) -> str:
    if not api_key:
        raise ValueError("AMAP key is required")

    rows = []
    for row in points.to_dict("records"):
        rows.append(
            {
                "name": str(row.get("checkpoint_id", "卡口")),
                "lng": float(row.get("gps_lng", AREA_CENTER[0])),
                "lat": float(row.get("gps_lat", AREA_CENTER[1])),
                "vehicleCount": int(row.get("vehicle_count", 0) or 0),
                "averageSpeed": round(float(row.get("average_speed", 0) or 0), 1),
            }
        )

    if rows:
        center = [rows[0]["lng"], rows[0]["lat"]]
    else:
        center = [AREA_CENTER[0], AREA_CENTER[1]]

    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    replacements = {
        # The key is embedded in a URL attribute, so URL-encode it instead of
        # serializing it as a JavaScript string (which would add quote marks).
        "__AMAP_KEY__": quote(api_key, safe=""),
        "__AMAP_SECURITY_CODE__": json.dumps(security_code or ""),
        "__MAP_CENTER__": json.dumps(center),
        "__MAP_POINTS__": json.dumps(rows, ensure_ascii=False),
        "__AREA_LABEL__": AREA_LABEL,
    }
    for token, value in replacements.items():
        html = html.replace(token, value)
    return html
