"""Download, verify, and standardize KDD Cup 2017 China tollgate flow data."""

from __future__ import annotations

import argparse
import base64
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

from .holiday_calendar import (
    CHINA_2016_NOTICE_URL,
    KDD2017_CALENDAR_VALID_FROM,
    KDD2017_CALENDAR_VALID_TO,
    china_calendar_flags,
)


GITHUB_REPOSITORY = "https://github.com/Engineering-Course/kddcup2017"
KDD_ANNOUNCEMENT_URL = (
    "https://www.kdd.org/kdd2017/News/view/"
    "announcing-kdd-cup-2017-highway-tollgates-traffic-flow-prediction"
)
TIANCHI_COMPETITION_URL = (
    "https://tianchi.aliyun.com/competition/entrance/231597/information"
)
TIANCHI_AGREEMENT_URL = "https://tianchi.aliyun.com/competition/agreement/231597"
RAW_BLOB_SHA = "d5d82fa4488a100338f20af3de16df88dcbdae47"
AGGREGATE_BLOB_SHA = "4f11ebc9f093d075bb3a2e0d71143cbcc7e0056e"
RAW_SIZE = 22_504_443
AGGREGATE_SIZE = 573_869
RAW_SHA256 = "7c824059114e0a6af9c3d89ffe856032a8510e3677a970654e7a694a1be99b62"
AGGREGATE_SHA256 = "f3311aef04ca4254f1497f426eedf54135c47438ba25784d8d55d501246a1727"
BIN_SECONDS = 1_200


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_github_blob(blob_sha: str, destination: Path) -> None:
    request = Request(
        f"https://api.github.com/repos/Engineering-Course/kddcup2017/git/blobs/{blob_sha}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "intel-transportation-kdd2017-preparer",
        },
    )
    with urlopen(request, timeout=120) as response:  # noqa: S310 - fixed trusted URL
        payload = json.load(response)
    destination.write_bytes(base64.b64decode(payload["content"]))


def _ensure_source_file(
    destination: Path,
    *,
    blob_sha: str,
    expected_size: int,
    expected_sha256: str,
) -> None:
    if not destination.is_file() or destination.stat().st_size != expected_size:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _download_github_blob(blob_sha, destination)
    actual_hash = _sha256(destination)
    if destination.stat().st_size != expected_size or actual_hash != expected_sha256:
        raise ValueError(
            f"源文件完整性校验失败: {destination}，"
            f"size={destination.stat().st_size}, sha256={actual_hash}"
        )


def _aggregate_raw_events(raw_path: Path) -> pd.DataFrame:
    frame = pd.read_csv(raw_path, usecols=["time", "tollgate_id", "direction"])
    frame["timestamp"] = pd.to_datetime(frame["time"], errors="raise")
    frame["timestamp"] = frame["timestamp"].dt.floor("20min")
    return (
        frame.groupby(["tollgate_id", "direction", "timestamp"], as_index=False)
        .size()
        .rename(columns={"size": "vehicle_count"})
        .sort_values(["timestamp", "tollgate_id", "direction"], kind="stable")
        .reset_index(drop=True)
    )


def _verify_mirror(nonzero_aggregate: pd.DataFrame, mirror_path: Path) -> None:
    mirror = pd.read_csv(mirror_path)
    mirror["timestamp"] = pd.to_datetime(mirror["time_window"].str.slice(1, 20))
    comparable = mirror[["tollgate_id", "direction", "timestamp", "volume"]].rename(
        columns={"volume": "vehicle_count"}
    )
    columns = ["tollgate_id", "direction", "timestamp", "vehicle_count"]
    left = nonzero_aggregate[columns].sort_values(columns[:3]).reset_index(drop=True)
    right = comparable[columns].sort_values(columns[:3]).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(left, right, check_dtype=False)
    except AssertionError as exc:
        raise ValueError("自行聚合结果与公开镜像的 20 分钟聚合文件不一致") from exc


def _attach_calendar(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    result = frame.copy()
    local_dates = result["timestamp"].dt.date
    calendar_rows = []
    current = KDD2017_CALENDAR_VALID_FROM
    while current <= KDD2017_CALENDAR_VALID_TO:
        flags = china_calendar_flags(current)
        calendar_rows.append({"date": current.isoformat(), **flags})
        current += timedelta(days=1)
    calendar = pd.DataFrame(calendar_rows)
    flags_by_date = calendar.set_index("date")
    for column in ("is_holiday", "is_weekend", "is_makeup_workday", "is_workday", "day_type"):
        result[column] = [flags_by_date.loc[value.isoformat(), column] for value in local_dates]
    result["series_id"] = [
        f"kdd2017_t{tollgate}_d{direction}"
        for tollgate, direction in zip(
            result["tollgate_id"], result["direction"], strict=True
        )
    ]
    result["bin_seconds"] = BIN_SECONDS
    result["vehicle_count"] = result["vehicle_count"].astype(int)
    result["is_holiday"] = result["is_holiday"].astype(int)
    result["is_weekend"] = result["is_weekend"].astype(int)
    result["is_makeup_workday"] = result["is_makeup_workday"].astype(int)
    result["is_workday"] = result["is_workday"].astype(int)
    return result, calendar


def prepare_dataset(output_dir: str | Path) -> dict[str, object]:
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    raw_path = destination / "volume_table6_training.csv"
    mirror_path = destination / "training_20min_avg_volume_mirror.csv"
    _ensure_source_file(
        raw_path,
        blob_sha=RAW_BLOB_SHA,
        expected_size=RAW_SIZE,
        expected_sha256=RAW_SHA256,
    )
    _ensure_source_file(
        mirror_path,
        blob_sha=AGGREGATE_BLOB_SHA,
        expected_size=AGGREGATE_SIZE,
        expected_sha256=AGGREGATE_SHA256,
    )

    aggregate = _aggregate_raw_events(raw_path)
    _verify_mirror(aggregate, mirror_path)
    standardized, calendar = _attach_calendar(aggregate)
    output_path = destination / "kdd2017_china_tollgate_20min.csv"
    calendar_path = destination / "china_calendar_2016_kdd_span.csv"
    standardized.to_csv(output_path, index=False)
    calendar.to_csv(calendar_path, index=False)

    pair_counts = {
        key: int(value)
        for key, value in standardized.groupby("series_id").size().items()
    }
    provenance = {
        "dataset": "KDD Cup 2017 Highway Tollgates Traffic Flow Prediction",
        "timezone": "Asia/Shanghai",
        "bin_seconds": BIN_SECONDS,
        "source_repository": GITHUB_REPOSITORY,
        "official_announcement": KDD_ANNOUNCEMENT_URL,
        "official_competition": TIANCHI_COMPETITION_URL,
        "competition_agreement": TIANCHI_AGREEMENT_URL,
        "usage_restriction": "non-profit academic research only; upstream license controls",
        "raw_file": {
            "path": str(raw_path),
            "bytes": raw_path.stat().st_size,
            "sha256": _sha256(raw_path),
            "github_blob_sha1": RAW_BLOB_SHA,
        },
        "aggregate_mirror": {
            "path": str(mirror_path),
            "bytes": mirror_path.stat().st_size,
            "sha256": _sha256(mirror_path),
            "github_blob_sha1": AGGREGATE_BLOB_SHA,
            "self_aggregation_match": True,
        },
        "standardized_file": {
            "path": str(output_path),
            "rows": int(len(standardized)),
            "sha256": _sha256(output_path),
        },
        "time_range": {
            "start": standardized["timestamp"].min().isoformat(),
            "end": standardized["timestamp"].max().isoformat(),
        },
        "series_rows": pair_counts,
        "missing_bucket_count": int(5 * 2_088 - len(standardized)),
        "missing_bucket_policy": "split_on_gap",
        "holiday_calendar": {
            "path": str(calendar_path),
            "source": CHINA_2016_NOTICE_URL,
            "valid_from": KDD2017_CALENDAR_VALID_FROM.isoformat(),
            "valid_to": KDD2017_CALENDAR_VALID_TO.isoformat(),
        },
    }
    provenance_path = destination / "provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return provenance


def _parse_args() -> argparse.Namespace:
    workspace_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="准备 KDD Cup 2017 中国收费站流量训练集")
    parser.add_argument(
        "--output-dir",
        default=str(
            workspace_root
            / "data"
            / "lstm_sources"
            / "public"
            / "kddcup2017_china_tollgate"
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _parse_args()
    print(json.dumps(prepare_dataset(arguments.output_dir), ensure_ascii=False, indent=2))
