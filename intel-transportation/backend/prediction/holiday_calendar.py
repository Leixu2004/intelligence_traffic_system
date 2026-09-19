"""Versioned holiday rules used by traffic forecasting features."""

from __future__ import annotations

from datetime import date, datetime


CHINA_2016_NOTICE_URL = (
    "https://www.gov.cn/zhengce/content/2015-12/10/content_10394.htm"
)
CHINA_2026_NOTICE_URL = (
    "https://www.gov.cn/zhengce/content/202511/content_7047090.htm"
)
KDD2017_CALENDAR_VALID_FROM = date(2016, 9, 19)
KDD2017_CALENDAR_VALID_TO = date(2016, 10, 17)
KDD2017_HOLIDAY_DATES = tuple(
    date(2016, 10, day) for day in range(1, 8)
)
KDD2017_MAKEUP_WORKDAYS = (
    date(2016, 10, 8),
    date(2016, 10, 9),
)

CHINA_2026_CALENDAR_VALID_FROM = date(2026, 1, 1)
CHINA_2026_CALENDAR_VALID_TO = date(2026, 12, 31)
CHINA_2026_HOLIDAY_DATES = tuple(
    date.fromisoformat(value)
    for value in (
        "2026-01-01", "2026-01-02", "2026-01-03",
        "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18",
        "2026-02-19", "2026-02-20", "2026-02-21", "2026-02-22",
        "2026-02-23",
        "2026-04-04", "2026-04-05", "2026-04-06",
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04",
        "2026-05-05",
        "2026-06-19", "2026-06-20", "2026-06-21",
        "2026-09-25", "2026-09-26", "2026-09-27",
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        "2026-10-05", "2026-10-06", "2026-10-07",
    )
)
CHINA_2026_MAKEUP_WORKDAYS = tuple(
    date.fromisoformat(value)
    for value in (
        "2026-01-04",
        "2026-02-14",
        "2026-02-28",
        "2026-05-09",
        "2026-09-20",
        "2026-10-10",
    )
)


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def china_calendar_flags(
    value: date | datetime,
    *,
    valid_from: date = KDD2017_CALENDAR_VALID_FROM,
    valid_to: date = KDD2017_CALENDAR_VALID_TO,
    holiday_dates: tuple[date, ...] = KDD2017_HOLIDAY_DATES,
    makeup_workdays: tuple[date, ...] = KDD2017_MAKEUP_WORKDAYS,
) -> dict[str, object]:
    """Classify one China-local date using the locked official calendar."""

    local_date = _as_date(value)
    if local_date < valid_from or local_date > valid_to:
        raise ValueError(
            f"中国节假日日历仅覆盖 {valid_from.isoformat()} 至 {valid_to.isoformat()}，"
            f"收到 {local_date.isoformat()}"
        )

    is_weekend = local_date.weekday() >= 5
    is_makeup_workday = local_date in makeup_workdays
    is_holiday = local_date in holiday_dates
    if is_makeup_workday:
        day_type = "makeup_workday"
        is_workday = True
    elif is_holiday:
        day_type = "holiday"
        is_workday = False
    elif is_weekend:
        day_type = "weekend"
        is_workday = False
    else:
        day_type = "workday"
        is_workday = True
    return {
        "is_holiday": is_holiday,
        "is_weekend": is_weekend,
        "is_makeup_workday": is_makeup_workday,
        "is_workday": is_workday,
        "day_type": day_type,
    }


def kdd2017_calendar_metadata() -> dict[str, object]:
    return {
        "holiday_policy": "china_statutory_with_makeup",
        "holiday_calendar_source": CHINA_2016_NOTICE_URL,
        "holiday_calendar_years": [2016],
        "holiday_calendar_valid_from": KDD2017_CALENDAR_VALID_FROM.isoformat(),
        "holiday_calendar_valid_to": KDD2017_CALENDAR_VALID_TO.isoformat(),
        "holiday_dates": [value.isoformat() for value in KDD2017_HOLIDAY_DATES],
        "makeup_workdays": [value.isoformat() for value in KDD2017_MAKEUP_WORKDAYS],
    }


def china_prediction_calendar_metadata() -> dict[str, object]:
    """Return non-contiguous, source-locked calendars available at inference time."""

    periods = [
        {
            "calendar_policy_id": "cn_state_council_2016_kdd_window_v1",
            "valid_from": KDD2017_CALENDAR_VALID_FROM.isoformat(),
            "valid_to": KDD2017_CALENDAR_VALID_TO.isoformat(),
            "source_url": CHINA_2016_NOTICE_URL,
            "holiday_dates": [value.isoformat() for value in KDD2017_HOLIDAY_DATES],
            "makeup_workdays": [value.isoformat() for value in KDD2017_MAKEUP_WORKDAYS],
        },
        {
            "calendar_policy_id": "cn_state_council_2026_gbfmd_2025_7_v1",
            "valid_from": CHINA_2026_CALENDAR_VALID_FROM.isoformat(),
            "valid_to": CHINA_2026_CALENDAR_VALID_TO.isoformat(),
            "source_title": "国务院办公厅关于2026年部分节假日安排的通知",
            "document_no": "国办发明电〔2025〕7号",
            "published_date": "2025-11-04",
            "source_url": CHINA_2026_NOTICE_URL,
            "holiday_feature_semantics": "official_days_off_in_annual_notice",
            "holiday_dates": [value.isoformat() for value in CHINA_2026_HOLIDAY_DATES],
            "makeup_workdays": [value.isoformat() for value in CHINA_2026_MAKEUP_WORKDAYS],
        },
    ]
    return {
        "training_calendar_years": [2016],
        "inference_calendar_years": [2016, 2026],
        "inference_calendar_periods": periods,
        "unknown_year_policy": "reject",
    }
