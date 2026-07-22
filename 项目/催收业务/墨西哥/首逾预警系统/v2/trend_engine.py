"""
V2 趋势预警引擎 — 核心计算层

职责:
  1. 拉取历史 N 天的首逾数据（复用 V1 fetch_overdue_data + 缓存）
  2. 计算 4 种趋势比较: DoD / 3d-avg / 7d-avg / target
  3. 自动判断严重等级: 🟢正常 / 🟡关注 / 🟠警告 / 🔴严重
  4. 输出统一 TrendResult[] → TrendReport

所有后续 AI 分析、异常定位、飞书预警全部基于 TrendReport。

不修改 V1 任何代码。通过 from .. import 复用 V1 数据层。
"""

import sys
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from .. import config as v1_config
from ..quickbi import (
    fetch_overdue_data,
    calculate_first_overdue_rate,
    _to_int,
    warm_cache_recovery,
    get_cache_stats,
    reset_cache,
)
from . import config as v2_config
from .models import TrendResult, TrendComparison, TrendReport


# ============================================================
#  日期工具
# ============================================================

def _get_due_week(date_str: str) -> str:
    """日期 → ISO week, 如 '2026-07-06' → '2026-28'"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def _get_business_date(run_date: str) -> str:
    """运行日期 → 业务日期（前一天）"""
    run_dt = datetime.strptime(run_date, "%Y-%m-%d")
    return (run_dt - timedelta(days=1)).strftime("%Y-%m-%d")


# ============================================================
#  数据拉取：历史多日
# ============================================================

def _fetch_day_snapshot(business_date: str, country_code: str) -> Optional[dict]:
    """
    拉取单个业务日期的首逾数据快照。

    复用 V1 的 fetch_overdue_data（含 API 缓存），
    返回该日在所有维度的聚合结果。

    Args:
        business_date: "2026-07-06"
        country_code: "MX" | "AR"

    Returns:
        {
            "business_date": "2026-07-06",
            "due_week": "2026-28",
            "overall": {"D0": {"due": ..., "pay": ..., "overdue_rate": ...}, ...},
            "dimensions": {
                "product": {"单期": {stage: {...}}, "分期": {stage: {...}}},
                "order_type": {...},
            },
            "raw_rows": [...],   # 原始行（用于 order_grade 实时聚合）
        }
        或 None（API 失败或无数据）
    """
    due_week = _get_due_week(business_date)

    try:
        data = fetch_overdue_data(due_week, country_code, business_date=business_date)
    except Exception as e:
        print(f"  [V2] WARN: fetch_overdue_data failed for {business_date}: {e}", flush=True)
        return None

    if data is None:
        return None
    if not data.get("raw_rows"):
        return None  # 无数据（如周末/节假日）

    return data


def _fetch_historical_snapshots(
    business_date: str, country_code: str, lookback_days: int = 7
) -> List[dict]:
    """
    拉取当前日期 + 过去 N 天的首逾数据快照。

    Args:
        business_date: 当前业务日期 "2026-07-06"
        country_code: "MX" | "AR"
        lookback_days: 回溯天数

    Returns:
        [snapshot_today, snapshot_yesterday, ..., snapshot_N_days_ago]
        按日期降序排列（最新在前），无数据的日期为 None
    """
    dt = datetime.strptime(business_date, "%Y-%m-%d")

    # 预热缓存：收集所有需要的 due_week，提前请求
    due_weeks = set()
    for i in range(lookback_days + 1):
        day = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
        due_weeks.add(_get_due_week(day))

    for dw in due_weeks:
        try:
            warm_cache_recovery(dw)
        except Exception:
            pass  # 预热失败不阻塞，后续拉取会重试

    # 拉取每天的快照
    snapshots = []
    dt_current = datetime.strptime(business_date, "%Y-%m-%d")
    for i in range(lookback_days + 1):
        day = (dt_current - timedelta(days=i)).strftime("%Y-%m-%d")
        snap = _fetch_day_snapshot(day, country_code)
        snapshots.append(snap)

    return snapshots


# ============================================================
#  数据提取：从快照中提取各维度的首逾率
# ============================================================

def _extract_dimension_rates(
    snapshot: dict, stage: str
) -> Dict[Tuple[str, str], dict]:
    """
    从单日快照中提取所有维度的首逾率 + 辅助指标。

    Args:
        snapshot: _fetch_day_snapshot 的输出
        stage: "D0" | "D1" | ...

    Returns:
        {
            ("overall", "total"): {
                "value": 0.3185, "due": 873068.33, "pay": 594953.25, "cases": 834
            },
            ("product_type", "单期"): {...},
            ("product_type", "分期"): {...},
            ("order_type", "非分期"): {...},
            ...
            ("order_grade", "A"): {...},
            ...
        }
    """
    rates = {}

    # --- overall ---
    overall = snapshot.get("overall", {}).get(stage, {})
    rates[("overall", "total")] = {
        "value": overall.get("overdue_rate", 0.0),
        "due": overall.get("due", 0.0),
        "pay": overall.get("pay", 0.0),
        "cases": 0,  # 稍后从 raw_rows 补全
    }

    # --- 从 raw_rows 预聚合 product_type / order_type 的到期单量 ---
    raw_rows = snapshot.get("raw_rows", [])
    product_cases: Dict[str, int] = {}
    order_type_cases: Dict[str, int] = {}
    for r in raw_rows:
        ot = r.get("order_type", "")
        due_case = _to_int(r.get("due_case", 0))
        if due_case > 0:
            # product_type: order_type → 分期/单期（复用 V1 ORDER_TYPE_MAP）
            pt = v1_config.ORDER_TYPE_MAP.get(ot, "")
            if pt:
                product_cases[pt] = product_cases.get(pt, 0) + due_case
            # order_type 直接计数
            if ot:
                order_type_cases[ot] = order_type_cases.get(ot, 0) + due_case

    # 补全 overall 到期单量（所有 product_type 之和 = 全部订单）
    rates[("overall", "total")]["cases"] = sum(product_cases.values())

    # --- product_type ---
    product_data = snapshot.get("dimensions", {}).get("product", {})
    for bucket, stage_data in product_data.items():
        s = stage_data.get(stage, {})
        rates[("product_type", bucket)] = {
            "value": s.get("overdue_rate", 0.0),
            "due": s.get("due", 0.0),
            "pay": s.get("pay", 0.0),
            "cases": product_cases.get(bucket, 0),
        }

    # --- order_type ---
    order_type_data = snapshot.get("dimensions", {}).get("order_type", {})
    for bucket, stage_data in order_type_data.items():
        s = stage_data.get(stage, {})
        rates[("order_type", bucket)] = {
            "value": s.get("overdue_rate", 0.0),
            "due": s.get("due", 0.0),
            "pay": s.get("pay", 0.0),
            "cases": order_type_cases.get(bucket, 0),
        }

    # --- order_grade（从 raw_rows 实时聚合） ---
    grades = v2_config.TREND_DIMENSIONS[-1].get("buckets", [])
    agg_grade = {g: {"due": 0.0, "pay": 0.0, "cases": 0} for g in grades}
    for r in raw_rows:
        grade = r.get("order_grade", "")
        if grade not in agg_grade:
            continue
        rcalc = calculate_first_overdue_rate(r, stage)
        agg_grade[grade]["due"] += rcalc["due_amt"]
        agg_grade[grade]["pay"] += rcalc["cum_pay"]
        agg_grade[grade]["cases"] += _to_int(r.get("due_case", 0))

    for grade, agg in agg_grade.items():
        if agg["due"] > 0:
            val = 1.0 - (agg["pay"] / agg["due"])
        else:
            val = 0.0
        rates[("order_grade", grade)] = {
            "value": round(val, 4),
            "due": round(agg["due"], 2),
            "pay": round(agg["pay"], 2),
            "cases": agg["cases"],
        }

    return rates


# ============================================================
#  趋势比较计算
# ============================================================

def _build_trend_comparison(
    method: str,
    current_value: float,
    baseline_value: float,
    stage: str,
    is_improvement: bool = False,
) -> TrendComparison:
    """
    构建单个 TrendComparison。

    Args:
        method: "dod" | "3d_avg" | "7d_avg" | "target"
        current_value: 当前首逾率
        baseline_value: 基线值
        stage: 阶段
        is_improvement: 当前值变化是否改善方向（首逾率下降=改善）

    Returns:
        TrendComparison
    """
    thresholds = v2_config.TREND_THRESHOLDS.get(stage, {}).get(method, {})
    label = v2_config.TREND_METHOD_LABELS.get(method, method)

    if baseline_value == 0 or baseline_value is None:
        return TrendComparison(
            method=method,
            method_label=label,
            current_value=current_value,
            baseline_value=baseline_value or 0.0,
            change_abs=0.0,
            change_pct=0.0,
            alert_level="GREEN",
            alert_icon=v1_config.ALERT_DISPLAY["GREEN"]["icon"],
            alert_label=v1_config.ALERT_DISPLAY["GREEN"]["label"],
            is_improvement=False,
        )

    change_abs = current_value - baseline_value
    change_pct = change_abs / abs(baseline_value) if abs(baseline_value) > 0.0001 else 0.0

    # 首逾率下降 = 改善 → 永远是 GREEN
    if change_abs <= 0:
        return TrendComparison(
            method=method,
            method_label=label,
            current_value=current_value,
            baseline_value=baseline_value,
            change_abs=round(change_abs, 4),
            change_pct=round(change_pct, 4),
            alert_level="GREEN",
            alert_icon=v1_config.ALERT_DISPLAY["GREEN"]["icon"],
            alert_label=v1_config.ALERT_DISPLAY["GREEN"]["label"],
            is_improvement=True,
        )

    # 首逾率上升 → 按阈值判定
    pp = change_abs  # 已经是小数形式

    if not thresholds:
        return TrendComparison(
            method=method,
            method_label=label,
            current_value=current_value,
            baseline_value=baseline_value,
            change_abs=round(change_abs, 4),
            change_pct=round(change_pct, 4),
            alert_level="GREEN",
            alert_icon=v1_config.ALERT_DISPLAY["GREEN"]["icon"],
            alert_label=v1_config.ALERT_DISPLAY["GREEN"]["label"],
            is_improvement=False,
        )

    red_t = thresholds.get("red", 999)
    orange_t = thresholds.get("orange", 999)
    yellow_t = thresholds.get("yellow", 999)

    if pp >= red_t:
        level = "RED"
        threshold_used = red_t
    elif pp >= orange_t:
        level = "ORANGE"
        threshold_used = orange_t
    elif pp >= yellow_t:
        level = "YELLOW"
        threshold_used = yellow_t
    else:
        level = "GREEN"
        threshold_used = yellow_t

    display = v1_config.ALERT_DISPLAY[level]

    return TrendComparison(
        method=method,
        method_label=label,
        current_value=current_value,
        baseline_value=baseline_value,
        change_abs=round(change_abs, 4),
        change_pct=round(change_pct, 4),
        alert_level=level,
        alert_icon=display["icon"],
        alert_label=display["label"],
        threshold_used=round(threshold_used, 4),
        is_improvement=False,
    )


def _compute_comparisons(
    dim_key: str,
    bucket: str,
    current_rates: dict,
    historical_rates_list: List[dict],
    stage: str,
    country_code: str,
) -> List[TrendComparison]:
    """
    计算某个维度切片的所有趋势比较。

    Args:
        dim_key: "overall" | "product_type" | ...
        bucket: "total" | "单期" | ...
        current_rates: {(dim, bucket): {value, due, pay, cases}}
        historical_rates_list: [rates_day0, rates_day1, ...]（每天一个 dict）
        stage: 阶段
        country_code: 国家代码（用于查 target）

    Returns:
        [TrendComparison, ...]
    """
    comparisons = []
    key = (dim_key, bucket)
    current = current_rates.get(key, {})
    current_value = current.get("value", 0.0)

    if current_value == 0.0 and current.get("due", 0) == 0:
        # 该维度切片完全无数据
        return comparisons

    # --- 1. DoD: 当前 vs 昨天 ---
    yesterday_rates = historical_rates_list[0] if len(historical_rates_list) > 0 else {}
    yesterday = yesterday_rates.get(key, {})
    yesterday_value = yesterday.get("value")

    if yesterday_value is not None:
        comp = _build_trend_comparison("dod", current_value, yesterday_value, stage)
        comparisons.append(comp)

    # --- 2. 3d_avg: 当前 vs 近3日均值 ---
    recent_3 = historical_rates_list[:3]  # 最近 3 天（不含今天）
    vals_3d = []
    for hr in recent_3:
        data = hr.get(key, {})
        if data.get("due", 0) > 0:  # 有到期本金才算有效数据（首逾率可为 0%）
            vals_3d.append(data.get("value", 0.0))
    if len(vals_3d) >= 2:  # 至少需要 2 天数据
        avg_3d = sum(vals_3d) / len(vals_3d)
        comp = _build_trend_comparison("3d_avg", current_value, avg_3d, stage)
        comparisons.append(comp)

    # --- 3. 7d_avg: 当前 vs 近7日均值 ---
    recent_7 = historical_rates_list[:7]
    vals_7d = []
    for hr in recent_7:
        data = hr.get(key, {})
        if data.get("due", 0) > 0:  # 有到期本金才算有效数据
            vals_7d.append(data.get("value", 0.0))
    if len(vals_7d) >= 3:  # 至少需要 3 天数据
        avg_7d = sum(vals_7d) / len(vals_7d)
        comp = _build_trend_comparison("7d_avg", current_value, avg_7d, stage)
        comparisons.append(comp)

    # --- 4. target: 当前 vs 目标值 ---
    targets = v2_config.TARGET_RATES.get(country_code, {})
    target_value = targets.get(stage)
    if target_value is not None:
        comp = _build_trend_comparison("target", current_value, target_value, stage)
        comparisons.append(comp)

    return comparisons


# ============================================================
#  样本过滤
# ============================================================

def _is_sample_too_small(due_amount: float, case_count: int = 0) -> bool:
    """样本量过滤（复用 V1 逻辑）"""
    if due_amount < v2_config.MIN_AMOUNT:
        return True
    if case_count > 0 and case_count < v2_config.MIN_CASE:
        return True
    return False


# ============================================================
#  主入口：计算趋势报告
# ============================================================

def compute_trends(
    country_code: str,
    business_date: str,
    stage: str = "D0",
    run_date: str = "",
) -> TrendReport:
    """
    计算完整的趋势分析报告 — V2 核心入口。

    流程:
      1. 拉取今天 + 过去 7 天数据
      2. 提取所有维度各天的首逾率
      3. 逐维度计算 DoD / 3d-avg / 7d-avg / target 比较
      4. 按阈值自动判定严重等级
      5. 输出 TrendReport

    Args:
        country_code: "MX" | "AR"
        business_date: 业务日期 "2026-07-06"
        stage: 分析阶段 "D0" | "D1" | ...
        run_date: 运行日期（用于报告元数据）

    Returns:
        TrendReport
    """
    country = v1_config.COUNTRIES[country_code]

    print(f"\n{'='*55}")
    print(f"  V2 趋势预警引擎  {v2_config.V2_VERSION}  Phase {v2_config.V2_PHASE}")
    print(f"  国家: {country['name']} ({country_code})  |  业务日期: {business_date}  |  阶段: {stage}")
    print(f"{'='*55}")

    # ---- Step 1: 拉取历史数据 ----
    print(f"\n  [1/3] 拉取历史数据 (回溯 {v2_config.LOOKBACK_DAYS} 天) ...")
    snapshots = _fetch_historical_snapshots(business_date, country_code, v2_config.LOOKBACK_DAYS)

    today_snapshot = snapshots[0]
    historical_snapshots = snapshots[1:]  # 昨天及更早

    valid_historical = [s for s in historical_snapshots if s is not None]
    print(f"  当前: {'✅' if today_snapshot else '❌ 无数据'}")
    print(f"  历史: {len(valid_historical)}/{len(historical_snapshots)} 天有数据")

    if today_snapshot is None:
        # 无当前数据 → 返回空报告
        print(f"  ⚠️ 当前日期无数据，无法生成趋势报告")
        return TrendReport(
            country_code=country_code,
            country_name=country["name"],
            business_date=business_date,
            stage=stage,
            run_date=run_date,
        )

    # ---- Step 2: 提取各天各维度的首逾率 ----
    print(f"\n  [2/3] 提取各维度首逾率 ...")

    today_rates = _extract_dimension_rates(today_snapshot, stage)
    historical_rates = [_extract_dimension_rates(s, stage) if s else {}
                        for s in historical_snapshots]

    # 收集历史值（供展示）
    all_historical_values = defaultdict(dict)  # {(dim, bucket): {date: value}}
    for i, hr in enumerate(historical_rates):
        if not hr:
            continue
        date_str = (datetime.strptime(business_date, "%Y-%m-%d") - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        for key, data in hr.items():
            all_historical_values[key][date_str] = data.get("value", 0.0)

    # ---- Step 3: 逐维度逐桶计算趋势 ----
    print(f"\n  [3/3] 计算趋势比较 ...")

    all_results = []
    dimension_configs = {
        ("overall", "total"): ("整体", "整体"),
    }

    # 从 today_rates 动态发现所有维度+桶
    for (dim_key, bucket), data in today_rates.items():
        if dim_key == "overall" and bucket == "total":
            dim_label = "整体"
            bucket_label = "整体"
        elif dim_key == "product_type":
            dim_label = "产品类型"
            bucket_label = bucket
        elif dim_key == "order_type":
            dim_label = "包体"
            bucket_label = bucket
        elif dim_key == "order_grade":
            dim_label = "订单风控等级"
            bucket_label = bucket
        else:
            dim_label = dim_key
            bucket_label = bucket

        # 样本过滤
        due = data.get("due", 0.0)
        cases = data.get("cases", 0)
        is_small = _is_sample_too_small(due, cases)

        # 计算比较
        comparisons = _compute_comparisons(
            dim_key, bucket, today_rates, historical_rates,
            stage, country_code
        )

        # 历史值
        hist_values = all_historical_values.get((dim_key, bucket), {})

        result = TrendResult(
            dimension=dim_key,
            bucket=bucket,
            dim_label=dim_label,
            bucket_label=bucket_label,
            stage=stage,
            current_value=data.get("value", 0.0),
            due_amount=due,
            pay_amount=data.get("pay", 0.0),
            case_count=cases,
            comparisons=comparisons,
            historical_values=dict(sorted(hist_values.items(), reverse=True)),
            is_sample_small=is_small,
            warnings=[],
        )

        # 样本太小 → 所有比较强制 GREEN
        if is_small:
            result.overall_judgment = "GREEN"
            result.overall_icon = v1_config.ALERT_DISPLAY["GREEN"]["icon"]
            result.overall_label = v1_config.ALERT_DISPLAY["GREEN"]["label"]
            result.warnings.append(f"样本量不足（到期本金 {due:,.0f} < {v2_config.MIN_AMOUNT:,}）")

        # 数据不足警告
        if len(valid_historical) < v2_config.MIN_LOOKBACK_DAYS:
            result.warnings.append(
                f"历史数据不足（仅 {len(valid_historical)} 天 < {v2_config.MIN_LOOKBACK_DAYS} 天），"
                f"趋势判断可能不准确"
            )

        all_results.append(result)

    # ---- 汇总 ----
    rank = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
    worst = "GREEN"
    anomaly_count = 0
    for r in all_results:
        if r.overall_judgment != "GREEN":
            anomaly_count += 1
        if rank.get(r.overall_judgment, 0) > rank.get(worst, 0):
            worst = r.overall_judgment

    # 构建索引
    index = defaultdict(dict)
    for r in all_results:
        index[r.dimension][r.bucket] = r

    report = TrendReport(
        country_code=country_code,
        country_name=country["name"],
        business_date=business_date,
        stage=stage,
        run_date=run_date,
        results=all_results,
        _index=dict(index),
        total_dimensions=len(all_results),
        anomaly_count=anomaly_count,
        worst_overall=worst,
    )

    # ---- 打印摘要 ----
    print(f"\n  📊 趋势分析完成")
    print(f"  维度数: {report.total_dimensions}")
    print(f"  异常数: {anomaly_count}")
    print(f"  综合等级: {v1_config.ALERT_DISPLAY[worst]['icon']} {worst}")
    if anomaly_count > 0:
        print(f"\n  异常维度:")
        for r in report.get_anomalies():
            print(f"    {r.dim_label}·{r.bucket_label}: {r.current_value:.2%} "
                  f"({r.overall_icon} {r.overall_label})")
            for c in r.comparisons:
                if c.alert_level != "GREEN":
                    sign = "+" if c.change_abs >= 0 else ""
                    print(f"      {c.method_label}: {sign}{c.change_abs:.4f} "
                          f"(基线 {c.baseline_value:.4f}) {c.alert_icon}")

    return report


# ============================================================
#  便捷函数
# ============================================================

def print_trend_report(report: TrendReport):
    """
    完整打印 TrendReport（终端友好格式）。

    用于调试和 --dry-run 模式。
    """
    rank_order = {"RED": 0, "ORANGE": 1, "YELLOW": 2, "GREEN": 3}

    print(f"\n{'='*70}")
    print(f"  V2 TrendReport — {report.country_name} ({report.country_code})")
    print(f"  业务日期: {report.business_date}  |  阶段: {report.stage}")
    print(f"  运行日期: {report.run_date}")
    print(f"{'='*70}")

    # 按严重程度排序
    sorted_results = sorted(
        report.results,
        key=lambda r: (rank_order.get(r.overall_judgment, 99), r.dim_label, r.bucket_label)
    )

    for r in sorted_results:
        # 样本太小或 GREEN 的跳过（简洁输出）
        if r.is_sample_small:
            print(f"\n  {r.dim_label}·{r.bucket_label}  ⚠️ 样本不足（到期本金 {r.due_amount:,.0f}）")
            continue

        print(f"\n{'─'*70}")
        print(f"  {r.dim_label}·{r.bucket_label}  首逾率: {r.current_value:.2%}  "
              f"到期本金: {r.due_amount:,.0f}  到期笔数: {r.case_count}  "
              f"{r.overall_icon} {r.overall_label}")

        for c in r.comparisons:
            arrow = _trend_arrow(c.change_abs)
            sign = "+" if c.change_abs >= 0 else ""
            print(f"    {c.alert_icon} {c.method_label:12s}: "
                  f"当前 {c.current_value:.4f}  |  基线 {c.baseline_value:.4f}  |  "
                  f"{arrow} {sign}{c.change_abs:.4f} ({sign}{c.change_pct:.1%})  "
                  f"[阈值: {c.threshold_used:.4f}pp]")

        if r.warnings:
            for w in r.warnings:
                print(f"    ⚠️ {w}")

    # 历史趋势摘要
    print(f"\n{'='*70}")
    print(f"  历史趋势（整体，{report.stage}）")
    print(f"{'─'*70}")
    overall = report.get("overall", "total")
    if overall and overall.historical_values:
        for date_str, val in sorted(overall.historical_values.items(), reverse=True):
            bar = _spark_bar(val, 0.20, 0.50)
            print(f"    {date_str}: {val:.2%}  {bar}")

    # 汇总
    print(f"\n{'='*70}")
    print(f"  {report.summary()}")
    print(f"{'='*70}\n")


def _trend_arrow(change_abs: float) -> str:
    """变化方向箭头"""
    if change_abs > 0.0005:
        return "↑"
    elif change_abs < -0.0005:
        return "↓"
    else:
        return "→"


def _spark_bar(value: float, low: float, high: float, width: int = 20) -> str:
    """简易 sparkline bar"""
    if high <= low:
        return ""
    pct = max(0.0, min(1.0, (value - low) / (high - low)))
    filled = int(round(pct * width))
    return "█" * filled + "░" * (width - filled)
