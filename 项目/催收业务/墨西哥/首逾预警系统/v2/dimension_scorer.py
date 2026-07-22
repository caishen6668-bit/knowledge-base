"""
多维度独立监控引擎 — Dimension Scorer

核心设计：
  1. 遍历 TrendReport 中所有 TrendResult（每个维度+分桶）
  2. 每个维度独立判断异常（不依赖 Overall）
  3. 计算 Risk Score 用于跨维度排序
  4. 选择 Top 3 风险最高的维度

不修改: trend_engine.py / root_cause.py / action_engine.py / V1
"""

from typing import List, Tuple, Optional, Dict, Set
from datetime import datetime, timedelta
from . import config as v2_config
from .models import (
    TrendReport, TrendResult, TrendComparison,
    DimensionAnomaly, DimensionScoreBreakdown,
    MultiDimAlertDecision, SimpleAction, AppBreakdown,
)


# ============================================================
#  配置解析
# ============================================================

def _resolve_dimension_config(dimension: str, country_code: str) -> dict:
    """解析某个维度在某国家的异常阈值配置。

    优先级: dimension.country > dimension > default
    """
    dim_cfg = v2_config.DIMENSION_ANOMALY_CONFIG

    # 1. 检查维度是否有 country-specific 配置
    if dimension in dim_cfg:
        sub = dim_cfg[dimension]
        # 判断是否包含 country keys (MX/AR) 还是直接是阈值
        if "MX" in sub or "AR" in sub:
            # country-specific 配置
            if country_code in sub:
                return sub[country_code]
            # fallback: 取第一个国家的配置
            return next(iter(sub.values()))
        elif "min_conditions" in sub:
            # 直接是阈值配置
            return sub

    # 2. 回退到 default
    return dict(dim_cfg.get("default", {
        "min_conditions": 2,
        "dod_worsening_pp": 0.010,
        "avg3d_worsening_pp": 0.008,
        "avg7d_worsening_pp": 0.006,
    }))


def _resolve_sample_min(dimension: str) -> dict:
    """解析某个维度的样本量最小阈值"""
    sample_cfg = v2_config.DIMENSION_SAMPLE_MIN
    if dimension in sample_cfg:
        return sample_cfg[dimension]
    return sample_cfg.get("default", {"min_cases": 30, "min_amount": 50_000})


# ============================================================
#  比较值提取
# ============================================================

def _extract_comparison_changes(trend_result: TrendResult) -> Dict[str, float]:
    """从 TrendResult.comparisons 提取各方法的 change_abs（百分点，小数）。

    Returns: {"dod": 0.0214, "3d_avg": 0.0150, "7d_avg": 0.0120, "target": 0.0250}
    缺失的方法 → 0.0
    """
    changes = {"dod": 0.0, "3d_avg": 0.0, "7d_avg": 0.0, "target": 0.0}
    for comp in trend_result.comparisons:
        if comp.method in changes:
            changes[comp.method] = comp.change_abs
    return changes


def _get_worst_alert_level(trend_result: TrendResult) -> str:
    """获取 TrendResult 中最严重的 alert_level"""
    rank = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
    worst = "GREEN"
    for comp in trend_result.comparisons:
        if rank.get(comp.alert_level, 0) > rank.get(worst, 0):
            worst = comp.alert_level
    return worst


# ============================================================
#  样本量过滤
# ============================================================

def _check_sample_size(trend_result: TrendResult, dimension: str) -> Tuple[bool, str]:
    """检查样本量是否足够。

    Returns: (is_too_small, reason)
    - BOTH case_count AND due_amount 低于阈值 → too small
    - 任一满足 → OK
    """
    sample_min = _resolve_sample_min(dimension)
    min_cases = sample_min.get("min_cases", 30)
    min_amount = sample_min.get("min_amount", 50_000)

    cases_ok = trend_result.case_count >= min_cases
    amount_ok = trend_result.due_amount >= min_amount

    if not cases_ok and not amount_ok:
        return True, (
            f"样本过小（笔数 {trend_result.case_count} < {min_cases} "
            f"且 金额 {trend_result.due_amount:,.0f} < {min_amount:,.0f}）"
        )

    # 如果 TrendResult 本身标记了 is_sample_small，也尊重
    if trend_result.is_sample_small:
        return True, "trend_engine 标记为样本过小"

    return False, ""


# ============================================================
#  Risk Score 计算
# ============================================================

def compute_risk_score(
    conditions_met: int,
    dod_change: float,
    avg3d_change: float,
    avg7d_change: float,
    worst_level: str,
    thresholds: dict,
) -> Tuple[float, DimensionScoreBreakdown]:
    """计算单个维度的风险评分（0-100）。

    三部分组成:
      conditions_score (0-50): 条件通过数 / 3 × 50
      magnitude_score  (0-30): 平均变化幅度 / 阈值 / cap × 30
      severity_score   (0-20): alert_level / 20 × 20

    thresholds: {"dod_worsening_pp": 0.015, "avg3d_worsening_pp": 0.012, ...}
    """
    weights = v2_config.RISK_SCORE_CONFIG["weights"]
    cap = v2_config.RISK_SCORE_CONFIG["magnitude_cap_multiplier"]

    # ---- 1. Conditions Score (0-50) ----
    conditions_score = (conditions_met / 3.0) * weights["conditions"]

    # ---- 2. Magnitude Score (0-30) ----
    # 对每个有变化的方法，计算 actual / threshold 的比率
    ratios = []
    method_thresholds = [
        ("dod", dod_change, thresholds.get("dod_worsening_pp", 0)),
        ("3d_avg", avg3d_change, thresholds.get("avg3d_worsening_pp", 0)),
        ("7d_avg", avg7d_change, thresholds.get("avg7d_worsening_pp", 0)),
    ]

    for _method, change, threshold in method_thresholds:
        if change > 0 and threshold > 0:
            ratios.append(change / threshold)

    if ratios:
        avg_ratio = sum(ratios) / len(ratios)
    else:
        avg_ratio = 0.0

    normalized_magnitude = min(avg_ratio / cap, 1.0)
    magnitude_score = normalized_magnitude * weights["magnitude"]

    # ---- 3. Severity Score (0-20) ----
    level_map = {"GREEN": 0, "YELLOW": 5, "ORANGE": 13, "RED": 20}
    raw_severity = level_map.get(worst_level, 0)
    severity_score = (raw_severity / 20.0) * weights["severity"]

    total = conditions_score + magnitude_score + severity_score

    breakdown = DimensionScoreBreakdown(
        conditions_score=round(conditions_score, 1),
        magnitude_score=round(magnitude_score, 1),
        severity_score=round(severity_score, 1),
        total=round(total, 1),
    )

    return round(total, 1), breakdown


# ============================================================
#  主入口: 对所有维度评分
# ============================================================

def score_all_dimensions(
    trend_report: TrendReport,
    country_code: str,
) -> List[DimensionAnomaly]:
    """遍历 TrendReport 中所有 TrendResult，对每个维度独立评分。

    Args:
        trend_report: Phase 1 趋势分析输出
        country_code: "MX" | "AR"

    Returns:
        所有维度的 DimensionAnomaly 列表（含 GREEN 的），供后续 Top 3 选择
    """
    anomalies: List[DimensionAnomaly] = []

    for tr in trend_report.results:
        dimension = tr.dimension
        bucket = tr.bucket

        # ---- 提取变化值 ----
        changes = _extract_comparison_changes(tr)
        dod_change = changes["dod"]
        avg3d_change = changes["3d_avg"]
        avg7d_change = changes["7d_avg"]
        target_dev = changes["target"]

        # ---- 目标值 ----
        target_value = 0.0
        target_rates = v2_config.TARGET_RATES.get(country_code, {})
        target_value = target_rates.get(tr.stage, 0.0)

        # ---- 解析配置 ----
        cfg = _resolve_dimension_config(dimension, country_code)
        min_conditions = cfg.get("min_conditions", 2)
        dod_threshold = cfg.get("dod_worsening_pp", 0.010)
        avg3d_threshold = cfg.get("avg3d_worsening_pp", 0.008)
        avg7d_threshold = cfg.get("avg7d_worsening_pp", 0.006)

        # ---- 判断条件 ----
        dod_pass = dod_change > 0 and dod_change >= dod_threshold
        avg3d_pass = avg3d_change > 0 and avg3d_change >= avg3d_threshold
        avg7d_pass = avg7d_change > 0 and avg7d_change >= avg7d_threshold
        conditions_met = sum([dod_pass, avg3d_pass, avg7d_pass])

        # ---- 样本量过滤 ----
        is_small, skip_reason = _check_sample_size(tr, dimension)

        # ---- 异常判断 ----
        is_anomalous = (not is_small) and (conditions_met >= min_conditions)

        # ---- 最严重的 alert_level ----
        worst_level = _get_worst_alert_level(tr)
        from .. import config as v1_config
        worst_icon = v1_config.ALERT_DISPLAY.get(worst_level, {}).get("icon", "")

        # ---- Risk Score ----
        risk_score, score_detail = compute_risk_score(
            conditions_met=conditions_met,
            dod_change=dod_change,
            avg3d_change=avg3d_change,
            avg7d_change=avg7d_change,
            worst_level=worst_level,
            thresholds=cfg,
        )

        # ---- 建议动作 ----
        # 如果维度异常，即使 DoD 是负的（今日小幅改善），也要给出动作建议
        # 因为 3d/7d 可能仍在恶化
        action = suggest_simple_action(dimension, bucket, dod_change > 0, is_anomalous)

        # ---- 保存原始 key（业务模型映射前） ----
        orig_dimension = dimension
        orig_bucket = bucket

        # ---- 构建 ----
        anomaly = DimensionAnomaly(
            dimension=dimension,
            bucket=bucket,
            dim_label=tr.dim_label or _dim_label(dimension),
            bucket_label=tr.bucket_label or bucket,
            current_rate=tr.current_value,
            due_amount=tr.due_amount,
            case_count=tr.case_count,
            dod_pass=dod_pass,
            avg3d_pass=avg3d_pass,
            avg7d_pass=avg7d_pass,
            conditions_met=conditions_met,
            min_conditions=min_conditions,
            dod_change_pp=dod_change,
            avg3d_change_pp=avg3d_change,
            avg7d_change_pp=avg7d_change,
            target_deviation_pp=target_dev,
            target_value=target_value,
            risk_score=risk_score,
            risk_breakdown=score_detail.summary() if score_detail else "",
            score_detail=score_detail,
            worst_alert_level=worst_level,
            worst_alert_icon=worst_icon,
            is_sample_small=is_small,
            is_anomalous=is_anomalous,
            skip_reason=skip_reason if not is_anomalous else "",
            suggested_action=action,
        )
        anomalies.append(anomaly)

    # ---- 聚合父级维度（分期产品 = 借款分期 + 展期分期 + 展期N期） ----
    anomalies = _aggregate_parent_dimensions(anomalies, country_code, trend_report)

    # ---- 应用业务模型映射（合并、层级、显示路径） ----
    anomalies = _apply_business_model(anomalies)

    return anomalies


# ============================================================
#  Top 3 选择（含层级去重）
# ============================================================

def _is_parent_child_conflict(a: DimensionAnomaly, b: DimensionAnomaly) -> bool:
    """检查两个 DimensionAnomaly 是否存在父子冲突。

    规则：
      - 直接父子：a.parent_key == f"{b.dimension}:{b.bucket}" 或反之
      - order_grade（parent_key="__any_product__"）与任何非 grade 维度冲突
    """
    # 直接父子关系
    if a.parent_key and a.parent_key == f"{b.dimension}:{b.bucket}":
        return True
    if b.parent_key and b.parent_key == f"{a.dimension}:{a.bucket}":
        return True

    # order_grade 与产品/分期子类型冲突（grade 是产品下的子层级）
    if a.parent_key == "__any_product__" and b.dimension != "order_grade":
        return True
    if b.parent_key == "__any_product__" and a.dimension != "order_grade":
        return True

    return False


def _select_with_dedup(
    candidates: List[DimensionAnomaly],
    max_count: int = 3,
) -> List[DimensionAnomaly]:
    """贪心选择 Top N，自动跳过父子冲突。

    当父子同时出现时，优先保留父节点（更宏观的业务口径），
    因为父节点是聚合后的业务实体，与 BI 口径一致。
    子节点的详情（如具体子类型、APP）在卡片正文中展示。
    """
    result: List[DimensionAnomaly] = []
    for c in candidates:
        conflicts = [r for r in result if _is_parent_child_conflict(r, c)]
        if conflicts:
            # 优先保留父节点（level 更小 = 更宏观）
            # level: 0=overall, 1=产品分类, 2=分期子类型, 3=风控等级
            c_is_parent = all(c.level < r.level for r in conflicts)
            c_is_child = all(c.level > r.level for r in conflicts)
            if c_is_parent:
                # candidate 是父节点 → 替换掉已选中的子节点
                result = [r for r in result if r not in conflicts]
                result.append(c)
            elif c_is_child:
                # candidate 是子节点 → 跳过（保留已选中的父节点）
                pass
            else:
                # 同级或无法判断 → 保留 risk_score 更高的
                if c.risk_score > max(r.risk_score for r in conflicts):
                    result = [r for r in result if r not in conflicts]
                    result.append(c)
        else:
            result.append(c)
        if len(result) >= max_count:
            break
    return result


def select_top3(
    anomalies: List[DimensionAnomaly],
) -> Tuple[Optional[DimensionAnomaly], List[DimensionAnomaly], int, int]:
    """从所有维度异常中选择 Top 3 展示（含层级去重）。

    规则:
      - 排除 is_merged_away=True 和 is_sample_small=True 的维度
      - 父子维度不同时出现（保留 risk_score 更高的）
      - Overall 异常 → [overall] + Top 2 segments
      - Overall 正常 → Top 3 segments
      - 无异常 → 空

    Returns:
        (overall_anomaly, top3_segments, total_anomalies, truncated_count)
    """
    # 分离 overall 和子维度
    overall = None
    segments = []

    for a in anomalies:
        if a.dimension == "overall" and a.bucket == "total":
            overall = a
        elif a.is_anomalous and not a.is_merged_away and not a.is_sample_small:
            segments.append(a)

    # 按 risk_score 降序
    segments.sort(key=lambda x: x.risk_score, reverse=True)

    total_anomalies = len(segments)

    # 贪心选择 Top 3（含父子去重）
    selected = _select_with_dedup(segments, max_count=3)

    # Overall 是否异常
    overall_anomalous = overall is not None and overall.is_anomalous

    if overall_anomalous:
        # Overall 异常 + Top 2 子维度（从 selected 中取前 2）
        top3 = selected[:2]
        truncated = max(0, total_anomalies - len(selected))
        return overall, top3, total_anomalies, truncated
    else:
        # Overall 正常 → Top 3 子维度
        top3 = selected[:3]
        truncated = max(0, total_anomalies - len(selected))
        return (None if not overall_anomalous else overall,
                top3, total_anomalies, truncated)


# ============================================================
#  父级维度聚合 — 分期产品 = 借款分期 + 展期分期 + 展期N期
# ============================================================

def _aggregate_parent_dimensions(
    anomalies: List[DimensionAnomaly],
    country_code: str,
    trend_report: 'TrendReport' = None,
) -> List[DimensionAnomaly]:
    """将子维度数据聚合到父级维度。

    当前聚合:
      - "分期产品" (product_type="分期") ← 借款分期 + 展期分期 + 展期N期

    聚合方式（与整体/BI 口径完全一致）:
      1. 每天独立聚合: 1 - Σ累计回款 / Σ到期本金（原始公式）
      2. 从聚合后的日序列计算 DoD / 3d_avg / 7d_avg
      3. 不对子节点的变化值做加权平均

    历史日期的子维度回款由 rate 反推（pay = due × (1-rate)），
    到期本金使用当日权重（近似，与整体简化口径一致）。
    """
    # ---- 查找父级 ----
    parent = None
    children = []
    for a in anomalies:
        if a.dimension == "product_type" and a.bucket == "分期":
            parent = a
        elif a.dimension == "order_type" and a.bucket in ("借款分期", "展期分期", "展期N期"):
            children.append(a)

    if parent is None or not children:
        return anomalies

    valid_children = [c for c in children if c.due_amount > 0]
    if not valid_children:
        return anomalies

    # ---- 聚合今日到期本金 ----
    total_due = sum(c.due_amount for c in valid_children)
    total_cases = sum(c.case_count for c in valid_children)

    # ---- Step 1: 每天独立聚合首逾率 ----
    # 从 TrendResult.historical_values 重建每个子维度的日序列
    child_daily_rates: Dict[str, Dict[str, float]] = {}  # {bucket: {date: rate}}
    all_dates: Set[str] = set()

    business_date = trend_report.business_date if trend_report else ""

    for child in valid_children:
        rates_by_date: Dict[str, float] = {}

        if trend_report is not None:
            tr = trend_report.get(child.dimension, child.bucket)
            if tr is not None:
                # 今日: current_value
                rates_by_date[business_date] = tr.current_value
                # 历史: historical_values
                for date_str, rate in tr.historical_values.items():
                    rates_by_date[date_str] = rate
        else:
            # 无 trend_report 时的降级: 仅用今日 rate 反推昨日
            rates_by_date[business_date] = child.current_rate
            yesterday_rate = child.current_rate - child.dod_change_pp
            yesterday_date = (
                datetime.strptime(business_date, "%Y-%m-%d") - timedelta(days=1)
            ).strftime("%Y-%m-%d") if business_date else "yesterday"
            rates_by_date[yesterday_date] = yesterday_rate

        child_daily_rates[child.bucket] = rates_by_date
        all_dates.update(rates_by_date.keys())

    if not all_dates:
        return anomalies

    # ---- Step 2: 每天按 1 - Σpay/Σdue 聚合 ----
    sorted_dates = sorted(all_dates, reverse=True)  # 最新在前
    agg_daily_rates: Dict[str, float] = {}  # {date: aggregated_rate}

    for date_str in sorted_dates:
        day_total_due = 0.0
        day_total_pay = 0.0
        for child in valid_children:
            rates = child_daily_rates.get(child.bucket, {})
            if date_str in rates:
                rate = rates[date_str]
                due = child.due_amount  # 使用今日到期本金作为权重（近似）
                day_total_due += due
                day_total_pay += due * (1.0 - rate)
        if day_total_due > 0:
            agg_daily_rates[date_str] = 1.0 - (day_total_pay / day_total_due)

    if not agg_daily_rates:
        return anomalies

    # ---- Step 3: 从聚合日序列计算变化值 ----
    today_date = sorted_dates[0]
    agg_rate_today = agg_daily_rates.get(today_date, parent.current_rate)

    # --- DoD: today vs yesterday ---
    hist_dates = sorted_dates[1:]  # 不含今日
    if hist_dates and hist_dates[0] in agg_daily_rates:
        agg_dod = agg_rate_today - agg_daily_rates[hist_dates[0]]
    else:
        agg_dod = parent.dod_change_pp  # 无历史数据，保留原值

    # --- 3d_avg: today vs avg(yesterday, day-2, day-3) ---
    recent_3_rates = [agg_daily_rates[d] for d in hist_dates[:3] if d in agg_daily_rates]
    if len(recent_3_rates) >= 2:
        agg_3d = agg_rate_today - (sum(recent_3_rates) / len(recent_3_rates))
    else:
        agg_3d = parent.avg3d_change_pp

    # --- 7d_avg: today vs avg(yesterday ... day-7) ---
    recent_7_rates = [agg_daily_rates[d] for d in hist_dates[:7] if d in agg_daily_rates]
    if len(recent_7_rates) >= 3:
        agg_7d = agg_rate_today - (sum(recent_7_rates) / len(recent_7_rates))
    else:
        agg_7d = parent.avg7d_change_pp

    # Target deviation: recalculate from aggregated rate
    agg_target = agg_rate_today - parent.target_value if parent.target_value > 0 else parent.target_deviation_pp

    # ---- 更新父级 ----
    parent.current_rate = agg_rate_today
    parent.due_amount = total_due
    parent.case_count = total_cases
    parent.dod_change_pp = agg_dod
    parent.avg3d_change_pp = agg_3d
    parent.avg7d_change_pp = agg_7d
    parent.target_deviation_pp = agg_target

    # ---- 重新计算条件通过状态 ----
    cfg = _resolve_dimension_config("product_type", country_code)
    dod_threshold = cfg.get("dod_worsening_pp", 0.010)
    avg3d_threshold = cfg.get("avg3d_worsening_pp", 0.008)
    avg7d_threshold = cfg.get("avg7d_worsening_pp", 0.006)
    min_conditions = cfg.get("min_conditions", 2)

    parent.dod_pass = agg_dod > 0 and agg_dod >= dod_threshold
    parent.avg3d_pass = agg_3d > 0 and agg_3d >= avg3d_threshold
    parent.avg7d_pass = agg_7d > 0 and agg_7d >= avg7d_threshold
    parent.conditions_met = sum([parent.dod_pass, parent.avg3d_pass, parent.avg7d_pass])
    parent.is_anomalous = parent.conditions_met >= min_conditions

    # ---- 重新计算 Risk Score ----
    risk_score, score_detail = compute_risk_score(
        conditions_met=parent.conditions_met,
        dod_change=agg_dod,
        avg3d_change=agg_3d,
        avg7d_change=agg_7d,
        worst_level=parent.worst_alert_level,
        thresholds=cfg,
    )
    parent.risk_score = risk_score
    parent.score_detail = score_detail
    parent.risk_breakdown = score_detail.summary() if score_detail else ""

    return anomalies


# ============================================================
#  业务模型映射 — 维度树、合并、显示路径
# ============================================================

def _build_hierarchy_path(a: DimensionAnomaly) -> str:
    """根据 level 和标签构建层级显示路径。

    - Level 0: "整体"
    - Level 1: "非分期产品" / "分期产品"
    - Level 2: "分期产品 · 借款分期"
    - Level 3: "Grade D"（风控等级 — 无法确定具体父节点时不加前缀）
    """
    if a.level == 0:
        return "整体"
    elif a.level == 1:
        return a.bucket_label
    elif a.level == 2:
        # 分期子类型: 显示为 "分期产品 · 借款分期"
        return f"{a.dim_label} · {a.bucket_label}"
    elif a.level == 3:
        # 风控等级: 仅显示 grade 名称
        return a.bucket_label
    return f"{a.dim_label} · {a.bucket_label}"


def _apply_business_model(
    anomalies: List[DimensionAnomaly],
) -> List[DimensionAnomaly]:
    """应用业务模型映射：合并重复维度、设置层级、构建显示路径。

    处理:
      1. ("order_type", "非分期") → is_merged_away=True（合并到 "非分期产品"）
      2. ("product_type", "单期") → "非分期产品", level=1
      3. ("product_type", "分期") → "分期产品", level=1
      4. ("order_type", "借款分期"等) → "分期产品 · 借款分期", level=2, parent="分期产品"
      5. ("order_grade", *) → level=3, parent_key="__any_product__"

    不修改 trend_engine.py — 仅在下游做映射。
    """
    model = v2_config.BUSINESS_MODEL
    key_map = model.get("key_map", {})
    merged_keys = model.get("merged_keys", [])

    for a in anomalies:
        key = (a.dimension, a.bucket)

        # ---- 1. 检查是否被合并 ----
        if key in merged_keys:
            a.is_merged_away = True
            a.level = -1  # 标记为无效
            continue

        # ---- 2. 查找业务模型映射 ----
        if key in key_map:
            mapping = key_map[key]
            a.dim_label = mapping["dim_label"]
            a.bucket_label = mapping["bucket_label"]
            a.level = mapping["level"]
            a.parent_key = mapping["parent_key"]
        elif a.dimension == "order_grade":
            # 风控等级: level=3, 父节点标记为任意产品（用于去重）
            a.level = 3
            a.parent_key = "__any_product__"
        elif a.dimension == "overall":
            a.level = 0
            a.parent_key = ""
        else:
            # 未知维度: 保持原样
            a.level = a.level or 0
            a.parent_key = a.parent_key or ""

        # ---- 3. 构建 hierarchy_path ----
        a.hierarchy_path = _build_hierarchy_path(a)

    return anomalies


# ============================================================
#  轻量动作建议
# ============================================================

def suggest_simple_action(
    dimension: str,
    bucket: str,
    is_worsening: bool = True,
    is_anomalous: bool = False,
) -> str:
    """基于维度和分桶生成一行动作建议。

    不修改 action_engine.py，仅用于子维度独立告警。

    Args:
        is_worsening: DoD > 0（今日较昨日恶化）
        is_anomalous: 维度是否被判定为异常
    """
    # 先确定基础动作
    base_action = _lookup_action(dimension, bucket)

    # DoD 改善中 → 简短的改善确认
    if not is_worsening and not is_anomalous:
        return "保持观察（趋势改善中）"

    # 维度异常但今日 DoD 小幅改善 → 基础动作 + 注意说明
    if is_anomalous and not is_worsening:
        return f"{base_action}（今日小幅改善，但近3日/7日均值仍在高位）"

    # DoD 恶化 → 基础动作
    return base_action


def _lookup_action(dimension: str, bucket: str) -> str:
    """根据维度和分桶查找基础动作。"""
    action_map = v2_config.SIMPLE_ACTION_MAP

    # 1. order_grade: A/B → grade_A_B, C/D → grade_C_D, E/F → grade_E_F
    if dimension == "order_grade":
        if bucket in ("A", "B"):
            entry = action_map.get("grade_A_B")
        elif bucket in ("C", "D"):
            entry = action_map.get("grade_C_D")
        elif bucket in ("E", "F"):
            entry = action_map.get("grade_E_F")
        else:
            entry = action_map.get("default")
        if entry:
            return entry.get("action", "持续监控")

    # 2. product_type
    if dimension == "product_type":
        key = f"product_{bucket}"
        entry = action_map.get(key)
        if entry:
            return entry.get("action", "持续监控")

    # 3. order_type
    if dimension == "order_type":
        key = f"order_{bucket}"
        entry = action_map.get(key)
        if entry:
            return entry.get("action", "持续监控")

    # 4. overall
    if dimension == "overall":
        return "整体首逾走弱，需排查主要贡献维度"

    # 5. fallback
    default_entry = action_map.get("default", {})
    return default_entry.get("action", "持续监控，确认趋势持续性")


# ============================================================
#  辅助：维度中文标签
# ============================================================

def _dim_label(dimension: str) -> str:
    """维度 key → 中文标签"""
    labels = {
        "overall": "整体",
        "product_type": "产品类型",
        "order_type": "包体",
        "order_grade": "订单风控等级",
    }
    return labels.get(dimension, dimension)


# ============================================================
#  量价分析 — 异常原因判定（放量 vs 质量走弱）
# ============================================================

def _analyze_volume_quality(
    today_cases: int,
    yesterday_cases: int,
    dod_change_pp: float,
) -> str:
    """判断单个 APP 的异常原因：放量驱动 vs 质量驱动。

    阈值：
      - 放量: 到期单量变化 > +30%（显著增加）/ < -20%（显著减少）
      - 质量走弱: DoD > 0.5pp
      - 质量改善: DoD < -0.5pp

    组合：
      - 放量 + 质量走弱 → 双重压力
      - 放量 + 质量稳定 → 放量导致
      - 量稳 + 质量走弱 → 质量走弱
      - 量缩 + 质量走弱 → 量缩但质量恶化（需警惕）
      - 放量 + 质量改善 → 放量但质量可控
      - 量缩 + 质量改善 → 双改善
      - 其他 → 正常波动
    """
    if yesterday_cases > 0:
        case_change_ratio = (today_cases - yesterday_cases) / yesterday_cases
    else:
        case_change_ratio = 0.0 if today_cases == 0 else 1.0  # 昨天0今天有 → 算放量

    VOL_UP = 0.3       # 单量增加 >30%
    VOL_DOWN = -0.2    # 单量减少 >20%
    QUAL_WORSE = 0.005  # 首逾率上升 >0.5pp
    QUAL_BETTER = -0.005

    vol_up = case_change_ratio > VOL_UP
    vol_down = case_change_ratio < VOL_DOWN
    qual_worse = dod_change_pp > QUAL_WORSE
    qual_better = dod_change_pp < QUAL_BETTER

    if vol_up and qual_worse:
        return "放量 + 质量走弱"
    elif vol_up and qual_better:
        return "放量但质量改善"
    elif vol_up:
        return "放量导致"
    elif vol_down and qual_worse:
        return "量缩但质量走弱 ⚠️"
    elif vol_down and qual_better:
        return "量缩且质量改善"
    elif vol_down:
        return "量缩"
    elif qual_worse:
        return "质量走弱"
    elif qual_better:
        return "质量改善"
    else:
        return "正常波动"


# ============================================================
#  APP 下钻 — 每个异常维度展示主要贡献 APP（纯展示）
# ============================================================

def compute_app_breakdowns(
    anomalies: List[DimensionAnomaly],
    country_code: str,
    business_date: str,
    stage: str,
) -> None:
    """为每个异常维度附加 Top 2 贡献 APP。

    从 raw_rows 中按维度+bucket 过滤，按 APP 聚合首逾率，
    计算 DoD 变化，取影响最大的前 2 个 APP。

    纯展示信息 — 不参与 Risk Score、不参与 should_alert、不修改任何算法。

    Args:
        anomalies: DimensionAnomaly 列表（原地修改 top_apps 字段）
        country_code: "MX" | "AR"
        business_date: 业务日期
        stage: 阶段
    """
    from ..quickbi import calculate_first_overdue_rate
    from .trend_engine import _fetch_day_snapshot

    # ---- 拉取今天和昨天的 raw_rows ----
    today_snap = _fetch_day_snapshot(business_date, country_code)
    if not today_snap or not today_snap.get("raw_rows"):
        return

    yesterday_date = (
        datetime.strptime(business_date, "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    yesterday_snap = _fetch_day_snapshot(yesterday_date, country_code)

    today_rows = today_snap["raw_rows"]
    yesterday_rows = yesterday_snap.get("raw_rows", []) if yesterday_snap else []

    # ---- 辅助：按 APP 聚合 ----
    def _agg_by_app(rows: list, stage: str) -> Dict[str, dict]:
        """按 APP 聚合首逾率。返回 {app_name: {rate, due, cases, grades}}"""
        app_agg: Dict[str, dict] = {}
        for r in rows:
            app = r.get("app", "") or r.get("app_name", "")
            if not app:
                continue
            rcalc = calculate_first_overdue_rate(r, stage)
            due = rcalc["due_amt"]
            if due <= 0:
                continue
            if app not in app_agg:
                app_agg[app] = {"due": 0.0, "pay": 0.0, "cases": 0, "grades": {}}
            app_agg[app]["due"] += due
            app_agg[app]["pay"] += rcalc["cum_pay"]
            app_agg[app]["cases"] += int(r.get("due_case", 0) or 0)
            # 按 Grade 聚合单量
            grade = r.get("order_grade", "") or ""
            if grade:
                app_agg[app]["grades"][grade] = app_agg[app]["grades"].get(grade, 0) + int(r.get("due_case", 0) or 0)
        # 计算 rate
        for app, agg in app_agg.items():
            agg["rate"] = 1.0 - (agg["pay"] / agg["due"]) if agg["due"] > 0 else 0.0
        return app_agg

    # ---- 辅助：按维度过滤 rows ----
    def _filter_rows(rows: list, anomaly: DimensionAnomaly) -> list:
        """根据 anomaly 的 dimension+bucket 过滤 raw_rows"""
        dim = anomaly.dimension
        bucket = anomaly.bucket
        if dim == "overall":
            return rows  # 整体：不过滤
        elif dim == "product_type":
            if bucket == "单期":
                return [r for r in rows if r.get("order_type", "") == "非分期"]
            elif bucket == "分期":
                return [r for r in rows if r.get("order_type", "") in ("借款分期", "展期分期", "展期N期")]
        elif dim == "order_type":
            return [r for r in rows if r.get("order_type", "") == bucket]
        elif dim == "order_grade":
            return [r for r in rows if r.get("order_grade", "") == bucket]
        return []

    # ---- 预计算：每个 APP 的主导产品分类（基于今日全部 rows） ----
    app_product_map: Dict[str, str] = {}  # {app_name: "分期产品"|"非分期产品"}
    for r in today_rows:
        app = r.get("app", "") or r.get("app_name", "")
        if not app or app in app_product_map:
            continue
        # 汇总该 APP 的所有订单
        app_rows = [x for x in today_rows if (x.get("app", "") or x.get("app_name", "")) == app]
        fenqi_cases = 0
        feifenqi_cases = 0
        for ar in app_rows:
            ot = ar.get("order_type", "")
            c = int(ar.get("due_case", 0) or 0)
            if ot in ("借款分期", "展期分期", "展期N期"):
                fenqi_cases += c
            elif ot == "非分期":
                feifenqi_cases += c
        app_product_map[app] = "分期产品" if fenqi_cases >= feifenqi_cases else "非分期产品"

    # ---- 为每个 anomaly 附加 APP 信息 ----
    for anomaly in anomalies:
        # 跳过合并的、样本过小的
        if anomaly.is_merged_away:
            continue
        if anomaly.is_sample_small:
            continue

        # 过滤今天和昨天的 rows
        filtered_today = _filter_rows(today_rows, anomaly)
        if not filtered_today:
            continue

        today_apps = _agg_by_app(filtered_today, stage)

        filtered_yesterday = _filter_rows(yesterday_rows, anomaly)
        yesterday_apps = _agg_by_app(filtered_yesterday, stage) if filtered_yesterday else {}

        # ---- 构建 AppBreakdown 列表 ----
        breakdowns: List[AppBreakdown] = []
        for app, agg in today_apps.items():
            today_rate = agg["rate"]
            yesterday_rate = yesterday_apps.get(app, {}).get("rate", today_rate)
            dod = today_rate - yesterday_rate
            # 取 Top 2 Grade（按单量降序）
            grades = sorted(agg.get("grades", {}).items(), key=lambda x: x[1], reverse=True)
            # 昨日数据
            yesterday_data = yesterday_apps.get(app, {})
            yesterday_cases = yesterday_data.get("cases", 0)
            yesterday_rate = yesterday_data.get("rate", today_rate)
            # 量价分析
            vq_label = _analyze_volume_quality(
                today_cases=agg.get("cases", 0),
                yesterday_cases=yesterday_cases,
                dod_change_pp=dod,
            )
            breakdowns.append(AppBreakdown(
                app_name=app,
                current_rate=today_rate,
                dod_change_pp=dod,
                due_amount=agg["due"],
                case_count=agg.get("cases", 0),
                yesterday_case_count=yesterday_cases,
                yesterday_rate=yesterday_rate,
                top_grades=grades[:2],
                volume_quality_label=vq_label,
            ))

        # 按风险程度排序（DoD 走弱优先 → 变化幅度 → 单量），取 Top 2
        # 走弱的 APP 排前面，同方向按变化幅度，幅度接近再按单量
        breakdowns.sort(key=lambda x: (
            1 if x.dod_change_pp > 0 else 0,   # 走弱优先
            abs(x.dod_change_pp),               # 变化幅度
            x.case_count,                        # 单量
        ), reverse=True)
        anomaly.top_apps = breakdowns[:2]

        # ---- 确定产品归属：以 Top APP 的主导产品为准 ----
        if anomaly.top_apps:
            top_app_name = anomaly.top_apps[0].app_name
            anomaly.primary_product = app_product_map.get(top_app_name, "")
        else:
            anomaly.primary_product = ""


# ============================================================
#  工具：打印所有维度评分（调试用）
# ============================================================

def print_dimension_scores(
    anomalies: List[DimensionAnomaly],
    country_code: str,
) -> None:
    """终端打印所有维度的评分结果（调试用途）"""
    print(f"\n{'─'*60}")
    print(f"  多维度独立监控 — 全部维度评分 ({country_code})")
    print(f"{'─'*60}")

    # 按 risk_score 降序，跳过合并的维度
    sorted_anomalies = sorted(
        [a for a in anomalies if not a.is_merged_away],
        key=lambda x: x.risk_score, reverse=True,
    )

    for i, a in enumerate(sorted_anomalies, 1):
        status = "🔴 ANOMALY" if a.is_anomalous else ("⚠️ SMALL" if a.is_sample_small else "🟢 OK")
        title = a.hierarchy_path or a.display_name
        print(
            f"  {i:2d}. {title:<24s} "
            f"Rate={a.current_rate*100:.2f}%  "
            f"DoD={a.dod_change_pp*100:+.2f}pp  "
            f"3d={a.avg3d_change_pp*100:+.2f}pp  "
            f"7d={a.avg7d_change_pp*100:+.2f}pp  "
            f"Cond={a.condition_summary}  "
            f"Risk={a.risk_score:.0f}  "
            f"{status}"
        )
        if a.skip_reason:
            print(f"      ↳ {a.skip_reason}")
        if a.suggested_action:
            print(f"      ↳ 建议: {a.suggested_action}")

    print(f"{'─'*60}")
