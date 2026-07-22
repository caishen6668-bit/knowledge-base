"""
数据获取层 — 从 Quick BI 拉取业绩 & 出勤，构建 DataFrame。

关键 API 语义（已用真实数据验证，2026-07）：
  · 业绩 API：statis_date=X 返回「从 X 起到最新可用日」的区间，每人每天一行。
    → 每个周期只需请求一次（用周期起始日），再按周期区间过滤。
    → 规格里的「循环每天请求」会重复计数（每次都返回整个向后窗口），故不采用。
  · 出勤 API：day=X 返回「从 X 起向后约 50 天」的排班窗口。
    → 一次请求（用本期起始日）即可覆盖本期，再按本期区间过滤。

缓存：同一运行日的结果落 cache/*_YYYYMMDD.pkl，当天重复运行直接读缓存，不再请求。
"""

import os
import pickle
from datetime import datetime

import pandas as pd

from . import api, config
from .utils import log, ok


def _cache_path(kind, run_date_str):
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    return os.path.join(config.CACHE_DIR, f"{kind}_{run_date_str}.pkl")


def _load_cache(kind, run_date_str):
    path = _cache_path(kind, run_date_str)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    return None


def _save_cache(kind, run_date_str, obj):
    with open(_cache_path(kind, run_date_str), "wb") as f:
        pickle.dump(obj, f)


def data_fetch_time(run_date_str):
    """
    实际 Quick BI 取数时间。

    取业绩缓存文件的最后写入时间：本次为新取数则约等于当前时间；
    命中缓存则为首次取数时间（即数据真正来自 Quick BI 的时刻）。
    API 本身不返回更新时间，故以程序落缓存的时间为准。
    """
    path = _cache_path("performance", run_date_str)
    if os.path.exists(path):
        return datetime.fromtimestamp(os.path.getmtime(path))
    return None


# ============================================================
#  业绩
# ============================================================
def fetch_performance(periods, run_date_str, refresh=False):
    """
    拉取三个周期的业绩原始行。

    Args:
        refresh: True 时忽略缓存，强制重新请求 Quick BI。

    Returns:
        dict[str, pd.DataFrame]: {period_key: df}，df 为该周期内目标部门的原始行。
    """
    if not refresh:
        cached = _load_cache("performance", run_date_str)
        if cached is not None:
            ok("业绩缓存命中，跳过请求")
            return cached
    else:
        log("业绩：--refresh 强制刷新，忽略缓存")

    result = {}
    for p in periods:
        rows = api.query(
            config.QBI_API_PERFORMANCE,
            {"statis_date": p["start_str"]},
        )
        # 按周期区间 + 部门 + 排除阶段过滤
        kept = [
            r for r in rows
            if p["start_str"] <= str(r.get(config.PERF_FIELD_DATE, "")) <= p["end_str"]
            and r.get("dept_name") == config.DEPARTMENT
            and not any(
                str(r.get(config.PERF_FIELD_STAGE, "")).upper().startswith(s.upper())
                for s in config.EXCLUDED_STAGES
            )
        ]
        df = pd.DataFrame(kept)
        result[p["key"]] = df

        n_days = df[config.PERF_FIELD_DATE].nunique() if not df.empty else 0
        ok(f"{p['name']} {p['label']}：{p['days']}天区间，实际获取 {n_days} 天 / {len(df)} 行")

    _save_cache("performance", run_date_str, result)
    return result


# ============================================================
#  出勤
# ============================================================
def fetch_attendance(current_period, run_date_str, refresh=False):
    """
    一次请求拉取本期出勤（排班）原始行。

    Args:
        refresh: True 时忽略缓存，强制重新请求 Quick BI。

    Returns:
        pd.DataFrame: 本期区间内目标部门的排班行。
    """
    if not refresh:
        cached = _load_cache("attendance", run_date_str)
        if cached is not None:
            ok("出勤缓存命中，跳过请求")
            return cached
    else:
        log("出勤：--refresh 强制刷新，忽略缓存")

    rows = api.query(
        config.QBI_API_ATTENDANCE,
        {"day": current_period["start_str"]},
    )
    kept = [
        r for r in rows
        if current_period["start_str"] <= str(r.get(config.ATT_FIELD_DAY, "")) <= current_period["end_str"]
        and r.get(config.ATT_FIELD_DEPT) == config.DEPARTMENT
    ]
    df = pd.DataFrame(kept)

    n_days = df[config.ATT_FIELD_DAY].nunique() if not df.empty else 0
    ok(f"出勤获取成功：{current_period['label']} 区间，{n_days} 天 / {len(df)} 行")

    _save_cache("attendance", run_date_str, df)
    return df
