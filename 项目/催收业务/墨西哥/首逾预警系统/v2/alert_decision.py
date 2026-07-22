"""
V2 告警决策引擎 — 判断是否真正发送飞书告警

输入: TrendReport + RootCauseResult + ActionResult
输出: AlertDecision

纯规则引擎，避免噪声告警。仅在有意义的异常出现时才触发飞书推送。

判定规则（满足任一即发送）:
  1. 存在 P1 紧急项
  2. 最大影响金额 >= ALERT_MIN_IMPACT
  3. 连续 N 天首逾率恶化（ALERT_CONSECUTIVE_DAYS）
  4. 当前首逾率高于目标值超过 ALERT_TARGET_PP

不满足任何条件 → should_alert=False → 仅记录日志，不发送飞书。

不修改 V1。不修改 TrendEngine。不修改 RootCauseEngine。不修改 ActionEngine。
"""

from datetime import datetime, timedelta
from typing import Optional

from .. import config as v1_config
from . import config as v2_config
from .models import (
    TrendReport, TrendResult,
    RootCauseResult,
    ActionResult,
    AlertDecision,
)


# ============================================================
#  条件 1: P1 检查
# ============================================================

def _check_p1(action_result: ActionResult) -> tuple:
    """
    检查是否存在 P1 紧急项。

    Returns:
        (triggered: bool, reason: str)
    """
    if action_result.has_critical:
        return True, f"P1（{len(action_result.p1_actions)} 项紧急）"
    return False, ""


# ============================================================
#  条件 2: 影响金额检查
# ============================================================

def _check_impact(action_result: ActionResult) -> tuple:
    """
    检查最大影响金额是否超过阈值。

    Returns:
        (triggered: bool, reason: str)
    """
    max_impact = 0.0
    for item in action_result.all_actions:
        if item.impact_amount > max_impact:
            max_impact = item.impact_amount

    if max_impact >= v2_config.ALERT_MIN_IMPACT:
        return True, f"影响金额过大（{max_impact:,.0f} ≥ {v2_config.ALERT_MIN_IMPACT:,}）"
    return False, ""


# ============================================================
#  条件 3: 连续恶化检查
# ============================================================

def _check_consecutive_worsening(
    trend_report: TrendReport,
    business_date: str,
) -> tuple:
    """
    检查整体首逾率是否连续 N 天恶化（逐日上升）。

    从 overall TrendResult 的 historical_values 中提取历史数据，
    按日期排序后检查: today > yesterday > day_before > ... 是否连续。

    Returns:
        (triggered: bool, reason: str, consecutive_days: int)
    """
    overall = trend_report.get("overall", "total")
    if not overall or not overall.historical_values:
        return False, "", 0

    threshold = v2_config.ALERT_CONSECUTIVE_DAYS

    # 构建日期→首逾率的映射（包括今天）
    rates_by_date = dict(overall.historical_values)
    rates_by_date[business_date] = overall.current_value

    # 按日期降序排列（最新在前）
    sorted_dates = sorted(rates_by_date.keys(), reverse=True)

    if len(sorted_dates) < threshold:
        return False, "", 0

    # 从今天开始，检查连续上升的天数
    consecutive = 0
    for i in range(len(sorted_dates) - 1):
        today_rate = rates_by_date[sorted_dates[i]]
        yesterday_rate = rates_by_date[sorted_dates[i + 1]]

        # 首逾率上升 = 恶化
        if today_rate > yesterday_rate + 0.0001:  # 微小容差
            consecutive += 1
        else:
            break  # 中断连续

    if consecutive >= threshold:
        return True, (
            f"连续恶化（{consecutive} 天 ≥ {threshold} 天）"
        ), consecutive

    return False, "", consecutive


# ============================================================
#  条件 4: 高于目标值检查
# ============================================================

def _check_target_deviation(
    trend_report: TrendReport,
    country_code: str,
) -> tuple:
    """
    检查当前首逾率是否高于目标值超过阈值。

    从 TrendReport 的 overall TrendResult 中读取当前值，
    与 config.TARGET_RATES 中的目标值比较。

    Returns:
        (triggered: bool, reason: str, deviation_pp: float)
    """
    overall = trend_report.get("overall", "total")
    if not overall:
        return False, "", 0.0

    targets = v2_config.TARGET_RATES.get(country_code, {})
    target = targets.get(trend_report.stage)
    if target is None:
        return False, "", 0.0

    deviation = overall.current_value - target
    threshold = v2_config.ALERT_TARGET_PP

    if deviation >= threshold:
        return True, (
            f"高于目标（{deviation * 100:+.2f}pp ≥ {threshold * 100:.1f}pp，"
            f"当前 {overall.current_value:.1%} vs 目标 {target:.1%}）"
        ), deviation

    return False, "", deviation


# ============================================================
#  主入口
# ============================================================

def decide_alert(
    trend_report: TrendReport,
    root_cause: RootCauseResult,
    action_result: ActionResult,
    country_code: str,
) -> AlertDecision:
    """
    告警决策 — V2 Phase 3.5 核心入口。

    综合评估四个维度，判定是否应该发送飞书告警。
    所有阈值均可通过 config.py 配置，无需修改代码。

    Args:
        trend_report: Phase 1 趋势分析报告
        root_cause: Phase 2 根因定位结果
        action_result: Phase 3 行动建议结果
        country_code: "MX" | "AR"

    Returns:
        AlertDecision
    """
    country = v1_config.COUNTRIES[country_code]
    stage = trend_report.stage
    business_date = trend_report.business_date

    print(f"\n{'='*55}")
    print(f"  V2 告警决策引擎 (Alert Decision)")
    print(f"  阈值: impact≥{v2_config.ALERT_MIN_IMPACT:,}  |  "
          f"target≥{v2_config.ALERT_TARGET_PP*100:.1f}pp  |  "
          f"连续≥{v2_config.ALERT_CONSECUTIVE_DAYS}天")
    print(f"{'='*55}")

    # ---- 逐条件检查 ----
    reasons = []
    sources = []
    p1_count = len(action_result.p1_actions)

    # 条件 1: P1
    p1_triggered, p1_reason = _check_p1(action_result)
    if p1_triggered:
        reasons.append(p1_reason)
        sources.append("P1")

    # 条件 2: 影响金额
    impact_triggered, impact_reason = _check_impact(action_result)
    if impact_triggered:
        reasons.append(impact_reason)
        sources.append("RootCause")

    # 条件 3: 连续恶化
    consec_triggered, consec_reason, consec_days = _check_consecutive_worsening(
        trend_report, business_date
    )
    if consec_triggered:
        reasons.append(consec_reason)
        sources.append("Trend")

    # 条件 4: 高于目标
    target_triggered, target_reason, target_dev = _check_target_deviation(
        trend_report, country_code
    )
    if target_triggered:
        reasons.append(target_reason)
        sources.append("Target")

    # ---- 计算辅助指标 ----
    max_impact = 0.0
    for item in action_result.all_actions:
        if item.impact_amount > max_impact:
            max_impact = item.impact_amount

    overall = trend_report.get("overall", "total")
    overall_change = overall.current_value - (
        list(overall.historical_values.values())[0]
        if overall and overall.historical_values else 0.0
    ) if overall else 0.0
    # Use root_cause's overall_change_abs for accuracy
    overall_change = root_cause.overall_change_abs if root_cause else overall_change

    # ---- 决策 ----
    should_alert = len(reasons) > 0
    alert_reason = ", ".join(reasons) if reasons else ""
    # 去重 source 并保留顺序
    seen = set()
    unique_sources = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique_sources.append(s)
    trigger_source = ", ".join(unique_sources) if unique_sources else ""

    decision = AlertDecision(
        country_code=country_code,
        country_name=country["name"],
        business_date=business_date,
        stage=stage,
        should_alert=should_alert,
        alert_reason=alert_reason,
        trigger_source=trigger_source,
        p1_count=p1_count,
        max_impact=max_impact,
        target_deviation_pp=target_dev,
        consecutive_worsening_days=consec_days,
        overall_change_pp=overall_change,
        overall_alert_level=root_cause.overall_alert_level if root_cause else "GREEN",
    )

    # ---- 打印 ----
    _print_decision(decision, p1_triggered, impact_triggered,
                    consec_triggered, target_triggered)

    return decision


# ============================================================
#  格式化输出
# ============================================================

def _print_decision(
    decision: AlertDecision,
    p1: bool, impact: bool, consec: bool, target: bool,
):
    """打印告警决策详情（终端格式）。"""

    # 条件检查明细
    print(f"\n  📋 条件检查")
    print(f"  {'─'*50}")
    _print_condition("P1", p1, decision.p1_count,
                     f"存在 {decision.p1_count} 个 P1 紧急项")
    _print_condition("影响金额", impact, decision.max_impact,
                     f"最大影响金额 {decision.max_impact:,.0f} ≥ "
                     f"{v2_config.ALERT_MIN_IMPACT:,}")
    _print_condition("连续恶化", consec, decision.consecutive_worsening_days,
                     f"连续恶化 {decision.consecutive_worsening_days} 天 ≥ "
                     f"{v2_config.ALERT_CONSECUTIVE_DAYS} 天")
    _print_condition("高于目标", target, decision.target_deviation_pp,
                     f"高于目标 {decision.target_deviation_pp * 100:+.2f}pp ≥ "
                     f"{v2_config.ALERT_TARGET_PP * 100:.1f}pp")

    # 最终决策
    print(f"\n  {'='*50}")
    if decision.should_alert:
        print(f"  📢 决策: 发送飞书告警")
        print(f"  原因: {decision.alert_reason}")
        print(f"  来源: {decision.trigger_source}")
    else:
        print(f"  🔇 决策: 不发送飞书告警（仅记录日志）")
        print(f"  原因: 未满足任何告警条件")
    print(f"  {'='*50}")

    print(f"\n  {decision.details}")


def _print_condition(label: str, triggered: bool, value, detail: str):
    """打印单个条件检查结果"""
    icon = "✅" if triggered else "⬜"
    print(f"  {icon} [{label}] {detail}")


def print_alert_decision(decision: AlertDecision):
    """完整打印 AlertDecision。"""
    print(f"\n{'='*70}")
    print(f"  V2 Alert Decision — {decision.country_name} ({decision.country_code})")
    print(f"  业务日期: {decision.business_date}  |  阶段: {decision.stage}")
    print(f"{'='*70}")

    status = "📢 SEND" if decision.should_alert else "🔇 SILENT"
    print(f"\n  决策: {status}")
    print(f"  告警等级: {decision.overall_alert_level}")
    print(f"  整体变化: {decision.overall_change_pp * 100:+.2f}pp")

    if decision.should_alert:
        print(f"  告警原因: {decision.alert_reason}")
        print(f"  触发来源: {decision.trigger_source}")
        print(f"\n  决策依据:")
        print(f"    P1 项数量: {decision.p1_count}")
        print(f"    最大影响金额: {decision.max_impact:,.0f}")
        print(f"    高于目标偏离: {decision.target_deviation_pp * 100:+.2f}pp")
        print(f"    连续恶化天数: {decision.consecutive_worsening_days}")

    print(f"\n  {decision.details}")
    print(f"{'='*70}\n")
