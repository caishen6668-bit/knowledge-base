"""
结构化报告生成 — V1.0 Release（管理层版本）

特性:
  - 今日分析摘要（置顶）
  - 默认隐藏 GREEN 节点
  - ↑↓→ 方向箭头
  - 影响金额展示
  - 层级下钻树
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional

from . import config
from .alert_engine import (
    AnalysisReport, ContributionItem, ContinuousAlert, HierarchicalNode,
)
from .analyzer import (
    DimensionResult, is_anomaly, change_arrow,
    calc_impact_amount, format_impact,
)


@dataclass
class ReportSection:
    """报告的一个区块"""
    title: str
    rows: List[Dict] = field(default_factory=list)
    highlight: bool = False


@dataclass
class FormattedReport:
    """格式化后的管理层报告（V1 Release）"""
    country_code: str
    country_name: str
    due_week_current: str
    due_week_baseline: str
    stage: str

    # 概要
    has_anomaly: bool
    summary_title: str
    summary_color: str

    # 整体首逾
    overall_rate: float = 0.0
    overall_change_abs: float = 0.0
    overall_alert_icon: str = ""
    overall_alert_level: str = "GREEN"
    overall_due: float = 0.0
    impact_amount: float = 0.0

    # 日期（业务口径：run_date ≠ business_date）
    run_date: str = ""           # 运行日期
    business_date: str = ""      # 业务日期（Quick BI 查询用）

    # 摘要（一行文字，放最前面）
    executive_summary: str = ""

    # 层级下钻树行
    tree_rows: List[Dict] = field(default_factory=list)

    # 异常来源
    anomaly_sources: Dict[str, List[Dict]] = field(default_factory=dict)

    # 连续异常
    continuous_alert: Optional[ContinuousAlert] = None

    # 建议
    recommendations: List[str] = field(default_factory=list)

    # 详细信息（文本降级）
    detail_sections: List[ReportSection] = field(default_factory=list)


def build_report(analysis: AnalysisReport) -> FormattedReport:
    """将 AnalysisReport 转换为 V1 Release 管理层报告。"""
    display = config.ALERT_DISPLAY

    # 整体告警等级
    worst_overall = "GREEN"
    overall_change = 0.0
    overall_due = 0.0
    if analysis.overall:
        overall_due = analysis.overall.due_amount
        for c in analysis.overall.comparisons:
            if _level_rank(c.alert_level) > _level_rank(worst_overall):
                worst_overall = c.alert_level
            if abs(c.change_abs) > abs(overall_change):
                overall_change = c.change_abs

    has_anomaly = worst_overall != "GREEN"
    info = display[worst_overall]
    arrow = change_arrow(overall_change)
    impact = calc_impact_amount(overall_change, overall_due)

    # 摘要
    if has_anomaly:
        summary_title = f"🚨 {analysis.country_name} 首逾预警"
        worst_path = _find_worst_path_label(analysis.tree) if analysis.tree else ""
        exec_summary = (
            f"{analysis.country_name} {analysis.stage}首逾 {analysis.overall.overdue_rate:.1%} "
            f"{arrow}{abs(overall_change):.2%} {info['icon']}  "
            f"影响约{format_impact(impact)}"
        )
        if worst_path:
            exec_summary += f"\n  → {worst_path}"
    else:
        summary_title = f"🟢 {analysis.country_name} 首逾正常"
        exec_summary = f"{analysis.country_name} {analysis.stage}首逾 {analysis.overall.overdue_rate:.1%} →持平 🟢"

    report = FormattedReport(
        country_code=analysis.country_code,
        country_name=analysis.country_name,
        due_week_current=analysis.due_week_current,
        due_week_baseline=analysis.due_week_baseline,
        stage=analysis.stage,
        has_anomaly=has_anomaly,
        summary_title=summary_title,
        summary_color=info["color"],
        overall_rate=analysis.overall.overdue_rate if analysis.overall else 0.0,
        overall_change_abs=overall_change,
        overall_alert_icon=info["icon"],
        overall_alert_level=worst_overall,
        overall_due=overall_due,
        impact_amount=impact,
        run_date=analysis.run_date,
        business_date=analysis.business_date,
        executive_summary=exec_summary,
        continuous_alert=analysis.continuous_alert,
    )

    # 层级树（隐藏 GREEN）
    if analysis.tree and analysis.tree.children:
        report.tree_rows = _flatten_tree(analysis.tree, hide_green=True)

    # 异常来源
    if has_anomaly and analysis.tree:
        report.anomaly_sources = _build_tree_anomaly_sources(analysis.tree)

    # 建议
    report.recommendations = _generate_recommendations(analysis)

    # 详细信息
    report.detail_sections = _build_detail_sections(analysis)

    return report


# ============================================================
#  层级树展平（V1 Release — 隐藏 GREEN、加箭头、加影响金额）
# ============================================================

def _flatten_tree(root: HierarchicalNode, hide_green: bool = True) -> List[Dict]:
    """
    层级树展平为展示行。

    格式：
      📦 单期  首逾 75.25%  ↑21.75pp  🔴  影响约5.8万
        → 包体: 非分期  75.25%  贡献 100%  🔴
          → 风控: 老客  69.80%  贡献 80%  🔴  影响约4.7万
    """
    rows = []
    overall_due = root.due_amount

    for pt_node in root.children:
        # 如果隐藏 GREEN 且该产品类型下所有节点都是 GREEN，跳过
        if hide_green and pt_node.alert_level == "GREEN":
            if not _has_anomaly_descendant(pt_node):
                continue

        arrow = change_arrow(pt_node.change_abs)
        impact = calc_impact_amount(pt_node.contribution_to_overall, overall_due)

        rows.append({
            "level": 1, "depth": 0,
            "dim_label": "产品类型", "name": pt_node.name,
            "overdue_rate": pt_node.overdue_rate,
            "change_abs": pt_node.change_abs,
            "contribution_pct": pt_node.contribution_pct,
            "alert_icon": pt_node.alert_icon,
            "alert_level": pt_node.alert_level,
            "impact": impact,
            "text": (
                f"📦 **{pt_node.name}**  首逾 {pt_node.overdue_rate:.1%}  "
                f"{arrow}{abs(pt_node.change_abs):.2%}  {pt_node.alert_icon}  "
                f"影响{format_impact(impact)}"
            ),
        })

        # Level 2: Packages
        packages = sorted(pt_node.children, key=lambda c: abs(c.contribution_pct), reverse=True)
        for pkg_node in packages[:3]:
            if hide_green and pkg_node.alert_level == "GREEN":
                # 但如果它有不GREEN的子节点，仍然展示
                if not _has_anomaly_descendant(pkg_node):
                    continue

            pkg_arrow = change_arrow(pkg_node.change_abs)
            pkg_contrib = f"贡献 {pkg_node.contribution_pct:.0%}"
            pkg_impact = calc_impact_amount(pkg_node.contribution_to_overall, overall_due)

            rows.append({
                "level": 2, "depth": 1,
                "dim_label": "包体", "name": pkg_node.name,
                "overdue_rate": pkg_node.overdue_rate,
                "change_abs": pkg_node.change_abs,
                "contribution_pct": pkg_node.contribution_pct,
                "alert_icon": pkg_node.alert_icon,
                "alert_level": pkg_node.alert_level,
                "impact": pkg_impact,
                "text": (
                    f"  → 包体: {pkg_node.name}  首逾 {pkg_node.overdue_rate:.1%}  "
                    f"{pkg_arrow}{abs(pkg_node.change_abs):.2%}  "
                    f"{pkg_contrib}  {pkg_node.alert_icon}"
                ),
            })

            # Level 3: Risk levels
            risks = sorted(pkg_node.children, key=lambda c: abs(c.contribution_pct), reverse=True)
            shown_risks = 0
            for risk_node in risks:
                if hide_green and risk_node.alert_level == "GREEN":
                    continue
                if shown_risks >= 2:
                    break
                shown_risks += 1

                risk_arrow = change_arrow(risk_node.change_abs)
                risk_contrib = f"贡献 {risk_node.contribution_pct:.0%}"
                risk_impact = calc_impact_amount(risk_node.contribution_to_overall, overall_due)

                rows.append({
                    "level": 3, "depth": 2,
                    "dim_label": "风控", "name": risk_node.name,
                    "overdue_rate": risk_node.overdue_rate,
                    "change_abs": risk_node.change_abs,
                    "contribution_pct": risk_node.contribution_pct,
                    "alert_icon": risk_node.alert_icon,
                    "alert_level": risk_node.alert_level,
                    "impact": risk_impact,
                    "text": (
                        f"    → 风控: {risk_node.name}  首逾 {risk_node.overdue_rate:.1%}  "
                        f"{risk_arrow}{abs(risk_node.change_abs):.2%}  "
                        f"{risk_contrib}  {risk_node.alert_icon}  "
                        f"影响{format_impact(risk_impact)}"
                    ),
                })

    return rows


def _has_anomaly_descendant(node: HierarchicalNode) -> bool:
    """检查节点是否有非 GREEN 的子节点"""
    if node.alert_level != "GREEN":
        return True
    for child in node.children:
        if _has_anomaly_descendant(child):
            return True
    return False


# ============================================================
#  摘要 & 建议
# ============================================================

def _find_worst_path_label(tree: HierarchicalNode) -> str:
    """找最差下钻路径的标签（用于摘要）"""
    if not tree or not tree.children:
        return ""

    # 找整体变化最大的产品类型
    worst_pt = max(tree.children, key=lambda c: abs(c.contribution_to_overall))

    # 在其下找贡献最大的包体
    if not worst_pt.children:
        return worst_pt.name
    worst_pkg = max(worst_pt.children, key=lambda c: abs(c.contribution_pct))

    # 在其下找贡献最大的风控等级
    if not worst_pkg.children:
        return f"{worst_pt.name}·{worst_pkg.name}"
    worst_risk = max(worst_pkg.children, key=lambda c: abs(c.contribution_pct))

    return f"{worst_pt.name}·{worst_pkg.name}·{worst_risk.name}"


def _build_tree_anomaly_sources(tree: HierarchicalNode) -> Dict[str, List[Dict]]:
    """从层级树构建异常来源（仅异常节点）。"""
    sources = {}
    overall_change = tree.change_abs

    for pt_node in tree.children:
        if pt_node.alert_level == "GREEN":
            continue

        pt_arrow = change_arrow(pt_node.change_abs)
        key = f"{pt_arrow} {pt_node.name}"
        sources[key] = []

        # Top contributing children
        all_children = []
        for child in pt_node.children:
            contrib_to_overall = abs(child.contribution_to_overall / overall_change) if abs(overall_change) > 0.0001 else 0
            all_children.append((child, contrib_to_overall))
        all_children.sort(key=lambda x: x[1], reverse=True)

        for child, contrib_overall in all_children[:3]:
            sources[key].append({
                "bucket": child.name,
                "contribution_pct": contrib_overall,
                "overdue_rate": child.overdue_rate,
                "alert_icon": child.alert_icon,
            })

    return sources


def _generate_recommendations(analysis: AnalysisReport) -> List[str]:
    """生成管理层建议"""
    recs = []
    if not analysis.has_anomaly:
        return recs

    if analysis.tree:
        path = _find_worst_path_label(analysis.tree)
        if path:
            recs.append(f"重点关注：{path}")

    if analysis.continuous_alert and analysis.continuous_alert.is_continuous_anomaly:
        recs.append(f"🔥 {analysis.continuous_alert.warning_text}")

    if analysis.stage == "D0":
        recs.append("建议核查当日 D0 入催案件分布及催收覆盖率")
    elif analysis.stage == "D1":
        recs.append("D1 异常建议回溯 D0 催收执行情况")

    return recs[:3]


# ============================================================
#  详细区块（文本降级）
# ============================================================

def _build_detail_sections(analysis: AnalysisReport) -> List[ReportSection]:
    """构建详细信息区块"""
    sections = []

    if analysis.overall:
        o = analysis.overall
        rows = [{"label": "金额首逾率", "value": f"{o.overdue_rate:.2%}"}]
        for comp in o.comparisons:
            arrow = change_arrow(comp.change_abs)
            rows.append({
                "label": config.COMPARISON_METHODS.get(comp.method, {}).get("label", comp.method),
                "value": f"{arrow}{abs(comp.change_abs):.2%}",
                "alert_icon": comp.alert_icon,
            })
        sections.append(ReportSection(title="整体首逾", rows=rows, highlight=analysis.has_anomaly))

    if analysis.tree and analysis.tree.children:
        tree_rows = _flatten_tree(analysis.tree, hide_green=config.HIDE_GREEN_NODES)
        rows = [{"label": row["text"], "value": ""} for row in tree_rows]
        sections.append(ReportSection(title="层级下钻", rows=rows, highlight=analysis.has_anomaly))

    return sections


def _level_rank(level: str) -> int:
    return {"RED": 3, "ORANGE": 2, "YELLOW": 1, "GREEN": 0}.get(level, 0)
