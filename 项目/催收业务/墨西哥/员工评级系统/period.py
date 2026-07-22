"""
自动周期计算 — 半月制考核周期。

每月分两期：[1, 15] 和 [16, 月末]。
根据当天日期自动定位「本期 / 上一期 / 上上期」，以后无需手改 cycle_label。

示例（今天 2026-07-14）：
    本期   2026-07-01 ~ 2026-07-15
    上一期 2026-06-16 ~ 2026-06-30
    上上期 2026-06-01 ~ 2026-06-15
"""

import calendar
from datetime import datetime, date


def _last_day(year, month):
    return calendar.monthrange(year, month)[1]


def _half_bounds(year, month, first_half):
    """返回某半月的 (start_date, end_date)"""
    if first_half:
        return date(year, month, 1), date(year, month, 15)
    return date(year, month, 16), date(year, month, _last_day(year, month))


def _prev_half(year, month, first_half):
    """上一个半月周期的 (year, month, first_half)"""
    if first_half:
        # 上一期 = 上个月的下半月
        if month == 1:
            return year - 1, 12, False
        return year, month - 1, False
    # 下半月的上一期 = 本月上半月
    return year, month, True


def compute_periods(today=None):
    """
    计算三个周期。

    Returns:
        list[dict]: 依次为 current / prev1 / prev2，每个：
            {
              "key":   "current" | "prev1" | "prev2",
              "name":  "本期" | "上一期" | "上上期",
              "start": date, "end": date,
              "start_str": "YYYYMMDD", "end_str": "YYYYMMDD",
              "label": "M.D-M.D",   # 如 7.1-7.15
              "days":  int,          # 周期天数
            }
    """
    if today is None:
        today = datetime.now().date()
    elif isinstance(today, datetime):
        today = today.date()

    first_half = today.day <= 15
    y, m, fh = today.year, today.month, first_half

    names = [("current", "本期"), ("prev1", "上一期"), ("prev2", "上上期")]
    periods = []
    for key, name in names:
        start, end = _half_bounds(y, m, fh)
        periods.append({
            "key": key,
            "name": name,
            "start": start,
            "end": end,
            "start_str": start.strftime("%Y%m%d"),
            "end_str": end.strftime("%Y%m%d"),
            "label": f"{start.month}.{start.day}-{end.month}.{end.day}",
            "days": (end - start).days + 1,
        })
        y, m, fh = _prev_half(y, m, fh)

    return periods
