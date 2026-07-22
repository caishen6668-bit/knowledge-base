"""
V2 行动建议引擎 — 基于规则的自动化行动建议

输入: TrendResult + RootCauseResult
输出: ActionResult

纯规则引擎，不使用大模型。
Phase 4 的 AI 只负责把 ActionResult 组织成自然语言。

规则设计原则:
  1. 优先级 = f(方向, |变化pp|, 整体告警等级, 影响金额)
  2. 人工介入 = f(优先级, 方向, 整体告警等级)
  3. 建议动作 = f(风控等级, 方向, 变化幅度, 阶段)
  4. 整体建议 = f(整体告警等级, P1数量, 恶化/改善分布)

不修改 V1。不修改 TrendEngine。不修改 RootCauseEngine。
"""

from typing import List, Optional

from .. import config as v1_config
from .models import (
    TrendReport, TrendResult,
    RootCauseResult, RootCausePath,
    ActionItem, ActionResult,
)


# ============================================================
#  规则阈值配置
# ============================================================

# P1 (紧急): 恶化 + (整体RED且|pp|>=阈值) 或 |pp|>=高阈值 或 影响金额>=阈值
# 注意: change_pp 以小数存储 (0.0124 = 1.24pp)，阈值也使用小数形式
P1_RED_CHANGE_THRESHOLD = 0.005     # 整体RED时，|change_pp| >= 0.5pp → P1
P1_CHANGE_THRESHOLD = 0.010         # |change_pp| >= 1.0pp → P1（恶化方向）
P1_IMPACT_THRESHOLD = 100_000       # 影响金额 >= 10万 → P1（恶化方向）

# P2 (重要): 恶化 + |pp|>=阈值 或 整体告警+小恶化 或 大改善需验证
P2_WORSENING_THRESHOLD = 0.005      # |change_pp| >= 0.5pp → P2（恶化但未达P1）
P2_ALERT_WORSENING_THRESHOLD = 0.003  # 整体🟠/🔴 + |change_pp| >= 0.3pp → P2
P2_IMPROVING_THRESHOLD = 0.015      # |change_pp| >= 1.5pp 改善 → P2（需验证持续性）

# "保持当前策略" 阈值
KEEP_STRATEGY_THRESHOLD = 0.010     # 改善 |change_pp| >= 1.0pp → 保持当前策略


# ============================================================
#  路径工具
# ============================================================

def _get_grade(path: RootCausePath) -> Optional[str]:
    """从路径中提取订单风控等级 (A~F)"""
    for node in path.path:
        if node.dim_key == "order_grade":
            return node.bucket
    return None


def _get_product_type(path: RootCausePath) -> Optional[str]:
    """从路径中提取产品类型 (单期/分期)"""
    for node in path.path:
        if node.dim_key == "product_type":
            return node.bucket
    return None


# ============================================================
#  Priority 判定
# ============================================================

def _determine_priority(
    path: RootCausePath,
    overall_alert_level: str,
) -> str:
    """
    判定单个根因路径的优先级。

    规则（按顺序匹配，命中即停止）:

    P1 条件（恶化方向）:
      1. 整体 RED + |change_pp| >= 0.5pp
      2. |change_pp| >= 1.0pp
      3. 影响金额 >= 10万

    P2 条件:
      1. 恶化 + |change_pp| >= 0.5pp（未达 P1）
      2. 整体 🟠/🔴 + 恶化 + |change_pp| >= 0.3pp
      3. 改善 + |change_pp| >= 1.5pp（大改善需验证持续性）

    P3: 其他
    """
    change_abs = abs(path.change_pp)
    is_worsening = path.change_pp > 0

    # ---- P1 判定 ----
    if is_worsening:
        # 整体 RED + 中等以上变化
        if overall_alert_level == "RED" and change_abs >= P1_RED_CHANGE_THRESHOLD:
            return "P1"

        # 大幅变化
        if change_abs >= P1_CHANGE_THRESHOLD:
            return "P1"

        # 重大影响金额
        if path.impact_amount >= P1_IMPACT_THRESHOLD:
            return "P1"

    # ---- P2 判定 ----
    if is_worsening:
        # 中等变化
        if change_abs >= P2_WORSENING_THRESHOLD:
            return "P2"

        # 告警状态下的小幅恶化
        if (overall_alert_level in ("ORANGE", "RED")
                and change_abs >= P2_ALERT_WORSENING_THRESHOLD):
            return "P2"

    else:  # 改善方向
        # 大幅改善需要验证
        if change_abs >= P2_IMPROVING_THRESHOLD:
            return "P2"

    # ---- P3 ----
    return "P3"


# ============================================================
#  人工介入判定
# ============================================================

def _needs_human(
    priority: str,
    is_worsening: bool,
    overall_alert_level: str,
) -> bool:
    """
    判定是否需要人工介入。

    规则:
      - P1: 始终 YES
      - P2 恶化: YES（恶化需要人判断原因）
      - 整体 RED: YES（红色告警必须人工关注）
      - 其他: NO
    """
    if priority == "P1":
        return True

    if priority == "P2" and is_worsening:
        return True

    if overall_alert_level == "RED":
        return True

    return False


# ============================================================
#  行动建议
# ============================================================

def _suggest_action(path: RootCausePath, stage: str) -> tuple:
    """
    根据路径特征生成建议动作 + 理由。

    规则:

    恶化（首逾上升）— 按风控等级分级:
      - Grade A/B（高风险）: "检查分案"
        高风险客户首逾上升 → 案件分配/准入标准可能需调整
      - Grade C/D（中风险）: "检查策略调整"
        中风险对策略变化最敏感 → 风控策略可能需要回调
      - Grade E/F（低风险）: "检查今日新增放款"
        低风险客户突然逾期 → 新增放款质量可能下降
      - 无 Grade: "检查催收覆盖率"（兜底）

    改善（首逾下降）— 按幅度分级:
      - |change_pp| >= 1.0pp: "保持当前策略"（策略有效）
      - |change_pp| < 1.0pp: "继续观察"（需要更多数据确认）

    特殊: D1/S1 阶段恶化 → 优先推送 "检查催收覆盖率"
    """
    grade = _get_grade(path)
    product_type = _get_product_type(path)
    change_abs = abs(path.change_pp)
    is_worsening = path.change_pp > 0

    if is_worsening:
        # 恶化方向：按风控等级推荐动作
        if grade in ("A", "B"):
            action = "检查分案"
            reason = (
                f"高风险等级 {grade} 首逾上升 {change_abs * 100:.2f}pp，"
                f"影响金额 {path.impact_amount:,.0f}，"
                f"需排查案件分配与准入标准"
            )
        elif grade in ("C", "D"):
            action = "检查策略调整"
            reason = (
                f"中风险等级 {grade} 首逾上升 {change_abs * 100:.2f}pp，"
                f"影响金额 {path.impact_amount:,.0f}，"
                f"该等级对风控策略最敏感，需排查近期策略变更"
            )
        elif grade in ("E", "F"):
            action = "检查今日新增放款"
            reason = (
                f"低风险等级 {grade} 首逾上升 {change_abs * 100:.2f}pp，"
                f"影响金额 {path.impact_amount:,.0f}，"
                f"低风险客户异常逾期通常指向新增放款质量下降"
            )
        else:
            # 无明确 grade（兜底）
            action = "检查催收覆盖率"
            reason = (
                f"首逾上升 {change_abs * 100:.2f}pp，"
                f"影响金额 {path.impact_amount:,.0f}，"
                f"建议排查催收覆盖与回收效率"
            )

        # D1/S1 阶段优先催收动作
        if stage in ("D1", "S1") and action != "检查催收覆盖率":
            reason += f"（{stage}阶段建议同步排查催收覆盖率）"

    else:
        # 改善方向
        if change_abs >= KEEP_STRATEGY_THRESHOLD:
            action = "保持当前策略"
            reason = (
                f"首逾下降 {change_abs * 100:.2f}pp，"
                f"改善金额 {path.impact_amount:,.0f}，"
                f"当前策略效果显著，建议保持"
            )
        else:
            action = "继续观察"
            reason = (
                f"首逾小幅下降 {change_abs * 100:.2f}pp，"
                f"改善金额 {path.impact_amount:,.0f}，"
                f"需更多数据确认趋势持续性"
            )

    return action, reason


# ============================================================
#  整体行动建议
# ============================================================

def _overall_recommendation(
    trend_report: TrendReport,
    root_cause: RootCauseResult,
    p1_count: int,
    p2_worsening_count: int,
    worsening_count: int,
    improving_count: int,
) -> str:
    """
    生成面向管理层的整体行动建议。

    规则:
      - RED + 趋势恶化:   紧急 — 立即排查 + 建议召开风险评审会
      - RED + 仅目标偏离: 警告 — 整体高于目标但趋势稳定，排查子维度恶化
      - ORANGE:          警告 — 当日完成排查
      - YELLOW/GREEN 但有恶化: 关注 — 持续监控
      - 全部改善:         保持策略
    """
    stage = root_cause.stage
    overall_alert = root_cause.overall_alert_level
    overall_change = root_cause.overall_change_abs

    # 检查 RED 是否来自趋势恶化（DoD/3d/7d），还是仅来自目标偏离
    red_from_trend = False
    if overall_alert == "RED":
        overall_tr = trend_report.get("overall", "total")
        if overall_tr:
            for c in overall_tr.comparisons:
                if c.method != "target" and c.alert_level == "RED":
                    red_from_trend = True
                    break

    if overall_alert == "RED":
        if red_from_trend or overall_change > 0:
            # 真正的恶化趋势
            msg = (
                f"🔴 严重预警：整体 {stage} 首逾率趋势恶化"
                f"（{overall_change * 100:+.2f}pp）。"
                f"建议立即启动异常排查，优先处理 {p1_count} 个 P1 节点。"
            )
            if p1_count > 0:
                msg += "必要时当日召开风险评审会。"
            else:
                msg += "持续监控恶化趋势。"
        else:
            # RED 仅来自目标偏离，趋势本身平稳或改善
            msg = (
                f"🔴 严重预警：整体 {stage} 首逾率"
                f"（{root_cause.overall_current_rate:.1%}）"
                f"显著高于目标值，但趋势平稳"
                f"（{overall_change * 100:+.2f}pp vs 昨日）。"
            )
            if worsening_count > 0:
                msg += (
                    f"建议优先排查 {worsening_count} 个恶化子节点，"
                    f"关注结构性风险。"
                )
            else:
                msg += "建议持续监控，关注绝对水平。"
        return msg

    if overall_alert == "ORANGE":
        if p1_count > 0:
            return (
                f"🟠 警告：整体 {stage} 首逾率出现异常。"
                f"建议优先处理 {p1_count} 个 P1 紧急节点，当日完成排查。"
            )
        elif p2_worsening_count > 0:
            return (
                f"🟠 警告：整体 {stage} 首逾率偏离正常范围。"
                f"建议当日完成 {p2_worsening_count} 个 P2 恶化节点排查。"
            )
        else:
            return (
                f"🟠 警告：整体 {stage} 首逾率出现异常。"
                f"建议当日完成异常节点排查，持续监控。"
            )

    # GREEN / YELLOW（但因 RootCause 被触发，说明有子维度异常）
    if worsening_count > 0:
        return (
            f"🟡 关注：整体 {stage} 首逾率在可控范围，"
            f"但 {worsening_count} 个子维度出现恶化趋势。"
            f"建议持续监控恶化节点，准备应对方案。"
        )

    # 全部改善 → 保持策略
    if improving_count > 0:
        return (
            f"🟢 良好：整体 {stage} 首逾率呈改善趋势。"
            f"建议保持当前风控与催收策略，继续观察。"
        )

    return f"🟢 正常：整体 {stage} 首逾率稳定。建议保持当前策略。"


# ============================================================
#  主入口
# ============================================================

def analyze_actions(
    trend_report: TrendReport,
    root_cause: RootCauseResult,
) -> Optional[ActionResult]:
    """
    行动建议分析 — V2 Phase 3 核心入口。

    基于 TrendReport + RootCauseResult 的规则引擎，
    为每个异常节点生成优先级、人工介入建议、具体动作。

    Args:
        trend_report: Phase 1 趋势分析报告
        root_cause: Phase 2 根因定位结果

    Returns:
        ActionResult 或 None（无根因结果）
    """
    if not root_cause or not root_cause.has_root_cause:
        print(f"\n  [ActionEngine] 无根因结果，跳过行动建议")
        return None

    country = v1_config.COUNTRIES[root_cause.country_code]
    overall_alert = root_cause.overall_alert_level
    all_paths = root_cause.worsening + root_cause.improving

    if not all_paths:
        print(f"\n  [ActionEngine] 无异常路径，跳过行动建议")
        return None

    print(f"\n{'='*55}")
    print(f"  V2 行动建议引擎 (规则引擎)")
    print(f"  输入: {len(root_cause.worsening)} 恶化路径 + {len(root_cause.improving)} 改善路径")
    print(f"{'='*55}")

    # ---- 为每个路径生成 ActionItem ----
    p1_actions: List[ActionItem] = []
    p2_actions: List[ActionItem] = []
    p3_actions: List[ActionItem] = []

    for path in all_paths:
        is_worsening = path.change_pp > 0
        priority = _determine_priority(path, overall_alert)
        needs_human = _needs_human(priority, is_worsening, overall_alert)
        action, reason = _suggest_action(path, root_cause.stage)

        item = ActionItem(
            priority=priority,
            needs_human=needs_human,
            action=action,
            target_path=path.path_label,
            change_pp=path.change_pp,
            impact_amount=path.impact_amount,
            is_worsening=is_worsening,
            reason=reason,
        )

        if priority == "P1":
            p1_actions.append(item)
        elif priority == "P2":
            p2_actions.append(item)
        else:
            p3_actions.append(item)

    # 组内排序：恶化在前，按 |change_pp| 降序
    def _sort_key(item: ActionItem) -> tuple:
        return (not item.is_worsening, -abs(item.change_pp))

    p1_actions.sort(key=_sort_key)
    p2_actions.sort(key=_sort_key)
    p3_actions.sort(key=_sort_key)

    # ---- 整体建议 ----
    p2_worsening = sum(1 for a in p2_actions if a.is_worsening)
    overall_action = _overall_recommendation(
        trend_report,
        root_cause,
        len(p1_actions),
        p2_worsening,
        len(root_cause.worsening),
        len(root_cause.improving),
    )

    # ---- 构建结果 ----
    result = ActionResult(
        country_code=root_cause.country_code,
        country_name=country["name"],
        business_date=root_cause.business_date,
        stage=root_cause.stage,
        overall_alert_level=overall_alert,
        overall_change_pp=root_cause.overall_change_abs,
        overall_due_amount=root_cause.overall_due_amount,
        p1_actions=p1_actions,
        p2_actions=p2_actions,
        p3_actions=p3_actions,
        overall_action=overall_action,
    )

    # ---- 打印 ----
    _print_action_result(result)

    return result


# ============================================================
#  格式化输出
# ============================================================

def _print_action_result(result: ActionResult):
    """打印 ActionResult（终端格式）。"""

    change_arrow = "↑" if result.overall_change_pp >= 0 else "↓"
    print(f"\n  📋 行动建议")
    print(f"  整体态势: {result.overall_change_pp:+.4f} {change_arrow}  "
          f"|  告警等级: {result.overall_alert_level}")
    print(f"  需要人工介入: {'⚠️ 是' if result.needs_any_human else '✅ 否'}")

    # ---- P1 紧急 ----
    if result.p1_actions:
        print(f"\n  🔴 P1 紧急 — 需立即处理 ({len(result.p1_actions)} 项)")
        print(f"  {'─'*55}")
        for i, item in enumerate(result.p1_actions):
            _print_action_item(i + 1, item)

    # ---- P2 重要 ----
    if result.p2_actions:
        print(f"\n  🟠 P2 重要 — 当日处理 ({len(result.p2_actions)} 项)")
        print(f"  {'─'*55}")
        for i, item in enumerate(result.p2_actions):
            _print_action_item(i + 1, item)

    # ---- P3 监控 ----
    if result.p3_actions:
        print(f"\n  🟡 P3 监控 — 持续观察 ({len(result.p3_actions)} 项)")
        print(f"  {'─'*55}")
        for i, item in enumerate(result.p3_actions):
            _print_action_item(i + 1, item)

    # ---- 整体建议 ----
    print(f"\n  {'='*55}")
    print(f"  💡 总体建议")
    print(f"  {'─'*55}")
    print(f"  {result.overall_action}")

    print(f"\n  {result.summary}")


def _print_action_item(index: int, item: ActionItem):
    """打印单个行动建议"""
    direction = "📈" if item.is_worsening else "📉"
    human_tag = "👤 需人工" if item.needs_human else "🤖 自动监控"
    print(f"  {index}. [{item.priority}] {direction} {item.target_path}")
    print(f"     {item.change_pp * 100:+.2f}pp  |  影响金额: {item.impact_amount:,.0f}  |  {human_tag}")
    print(f"     → {item.action}")
    print(f"     📝 {item.reason}")


def print_action_report(result: ActionResult):
    """完整打印 ActionResult（面向管理层，可在 Phase 3/4 复用）。"""
    if not result:
        print(f"\n  🟢 无需行动建议")
        return

    print(f"\n{'='*70}")
    print(f"  V2 Action Report — {result.country_name} ({result.country_code})")
    print(f"  业务日期: {result.business_date}  |  阶段: {result.stage}")
    print(f"  整体告警等级: {result.overall_alert_level}")
    print(f"{'='*70}")

    # 统计
    print(f"\n  行动项统计: "
          f"P1={len(result.p1_actions)}  "
          f"P2={len(result.p2_actions)}  "
          f"P3={len(result.p3_actions)}")

    # P1
    if result.p1_actions:
        print(f"\n  {'─'*60}")
        print(f"  🔴 紧急行动 (P1)")
        for i, item in enumerate(result.p1_actions):
            print(f"  {i+1}. {item.target_path}")
            print(f"     {item.change_pp * 100:+.2f}pp | {item.action} | 👤需人工")
            print(f"     {item.reason}")

    # P2
    if result.p2_actions:
        print(f"\n  {'─'*60}")
        print(f"  🟠 重要行动 (P2)")
        for i, item in enumerate(result.p2_actions):
            human = "👤需人工" if item.needs_human else "🤖自动"
            print(f"  {i+1}. {item.target_path}")
            print(f"     {item.change_pp * 100:+.2f}pp | {item.action} | {human}")
            print(f"     {item.reason}")

    # P3
    if result.p3_actions:
        print(f"\n  {'─'*60}")
        print(f"  🟡 持续监控 (P3)")
        for i, item in enumerate(result.p3_actions):
            human = "👤需人工" if item.needs_human else "🤖自动"
            print(f"  {i+1}. {item.target_path}")
            print(f"     {item.change_pp * 100:+.2f}pp | {item.action} | {human}")
            print(f"     {item.reason}")

    # 总体建议
    print(f"\n  {'─'*60}")
    print(f"  💡 总体建议")
    print(f"  {result.overall_action}")

    print(f"\n{'='*70}")
    print(f"  {result.summary}")
    print(f"{'='*70}\n")
