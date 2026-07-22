"""
Quick BI 数据获取层

负责:
  - HMAC-SHA1 签名 & API 调用
  - 回收率数据拉取 → 首逾率计算
  - 案件量拉取
  - API 内存缓存（v1.0.1: 相同参数只请求一次）
  - Profile 统计（v1.0.1: 请求次数、行数、耗时、缓存命中率）
"""

import base64
import json
import time
from datetime import datetime, timedelta
from collections import defaultdict

import requests

from . import config


# ============================================================
#  API 内存缓存（v1.0.1）
# ============================================================

# 缓存: {(api_id, conditions_hash, return_fields_hash): parsed_json}
_query_cache = {}

# 统计
_cache_stats = {
    "calls": 0,        # 实际 HTTP 请求次数
    "hits": 0,         # 缓存命中次数
    "per_api": {},     # {api_id: {"calls": N, "rows": N, "time_sec": float}}
}


def _cached_query(api_id, conditions_str, return_fields_str=None):
    """
    带缓存的 Quick BI 查询。

    相同 (api_id, conditions, return_fields) 只请求一次，
    后续调用直接返回缓存中的 parsed JSON。

    Args:
        api_id: Quick BI API ID
        conditions_str: JSON 序列化后的 Conditions
        return_fields_str: JSON 序列化后的 ReturnFields（可选）

    Returns:
        parsed JSON dict（与 _sign_and_call 返回格式相同）
    """
    cache_key = (api_id, conditions_str, return_fields_str or "")

    if cache_key in _query_cache:
        _cache_stats["hits"] += 1
        return _query_cache[cache_key]

    t0 = time.time()
    extra = {"ApiId": api_id, "Conditions": conditions_str}
    if return_fields_str:
        extra["ReturnFields"] = return_fields_str

    data = _sign_and_call("QueryDataService", extra)
    elapsed = time.time() - t0

    _query_cache[cache_key] = data

    rows_count = len(data.get("Result", {}).get("Values", []))
    _cache_stats["calls"] += 1

    api_stats = _cache_stats["per_api"].setdefault(
        api_id, {"calls": 0, "rows": 0, "time_sec": 0.0}
    )
    api_stats["calls"] += 1
    api_stats["rows"] += rows_count
    api_stats["time_sec"] += elapsed

    return data


def get_cache_stats():
    """返回缓存统计的快照"""
    import copy
    return {
        "calls": _cache_stats["calls"],
        "hits": _cache_stats["hits"],
        "hit_rate": (
            _cache_stats["hits"] / (_cache_stats["calls"] + _cache_stats["hits"]) * 100
            if (_cache_stats["calls"] + _cache_stats["hits"]) > 0 else 0.0
        ),
        "per_api": {
            api_id: dict(s)
            for api_id, s in _cache_stats["per_api"].items()
        },
    }


def reset_cache():
    """重置缓存和统计（用于测试或新的一次运行）"""
    _query_cache.clear()
    _cache_stats["calls"] = 0
    _cache_stats["hits"] = 0
    _cache_stats["per_api"] = {}


def warm_cache_recovery(due_week):
    """
    预热回收率 API 缓存 — 静默请求一次，后续 fetch_overdue_data 命中缓存。

    Args:
        due_week: ISO 周字符串 "2026-28"
    """
    cond = json.dumps({"due_week": due_week}, ensure_ascii=False)
    _cached_query(config.QBI_API_RECOVERY, cond)


def warm_cache_cases(week_start):
    """
    预热案件量 API 缓存 — 静默请求一次，后续 fetch_case_volumes 命中缓存。

    Args:
        week_start: 周一日期字符串 "20260706"
    """
    cond = json.dumps({"due_date": week_start}, ensure_ascii=False)
    fields = json.dumps(["app", "due_date", "order", "cust_type", "case"])
    _cached_query(config.QBI_API_CASES, cond, fields)


# ============================================================
#  底层 API 调用
# ============================================================

def _sign_and_call(action, extra_params):
    """HMAC-SHA1 签名 → POST Quick BI → 返回 parsed JSON"""
    import hmac as _hmac
    import urllib.parse as _up

    params = {
        "Format": "json",
        "Version": "2022-01-01",
        "AccessKeyId": config.QBI_AK,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "SignatureVersion": "1.0",
        "SignatureNonce": str(int(time.time() * 1000000) + hash(action) % 1000000),
        "Action": action,
    }
    params.update(extra_params)

    sorted_keys = sorted(params.keys())
    canonicalized = "&".join(
        f"{_up.quote(k, safe='')}={_up.quote(str(params[k]), safe='')}"
        for k in sorted_keys
    )
    string_to_sign = (
        f"POST&{_up.quote('/', safe='')}&{_up.quote(canonicalized, safe='')}"
    )
    sig = base64.b64encode(
        _hmac.new(f"{config.QBI_SK}&".encode(), string_to_sign.encode(), "sha1").digest()
    ).decode()
    params["Signature"] = sig

    url = f"https://{config.QBI_ENDPOINT}/?" + _up.urlencode(params)
    resp = requests.post(url, timeout=60)
    return resp.json()


def _to_num(val):
    """安全转 float"""
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _to_int(val):
    """安全转 int"""
    if val is None or val == "-" or val == "":
        return 0
    try:
        return int(float(str(val).replace(",", "")))
    except (ValueError, TypeError):
        return 0


# ============================================================
#  数据拉取
# ============================================================

def fetch_overdue_data(due_week, country_code, business_date=None):
    """
    从 Quick BI API 524c3ccd429c 拉取回收率数据，
    并转换为首逾率（= 1 - 回收率）。

    Args:
        due_week: "2026-28"
        country_code: "MX" | "AR"
        business_date: "2026-07-06" — 仅保留 due_day 等于该日期的行（单日口径）

    Returns:
        {
            "meta": {"due_week": "...", "country": "..."},
            "overall": {"D0": {"due": N, "pay": N, "overdue_rate": float}, ...},
            "dimensions": {
                # 按 ANALYSIS_DIMENSIONS 中的维度拆分
                "product": {
                    "分期": {"D0": {"due": N, "pay": N, "overdue_rate": float}, ...},
                    "单期": {...},
                },
                "cust_type": {
                    "新客": {...},
                    "老客": {...},
                },
            },
            "raw_rows": [...],  # 原始行（用于后续新增维度计算）
        }
    """
    country = config.COUNTRIES[country_code]
    apps = set(country["apps"])

    # 将 business_date 转为 due_day 格式 (2026-07-06 → 20260706)
    due_day_str = business_date.replace("-", "") if business_date else None

    print(f"  [QBI] Fetch overdue data ({country['name']}, due_week={due_week}) ...",
          end=" ", flush=True)

    conditions_str = json.dumps({"due_week": due_week}, ensure_ascii=False)
    data = _cached_query(config.QBI_API_RECOVERY, conditions_str)

    if not data.get("Success"):
        print(f"FAIL: {data.get('Message', 'Unknown')}")
        return None

    rows = data.get("Result", {}).get("Values", [])
    print(f"{len(rows)} rows", flush=True)

    # 按 APP 过滤
    filtered_rows = [r for r in rows if r.get("app_name", "") in apps]
    print(f"  [QBI] APP filter: {len(filtered_rows)} rows (apps={apps})", flush=True)

    # 按 due_day 过滤（单日口径 — 与 BI 页面一致）
    if due_day_str:
        before = len(filtered_rows)
        filtered_rows = [r for r in filtered_rows if r.get("due_day", "") == due_day_str]
        print(f"  [QBI] due_day filter ({due_day_str}): {len(filtered_rows)} rows (was {before})", flush=True)

    # 聚合: 按维度 + 阶段
    agg_overall = _init_stage_agg()
    agg_product = defaultdict(_init_stage_agg)      # 产品类型（映射后: 分期/单期）
    agg_order_type = defaultdict(_init_stage_agg)   # 包体（原始 order_type）
    agg_cust = defaultdict(_init_stage_agg)          # 风控等级

    for r in filtered_rows:
        cust_type = r.get("cust_type", "")
        order_type = r.get("order_type", "")
        product = config.ORDER_TYPE_MAP.get(order_type)
        if product is None:
            continue

        for stage_label in config.STAGE_KEY_MAP.values():
            rcalc = calculate_first_overdue_rate(r, stage_label)

            agg_overall[stage_label]["pay"] += rcalc["cum_pay"]
            agg_overall[stage_label]["due"] += rcalc["due_amt"]
            agg_product[product][stage_label]["pay"] += rcalc["cum_pay"]
            agg_product[product][stage_label]["due"] += rcalc["due_amt"]
            agg_order_type[order_type][stage_label]["pay"] += rcalc["cum_pay"]
            agg_order_type[order_type][stage_label]["due"] += rcalc["due_amt"]
            agg_cust[cust_type][stage_label]["pay"] += rcalc["cum_pay"]
            agg_cust[cust_type][stage_label]["due"] += rcalc["due_amt"]

    # 计算首逾率
    result = {
        "meta": {"due_week": due_week, "country": country_code},
        "overall": _compute_overdue_rates(agg_overall),
        "dimensions": {
            "product": {k: _compute_overdue_rates(v) for k, v in agg_product.items()},
            "order_type": {k: _compute_overdue_rates(v) for k, v in agg_order_type.items()},
            "cust_type": {k: _compute_overdue_rates(v) for k, v in agg_cust.items()},
        },
        "raw_rows": filtered_rows,
    }

    # 摘要日志
    for stage in config.FIRST_OVERDUE_STAGES:
        r = result["overall"].get(stage, {})
        print(f"  [QBI] Overall {stage}: due={r.get('due',0):,.0f}  "
              f"pay={r.get('pay',0):,.0f}  overdue_rate={r.get('overdue_rate',0):.2%}",
              flush=True)

    return result


def fetch_case_volumes(due_week, country_code):
    """从 Quick BI API c2f93e0fa45b 获取到期案件量"""
    country = config.COUNTRIES[country_code]
    apps = set(country["apps"])

    print(f"  [QBI] Fetch case volumes ({country['name']}, due_week={due_week}) ...",
          end=" ", flush=True)

    year, week = due_week.split("-")
    monday = datetime.strptime(f"{year}-W{week}-1", "%Y-W%W-%w")
    week_start = monday.strftime("%Y%m%d")
    week_end = (monday + timedelta(days=6)).strftime("%Y%m%d")

    fields = json.dumps(["app", "due_date", "order", "cust_type", "case"])
    conditions_str = json.dumps({"due_date": week_start}, ensure_ascii=False)

    data = _cached_query(config.QBI_API_CASES, conditions_str, fields)

    if not data.get("Success"):
        print(f"FAIL: {data.get('Message', 'Unknown')}")
        return {}

    rows = data.get("Result", {}).get("Values", [])
    rows = [r for r in rows if week_start <= r.get("due_date", "") <= week_end]

    agg = defaultdict(int)
    for r in rows:
        app = r.get("app", "")
        if app not in apps:
            continue
        order = r.get("order", "")
        product = config.ORDER_TYPE_MAP.get(order)
        if product is None:
            continue
        cust = r.get("cust_type", "")
        agg[("overall", "")] += _to_int(r.get("case"))
        agg[("product", product)] += _to_int(r.get("case"))
        agg[("cust_type", cust)] += _to_int(r.get("case"))

    print(f"{len(rows)} rows -> {len(agg)} buckets", flush=True)
    return dict(agg)


# ============================================================
#  统一首逾计算（v1.0.2 — BI 口径）
# ============================================================

# 回款阶段累计顺序（从最早到最晚）
_PAY_STAGE_ORDER = ["D_3", "D_2", "D_1", "D0", "D1", "S1", "S2"]


def calculate_first_overdue_rate(row, stage):
    """
    统一首逾计算 — 已于 UAT 与 Quick BI 验证一致（2026-07-06）。

    到期本金 = due_amt
    累计回款 = D_3_pay_amt + D_2_pay_amt + ... + {stage}_pay_amt（按阶段递增累计）
    金额首逾率 = 1 - 累计回款 / 到期本金

    Args:
        row: Quick BI 回收率 API 返回的原始行 dict
        stage: 阶段标签 "D-2" | "D-1" | "D0" | "D1" | "S1" | "S2"

    Returns:
        {
            "due_amt": float,       # 到期本金 (= due_amt)
            "cum_pay": float,       # 累计回款 (up to stage)
            "overdue_rate": float,  # 金额首逾率 = 1 - cum_pay / due_amt
        }
    """
    due_amt = _to_num(row.get("due_amt", 0))

    # 找到目标 stage_key
    target_key = None
    for sk, sl in config.STAGE_KEY_MAP.items():
        if sl == stage:
            target_key = sk
            break

    if target_key is None:
        return {"due_amt": 0.0, "cum_pay": 0.0, "overdue_rate": 0.0}

    # 累计回款：从 D_3 到目标阶段
    cum_pay = 0.0
    for ps in _PAY_STAGE_ORDER:
        cum_pay += _to_num(row.get(f"{ps}_pay_amt", 0))
        if ps == target_key:
            break

    overdue_rate = 1.0 - (cum_pay / due_amt) if due_amt > 0 else 0.0

    return {
        "due_amt": round(due_amt, 2),
        "cum_pay": round(cum_pay, 2),
        "overdue_rate": round(overdue_rate, 4),
    }


# ============================================================
#  辅助函数
# ============================================================

def _init_stage_agg():
    """初始化阶段聚合容器"""
    return {s: {"pay": 0.0, "due": 0.0} for s in config.STAGE_KEY_MAP.values()}


def _compute_overdue_rates(stage_agg):
    """计算各阶段首逾率 = 1 - (pay / due)"""
    result = {}
    for stage, vals in stage_agg.items():
        due = vals["due"]
        pay = vals["pay"]
        recovery_rate = pay / due if due > 0 else 0.0
        overdue_rate = 1.0 - recovery_rate
        result[stage] = {
            "due": round(due, 2),
            "pay": round(pay, 2),
            "recovery_rate": round(recovery_rate, 4),
            "overdue_rate": round(overdue_rate, 4),
        }
    return result
