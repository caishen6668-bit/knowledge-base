"""
V2 Alert Decision V2 — 整体驱动 + 分国家阈值 + 置信度评分

核心原则: "整体驱动，局部解释"

规则:
  1. 整体趋势必须满足告警条件（DoD / 7日均值 / 高于目标）
     否则 → Root Cause 仅展示，不发送飞书
  2. Root Cause 不再独立触发飞书
     Root Cause 仅负责解释"为什么整体恶化"
  3. 每个国家独立配置阈值（MX / AR）
  4. 业务规模过小的异常默认不发送飞书
  5. Alert Confidence 0~100% 评分体系

V1 vs V2 核心区别:
  V1: 任一条件触发 → SEND（P1 / Impact / 连续恶化 / 高于目标）
  V2: 整体趋势先过关 → 根因仅作解释 → SEND + Confidence 评分

不修改 V1 Alert Decision。不修改 TrendEngine。不修改 RootCauseEngine。不修改 ActionEngine。
"""

from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from .. import config as v1_config
from . import config as v2_config
from .models import (
    TrendReport, TrendResult,
    RootCauseResult, RootCausePath,
    ActionResult, ActionItem,
    AlertDecision,
    DimensionAnomaly, DimensionScoreBreakdown,
    MultiDimAlertDecision, SimpleAction,
)
from .dimension_scorer import (
    score_all_dimensions,
    select_top3,
    suggest_simple_action,
    print_dimension_scores,
)


# ============================================================
#  Step 0: 业务规模检查（硬性过滤）
# ============================================================

def _check_business_scale(
    trend_report: TrendReport,
    country_code: str,
) -> Tuple[bool, float, int]:
    """
    检查业务规模是否过小。规模过小的异常不发送飞书。

    同时满足以下两个条件才算"规模过小":
      - 到期本金 < scale_min_amount
      - 到期笔数 < scale_min_cases

    仅一个不满足 → 不算过小（例如大额但笔数少、小额但笔数多）。

    Returns:
        (skip: bool, due_amount: float, case_count: int)
        skip=True → 规模过小，应跳过告警
    """
    scale_cfg = v2_config.ALERT_BUSINESS_SCALE_MIN.get(country_code, {})
    min_amount = scale_cfg.get("due_amount", 0)
    min_cases = scale_cfg.get("case_count", 0)

    overall = trend_report.get("overall", "total")
    if not overall:
        return True, 0.0, 0  # 无数据 → 跳过

    due_amount = overall.due_amount
    case_count = overall.case_count

    # 两个条件都低于阈值才算过小
    amount_too_small = due_amount < min_amount
    cases_too_few = case_count < min_cases
    skip = amount_too_small and cases_too_few

    return skip, due_amount, case_count


# ============================================================
#  Step 1: 检查整体趋势
# ============================================================

def _check_overall_trend(
    trend_report: TrendReport,
    country_code: str,
) -> Tuple[bool, List[str], dict]:
    """
    检查整体首逾率是否满足告警条件。

    按国家独立阈值检查（AND 逻辑 — 全部满足才触发）:
      - DoD 恶化（较昨日上升 >= dod_worsening_pp）
      - 高于近3日均值（>= avg3d_worsening_pp）
      - 高于近7日均值（>= avg7d_worsening_pp）
      - Target 仅提取展示，不参与 should_alert 判断

    Returns:
        (qualifies: bool, reasons: [str], metrics: dict)
        metrics 中包含各条件独立通过状态和 target 参考值
    """
    cfg = v2_config.ALERT_V2_CONFIG.get(country_code)
    if not cfg:
        return False, [], {}

    overall = trend_report.get("overall", "total")
    if not overall:
        return False, [], {}

    dod_change = 0.0
    avg3d_change = 0.0
    avg7d_change = 0.0
    target_dev = 0.0
    target_value = None
    current_rate = overall.current_value

    # 从比较结果中提取各维度变化
    for c in overall.comparisons:
        if c.method == "dod":
            dod_change = c.change_abs  # 正数 = 恶化
        elif c.method == "3d_avg":
            avg3d_change = c.change_abs
        elif c.method == "7d_avg":
            avg7d_change = c.change_abs
        elif c.method == "target":
            target_dev = c.change_abs
            target_value = c.baseline_value

    # ---- 三个趋势条件（AND 逻辑：全部满足才通过） ----
    dod_pass = dod_change > 0 and dod_change >= cfg["dod_worsening_pp"]
    avg3d_pass = avg3d_change > 0 and avg3d_change >= cfg["avg3d_worsening_pp"]
    avg7d_pass = avg7d_change > 0 and avg7d_change >= cfg["avg7d_worsening_pp"]

    conditions_met = sum([dod_pass, avg3d_pass, avg7d_pass])
    min_conditions = cfg.get("min_conditions", 3)
    qualifies = conditions_met >= min_conditions

    # 构建原因文本
    all_reasons = []
    if qualifies:
        all_reasons.append(
            f"较昨日恶化 {dod_change * 100:+.2f}pp "
            f"(阈值 {cfg['dod_worsening_pp'] * 100:.2f}pp)"
        )
        all_reasons.append(
            f"高于近3日均值 {avg3d_change * 100:+.2f}pp "
            f"(阈值 {cfg['avg3d_worsening_pp'] * 100:.2f}pp)"
        )
        all_reasons.append(
            f"高于近7日均值 {avg7d_change * 100:+.2f}pp "
            f"(阈值 {cfg['avg7d_worsening_pp'] * 100:.2f}pp)"
        )

    # Target 参考值（不参与触发）
    target_note = ""
    if target_dev > 0:
        target_note = (
            f"当前高于目标 {target_dev * 100:+.2f}pp "
            f"({current_rate:.2%} vs {target_value:.2%})"
        )

    metrics = {
        "dod_change": dod_change,
        "avg3d_change": avg3d_change,
        "avg7d_change": avg7d_change,
        "target_dev": target_dev,
        "target_value": target_value,
        "current_rate": current_rate,
        "conditions_met": conditions_met,
        "min_conditions": min_conditions,
        "dod_pass": dod_pass,
        "avg3d_pass": avg3d_pass,
        "avg7d_pass": avg7d_pass,
        "target_note": target_note,
    }

    return qualifies, all_reasons if qualifies else [], metrics


# ============================================================
#  Step 1.5: 连续恶化检查（用于置信度评分）
# ============================================================

def _check_consecutive_worsening(
    trend_report: TrendReport,
    business_date: str,
    country_code: str,
) -> Tuple[int, bool]:
    """
    检查整体首逾率连续恶化天数。

    用于置信度计算，不作为独立告警触发条件（V2）。

    Returns:
        (consecutive_days: int, meets_threshold: bool)
    """
    conf_cfg = v2_config.ALERT_CONFIDENCE_CONFIG.get(country_code, {})
    threshold = conf_cfg.get("consecutive_days", 3)

    overall = trend_report.get("overall", "total")
    if not overall or not overall.historical_values:
        return 0, False

    # 构建日期→首逾率映射
    rates_by_date = dict(overall.historical_values)
    rates_by_date[business_date] = overall.current_value

    sorted_dates = sorted(rates_by_date.keys(), reverse=True)

    if len(sorted_dates) < 2:
        return 0, False

    # 从今天开始，检查连续上升天数
    consecutive = 0
    for i in range(len(sorted_dates) - 1):
        today_rate = rates_by_date[sorted_dates[i]]
        yesterday_rate = rates_by_date[sorted_dates[i + 1]]
        if today_rate > yesterday_rate + 0.0001:
            consecutive += 1
        else:
            break

    meets = consecutive >= threshold
    return consecutive, meets


# ============================================================
#  Step 2: 根因解释（仅当整体通过时）
# ============================================================

def _build_root_cause_explanation(
    root_cause: RootCauseResult,
    action_result: ActionResult,
    country_code: str,
) -> Tuple[str, str, List[str]]:
    """
    构建根因解释文本。

    仅用于告警内容，不作为独立触发条件。

    Returns:
        (explanation: str, trigger_source: str, top_signals: [str])
    """
    cfg = v2_config.ALERT_V2_CONFIG.get(country_code, {})
    min_impact = cfg.get("min_impact_for_mention", 0)

    explanation_parts = []
    top_signals = []

    # 恶化来源（超过影响金额阈值的才展示）
    if root_cause.worsening:
        significant = [p for p in root_cause.worsening
                       if p.impact_amount >= min_impact]
        if significant:
            items = []
            for p in significant[:3]:
                items.append(
                    f"{p.path_label} {p.change_pp * 100:+.2f}pp "
                    f"(影响 {p.impact_amount:,.0f})"
                )
                top_signals.append(p.path_label)
            explanation_parts.append(f"📈 恶化来源: {'; '.join(items)}")
        else:
            explanation_parts.append(
                f"📈 恶化来源: {len(root_cause.worsening)} 项，但均未达影响阈值"
            )

    # 改善来源
    if root_cause.improving:
        significant = [p for p in root_cause.improving
                       if p.impact_amount >= min_impact]
        if significant:
            items = []
            for p in significant[:2]:
                items.append(
                    f"{p.path_label} {p.change_pp * 100:+.2f}pp"
                )
            explanation_parts.append(f"📉 改善来源: {'; '.join(items)}")

    # 触发来源标签
    if root_cause.worsening:
        trigger_source = "RootCause"
    else:
        trigger_source = "Trend"

    explanation = " | ".join(explanation_parts) if explanation_parts else ""
    return explanation, trigger_source, top_signals


# ============================================================
#  Step 3: 置信度计算
# ============================================================

def _calculate_confidence(
    metrics: dict,
    consecutive_meets: bool,
    due_amount: float,
    case_count: int,
    country_code: str,
) -> Tuple[float, str]:
    """
    计算预警置信度（0~100%）。

    四个维度，每个维度满足条件 → 累加对应权重：

    1. Trend (30分): DoD 恶化幅度 ≥ 阈值
    2. Target (25分): 高于目标偏离 ≥ 阈值
    3. Consecutive (20分): 连续恶化天数 ≥ 阈值
    4. Scale (25分): 业务规模足够大（到期本金或到期笔数达标）

    Returns:
        (confidence: float, breakdown: str)
    """
    weights = v2_config.ALERT_CONFIDENCE_CONFIG.get("weights", {})
    conf_cfg = v2_config.ALERT_CONFIDENCE_CONFIG.get(country_code, {})

    w_trend = weights.get("trend", 30)
    w_target = weights.get("target", 25)
    w_consecutive = weights.get("consecutive", 20)
    w_scale = weights.get("scale", 25)

    score = 0.0
    parts = []

    dod_change = metrics.get("dod_change", 0.0)
    target_dev = metrics.get("target_dev", 0.0)

    # 1. Trend: DoD 恶化超过置信度阈值
    trend_pp = conf_cfg.get("trend_dod_pp", 0.010)
    if dod_change > 0 and dod_change >= trend_pp:
        score += w_trend
        parts.append(f"Trend:{w_trend}")
    else:
        parts.append(f"Trend:0")

    # 2. Target: 高于目标超过置信度阈值
    target_pp = conf_cfg.get("target_pp", 0.015)
    if target_dev > 0 and target_dev >= target_pp:
        score += w_target
        parts.append(f"Target:{w_target}")
    else:
        parts.append(f"Target:0")

    # 3. Consecutive: 连续恶化达标
    if consecutive_meets:
        score += w_consecutive
        parts.append(f"Consecutive:{w_consecutive}")
    else:
        parts.append(f"Consecutive:0")

    # 4. Scale: 业务规模达标
    scale_amount = conf_cfg.get("scale_min_amount", 500_000)
    scale_cases = conf_cfg.get("scale_min_cases", 200)
    if due_amount >= scale_amount or case_count >= scale_cases:
        score += w_scale
        parts.append(f"Scale:{w_scale}")
    else:
        parts.append(f"Scale:0")

    breakdown = " + ".join(parts) + f" = {score:.0f}"

    return score, breakdown


# ============================================================
#  Step 4: 统计 Action 信息
# ============================================================

def _determine_action_items(
    action_result: ActionResult,
    country_code: str,
) -> Tuple[int, int, int, float]:
    """统计 P1/P2/P3 和最大影响金额"""
    if not action_result:
        return 0, 0, 0, 0.0

    p1 = len(action_result.p1_actions)
    p2 = len(action_result.p2_actions)
    p3 = len(action_result.p3_actions)

    max_impact = 0.0
    for item in action_result.all_actions:
        if item.impact_amount > max_impact:
            max_impact = item.impact_amount

    return p1, p2, p3, max_impact


# ============================================================
#  主入口
# ============================================================

def decide_alert_v2(
    trend_report: TrendReport,
    root_cause: RootCauseResult,
    action_result: ActionResult,
    country_code: str,
) -> AlertDecision:
    """
    Alert Decision V2 — 整体驱动 + AND 逻辑 + 置信度评分。

    决策流程:
      0. 业务规模检查 → 规模过小直接跳过
      1. 整体趋势三条件（AND 逻辑）:
         DoD 恶化 AND 高于近3日均值 AND 高于近7日均值
         Target 仅展示参考，不参与触发判断
         NO  → should_alert=False, Root Cause 仅展示不发送
         YES → Step 2
      2. 根因分析（仅解释，不独立触发）
      3. 置信度计算（0~100%，四个维度加权）

    Args:
        trend_report: Phase 1 趋势报告
        root_cause: Phase 2 根因定位
        action_result: Phase 3 行动建议
        country_code: "MX" | "AR"

    Returns:
        AlertDecision（含 confidence_score）
    """
    country = v1_config.COUNTRIES[country_code]
    cfg = v2_config.ALERT_V2_CONFIG.get(country_code, {})
    stage = trend_report.stage
    business_date = trend_report.business_date

    # ---- Step 0: 业务规模检查（硬性过滤） ----
    scale_skip, due_amount, case_count = _check_business_scale(
        trend_report, country_code
    )

    # ---- Step 1: 整体趋势检查 ----
    overall_qualifies, overall_reasons, metrics = _check_overall_trend(
        trend_report, country_code
    )

    # ---- Step 1.5: 连续恶化（用于置信度） ----
    consec_days, consec_meets = _check_consecutive_worsening(
        trend_report, business_date, country_code
    )

    # ---- Step 2: 根因解释 ----
    root_explanation = ""
    trigger_source = ""
    top_signals = []

    if root_cause and root_cause.has_root_cause:
        root_explanation, trigger_source, top_signals = _build_root_cause_explanation(
            root_cause, action_result, country_code
        )

    # ---- Step 3: 置信度计算 ----
    confidence, confidence_breakdown = _calculate_confidence(
        metrics, consec_meets, due_amount, case_count, country_code
    )

    # ---- Step 4: 统计 ----
    p1, p2, p3, max_impact = _determine_action_items(action_result, country_code)

    # ---- 决策 ----
    # 规模过小 → 强制不告警
    if scale_skip:
        should_alert = False
        alert_reason = ""
    else:
        should_alert = overall_qualifies
        alert_reason = ", ".join(overall_reasons) if overall_reasons else ""

    # ---- 构建详情 ----
    # 各条件状态（用于展示）
    cond_status = []
    for label, key, pp_key in [
        ("DoD", "dod_pass", "dod_worsening_pp"),
        ("3d", "avg3d_pass", "avg3d_worsening_pp"),
        ("7d", "avg7d_pass", "avg7d_worsening_pp"),
    ]:
        icon = "✅" if metrics.get(key, False) else "❌"
        th = cfg.get(pp_key, 0) * 100 if cfg else 0
        cond_status.append(f"{icon} {label}(≥{th:.2f}pp)")

    cond_summary = "  ".join(cond_status)
    target_note = metrics.get("target_note", "")

    if scale_skip:
        details = (
            f"🔇 跳过飞书告警 — 业务规模过小 "
            f"(到期本金 {due_amount:,.0f} < 阈值, "
            f"到期笔数 {case_count} < 阈值)"
        )
    elif should_alert:
        details = (
            f"📢 发送飞书告警 — 趋势三条件全部满足\n"
            f"   {cond_summary}"
        )
        if target_note:
            details += f"\n   📊 参考: {target_note}"
        if root_explanation:
            details += f"\n   根因: {root_explanation}"
    else:
        details = (
            f"🔇 跳过飞书告警 — 趋势条件未全部满足 "
            f"({metrics.get('conditions_met', 0)}/{metrics.get('min_conditions', 3)})\n"
            f"   {cond_summary}"
        )
        if target_note:
            details += f"\n   📊 参考: {target_note}"
        if root_cause and root_cause.has_root_cause:
            worsening_count = len(root_cause.worsening)
            improving_count = len(root_cause.improving)
            if worsening_count > 0 or improving_count > 0:
                details += (
                    f"\n   （子维度有 {worsening_count} 恶化 / {improving_count} 改善，"
                    f"仅展示不推送）"
                )

    decision = AlertDecision(
        country_code=country_code,
        country_name=country["name"],
        business_date=business_date,
        stage=stage,
        should_alert=should_alert,
        alert_reason=alert_reason,
        trigger_source=trigger_source if should_alert else "",
        p1_count=p1,
        max_impact=max_impact,
        target_deviation_pp=metrics.get("target_dev", 0.0),
        consecutive_worsening_days=consec_days,
        overall_change_pp=metrics.get("dod_change", 0.0),
        overall_alert_level=root_cause.overall_alert_level if root_cause else "GREEN",
        confidence_score=confidence,
        confidence_breakdown=confidence_breakdown,
        due_amount=due_amount,
        case_count=case_count,
        business_scale_skip=scale_skip,
        details=details,
    )

    return decision


# ============================================================
#  打印
# ============================================================

def _confidence_bar(score: float) -> str:
    """置信度可视化进度条"""
    filled = int(score / 10)
    empty = 10 - filled
    bar = "█" * filled + "░" * empty
    if score >= 80:
        color = "🟢"
    elif score >= 50:
        color = "🟡"
    else:
        color = "🔴"
    return f"{color} [{bar}] {score:.0f}%"


def print_alert_decision_v2(decision: AlertDecision, cfg: dict = None):
    """打印 AlertDecision V2 结果"""
    print(f"\n  📋 Alert Decision V2 ({decision.country_name})")
    print(f"  {'─'*55}")

    if cfg:
        print(f"  规则: 3/3 AND 全部满足 → SEND")
        print(f"       DoD≥{cfg['dod_worsening_pp']*100:.2f}pp  &  "
              f"3d≥{cfg['avg3d_worsening_pp']*100:.2f}pp  &  "
              f"7d≥{cfg['avg7d_worsening_pp']*100:.2f}pp")
        print(f"       Target({cfg['target_pp']*100:.2f}pp) 仅展示参考，不参与触发")

    # 业务规模
    print(f"  业务规模: 到期本金 {decision.due_amount:,.0f} | "
          f"到期笔数 {decision.case_count}")
    if decision.business_scale_skip:
        print(f"  ⚠️ 规模过小 — 强制跳过告警")

    status = "📢 SEND" if decision.should_alert else "🔇 SILENT"
    print(f"  决策: {status}")

    # 置信度
    bar = _confidence_bar(decision.confidence_score)
    print(f"  置信度: {bar}")
    print(f"          {decision.confidence_breakdown}")

    if decision.should_alert:
        print(f"  原因: {decision.alert_reason}")
        print(f"  P1={decision.p1_count} | 最大影响={decision.max_impact:,.0f}")
    print(f"  {decision.details}")


# ============================================================
#  多维度独立监控 — decide_alert_multi_dim
# ============================================================
# 新入口，替代 decide_alert_v2。
# 不再依赖"整体驱动"，而是每个维度独立判断异常。
# Root Cause / Action 可选增强。

def decide_alert_multi_dim(
    trend_report: TrendReport,
    country_code: str,
) -> MultiDimAlertDecision:
    """多维度独立监控告警决策。

    仅依赖 TrendReport（Phase 1），不依赖 Root Cause / Action。
    每个维度（overall, product_type, order_type, order_grade）独立评分。

    Args:
        trend_report: Phase 1 趋势分析完整报告
        country_code: "MX" | "AR"

    Returns:
        MultiDimAlertDecision — 含 Top 3 选择和发送判断
    """
    country_name = v1_config.COUNTRIES.get(country_code, {}).get("name", country_code)
    stage = trend_report.stage
    business_date = trend_report.business_date

    # ---- Phase 1.5: 所有维度评分 ----
    all_anomalies = score_all_dimensions(trend_report, country_code)

    # ---- Top 3 选择 ----
    overall_anomaly, top3_segments, total_anomalies, truncated = select_top3(all_anomalies)

    # ---- 发送判断 ----
    should_alert = (overall_anomaly is not None) or (len(top3_segments) > 0)

    # 构建告警原因
    reasons = []
    if overall_anomaly is not None:
        reasons.append(f"整体异常（{overall_anomaly.condition_summary}）")
    if top3_segments:
        seg_names = [s.hierarchy_path or s.display_name for s in top3_segments]
        reasons.append(f"子维度异常: {', '.join(seg_names)}")
    alert_reason = "; ".join(reasons) if reasons else ""

    # ---- 整体数据快照 ----
    overall_tr = trend_report.get("overall", "total")
    overall_rate = overall_tr.current_value if overall_tr else 0.0
    overall_due = overall_tr.due_amount if overall_tr else 0.0
    overall_cases = overall_tr.case_count if overall_tr else 0
    overall_level = overall_tr.overall_judgment if overall_tr else "GREEN"
    overall_icon = overall_tr.overall_icon if overall_tr else ""

    # 提取 overall 的比较变化
    overall_dod = 0.0
    overall_3d = 0.0
    overall_7d = 0.0
    overall_target_pp = 0.0
    overall_target_val = 0.0
    if overall_tr:
        for comp in overall_tr.comparisons:
            if comp.method == "dod":
                overall_dod = comp.change_abs
            elif comp.method == "3d_avg":
                overall_3d = comp.change_abs
            elif comp.method == "7d_avg":
                overall_7d = comp.change_abs
            elif comp.method == "target":
                overall_target_pp = comp.change_abs
                overall_target_val = comp.baseline_value

    # ---- 今日结论 ----
    conclusion = _build_conclusion(overall_anomaly, top3_segments, overall_rate,
                                   overall_dod, overall_3d, overall_7d)

    # ---- 构建决策 ----
    decision = MultiDimAlertDecision(
        country_code=country_code,
        country_name=country_name,
        business_date=business_date,
        stage=stage,
        should_alert=should_alert,
        alert_reason=alert_reason,
        all_anomalies=all_anomalies,
        overall_anomaly=overall_anomaly,
        top3_segments=top3_segments,
        overall_rate=overall_rate,
        overall_due_amount=overall_due,
        overall_case_count=overall_cases,
        overall_dod_change_pp=overall_dod,
        overall_avg3d_change_pp=overall_3d,
        overall_avg7d_change_pp=overall_7d,
        overall_target_pp=overall_target_pp,
        overall_target_value=overall_target_val,
        overall_alert_level=overall_level,
        overall_alert_icon=overall_icon,
        conclusion_text=conclusion,
        total_segment_anomalies=total_anomalies,
        truncated_count=truncated,
    )

    return decision


def decide_alert_multi_dim_enriched(
    trend_report: TrendReport,
    root_cause: Optional[RootCauseResult],
    action_result: Optional[ActionResult],
    country_code: str,
) -> MultiDimAlertDecision:
    """多维度独立监控 + Root Cause / Action 增强。

    当 Root Cause 和 Action 可用时（Overall ORANGE/RED 触发），
    将 Action 中的建议映射到对应维度，提供更丰富的告警信息。

    Args:
        trend_report: Phase 1 输出
        root_cause: Phase 2 输出（可为 None）
        action_result: Phase 3 输出（可为 None）
        country_code: "MX" | "AR"

    Returns:
        MultiDimAlertDecision — 含增强建议
    """
    # ---- 基础决策 ----
    decision = decide_alert_multi_dim(trend_report, country_code)

    # ---- Root Cause / Action 增强 ----
    if root_cause is not None and root_cause.has_root_cause:
        decision.has_root_cause = True

        if action_result is not None:
            decision.has_actions = True

            # 将 Action 映射到对应的 DimensionAnomaly
            _enrich_with_actions(decision, action_result)

    return decision


def _build_conclusion(
    overall_anomaly: Optional[DimensionAnomaly],
    top3_segments: List[DimensionAnomaly],
    overall_rate: float,
    overall_dod: float,
    overall_3d: float,
    overall_7d: float,
) -> str:
    """生成"今日结论"文本。"""
    if overall_anomaly is not None:
        # 整体异常
        parts = [f"整体首逾率明显恶化（{overall_rate*100:.2f}%），"]
        conditions = []
        if overall_anomaly.dod_pass:
            conditions.append(f"较昨日 +{overall_dod*100:.2f}pp")
        if overall_anomaly.avg3d_pass:
            conditions.append(f"较3日 +{overall_3d*100:.2f}pp")
        if overall_anomaly.avg7d_pass:
            conditions.append(f"较7日 +{overall_7d*100:.2f}pp")
        parts.append("、".join(conditions))

        if top3_segments:
            seg_names = [s.hierarchy_path or s.display_name for s in top3_segments]
            parts.append(f"。局部风险集中在: {', '.join(seg_names)}")
        parts.append("，建议立即排查。")
        return "".join(parts)

    elif top3_segments:
        # 整体正常，但子维度异常
        n = len(top3_segments)
        seg_names = [s.hierarchy_path or s.display_name for s in top3_segments]
        return (
            f"整体首逾率基本稳定（{overall_rate*100:.2f}%），"
            f"但发现 {n} 个局部风险: {', '.join(seg_names)}，需重点关注。"
        )

    else:
        return f"各维度首逾率均在正常范围，整体风险可控（{overall_rate*100:.2f}%）。"


def _enrich_with_actions(
    decision: MultiDimAlertDecision,
    action_result: ActionResult,
) -> None:
    """将 Action Engine 的建议映射到对应维度异常。

    匹配逻辑:
      - P1/P2 action 的 target_path 可能包含 bucket 名称
      - 尝试匹配到 top3_segments 或 overall_anomaly
    """
    # 合并所有 action items（P1 优先）
    all_items = (action_result.p1_actions + action_result.p2_actions +
                 action_result.p3_actions)

    if not all_items:
        return

    # 构建查找: bucket_name → action_text
    # target_path 格式: "借款分期 → C" 或 "单期 → 非分期 → D"
    action_map = {}
    for item in all_items:
        if item.target_path:
            # 取路径最后一段作为 key
            parts = [p.strip() for p in item.target_path.split("→")]
            for part in parts:
                action_map[part] = item.action

    # 映射到 top3
    for anomaly in decision.top3_segments:
        if anomaly.bucket in action_map and not anomaly.suggested_action:
            anomaly.suggested_action = action_map[anomaly.bucket]

    # 映射到 overall
    if decision.overall_anomaly is not None:
        # 用 action_result.overall_action 作为整体建议
        if action_result.overall_action:
            decision.overall_anomaly.suggested_action = action_result.overall_action


# ============================================================
#  终端打印（多维度）
# ============================================================

def print_multi_dim_decision(decision: MultiDimAlertDecision) -> None:
    """终端打印多维度告警决策（调试用，非飞书格式）。"""
    country_flag = v1_config.COUNTRIES.get(decision.country_code, {}).get("flag", "")
    print(f"\n{'='*60}")
    print(f"  {country_flag} Multi-Dim Alert Decision — {decision.country_name}")
    print(f"  Stage: {decision.stage} | Date: {decision.business_date}")
    print(f"{'='*60}")

    # ---- 今日结论 ----
    print(f"\n  ━━ 今日结论 ━━")
    print(f"  {decision.conclusion_text}")

    # ---- 决策 ----
    status = "📢 SEND" if decision.should_alert else "🔇 SILENT"
    print(f"\n  决策: {status}")
    if decision.alert_reason:
        print(f"  原因: {decision.alert_reason}")
    if decision.has_root_cause:
        print(f"  增强: Root Cause + Action 已启用")
    if decision.truncated_count > 0:
        print(f"  注意: {decision.truncated_count} 个异常子维度被截断，仅展示 Top 3")

    # ---- 重点风险 Top 3 ----
    if decision.overall_anomaly or decision.top3_segments:
        print(f"\n  ━━ 重点风险 Top 3 ━━")

        rank = 1
        if decision.overall_anomaly is not None:
            _print_anomaly_row(rank, decision.overall_anomaly)
            rank += 1

        for seg in decision.top3_segments:
            _print_anomaly_row(rank, seg)
            rank += 1

    # ---- 整体数据 ----
    print(f"\n  ━━ 整体数据 ━━")
    print(f"  首逾率: {decision.overall_rate*100:.2f}%")
    print(f"  较昨日: {decision.overall_dod_change_pp*100:+.2f}pp  "
          f"较3日: {decision.overall_avg3d_change_pp*100:+.2f}pp  "
          f"较7日: {decision.overall_avg7d_change_pp*100:+.2f}pp")
    if decision.overall_target_value > 0:
        print(f"  目标值: {decision.overall_target_value*100:.2f}%（参考，高于 {decision.overall_target_pp*100:+.2f}pp）")
    print(f"  到期本金: ¥{decision.overall_due_amount:,.0f}  |  "
          f"到期笔数: {decision.overall_case_count:,}")

    # ---- 全部维度评分（调试用） ----
    print_dimension_scores(decision.all_anomalies, decision.country_code)

    print(f"\n{'='*60}\n")


def _print_anomaly_row(rank: int, anomaly: DimensionAnomaly) -> None:
    """打印单个风险行。"""
    icon = anomaly.worst_alert_icon or "🟢"
    risk_bar = _risk_bar(anomaly.risk_score)
    # Top3 标题仅显示一级业务分类
    if anomaly.level == 2:
        title_name = anomaly.dim_label  # "分期产品"（父级分类）
    elif anomaly.level in (1, 3):
        title_name = anomaly.bucket_label
    else:
        title_name = anomaly.hierarchy_path or anomaly.display_name
    print(f"\n   {rank}  {icon} {title_name}  {risk_bar}")
    print(f"      今日首逾: {anomaly.current_rate*100:.2f}%")
    print(f"      较昨日: {anomaly.dod_change_pp*100:+.2f}pp  "
          f"较3日: {anomaly.avg3d_change_pp*100:+.2f}pp  "
          f"较7日: {anomaly.avg7d_change_pp*100:+.2f}pp")
    print(f"      条件: {anomaly.condition_summary}  "
          f"(DoD {'✅' if anomaly.dod_pass else '❌'}  "
          f"3d {'✅' if anomaly.avg3d_pass else '❌'}  "
          f"7d {'✅' if anomaly.avg7d_pass else '❌'})")
    print(f"      影响金额: ¥{anomaly.due_amount:,.0f}  |  "
          f"到期笔数: {anomaly.case_count:,}")
    if anomaly.persistence_label:
        print(f"      持续状态: {anomaly.persistence_label}")
    if anomaly.suggested_action:
        print(f"      建议: {anomaly.suggested_action}")
    if anomaly.risk_breakdown:
        print(f"      Risk: {anomaly.risk_breakdown}")


def _risk_bar(score: float) -> str:
    """风险分数可视化条。"""
    filled = int(score / 10)
    empty = 10 - filled
    bar = "█" * filled + "░" * empty
    if score >= 70:
        color = "🔴"
    elif score >= 40:
        color = "🟠"
    elif score >= 20:
        color = "🟡"
    else:
        color = "🟢"
    return f"{color} [{bar}] {score:.0f}"
