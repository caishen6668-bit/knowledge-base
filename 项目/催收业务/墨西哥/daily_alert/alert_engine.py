"""
首逾智能预警引擎 — 层级下钻分析

分析流程（按 config.HIERARCHY 顺序）：
  整体 → 产品类型（单期/分期）
       → 包体（非分期/借款分期/展期分期/展期N期）
           → 订单风控等级（新客/老客）

每一层通过过滤 raw_rows 做真正的交叉下钻，
而不是独立维度的简单拼接。
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field

from . import config
from .analyzer import (
    ComparisonResult,
    DimensionResult,
    compare,
    analyze_dimension as _low_level_analyze,
    calc_contribution,
    calc_dimension_contribution_pp,
    is_anomaly,
    is_sample_too_small,
)


# ============================================================
#  数据结构
# ============================================================

@dataclass
class ContributionItem:
    """贡献度条目"""
    dimension: str
    bucket: str
    change_abs: float
    contribution_pct: float
    overdue_rate: float
    alert_level: str
    dim_label: str = ""
    bucket_label: str = ""


@dataclass
class ContinuousAlert:
    """连续异常监控结果"""
    consecutive_up: int = 0
    consecutive_above_avg: int = 0
    is_continuous_anomaly: bool = False
    warning_text: str = ""


@dataclass
class HierarchicalNode:
    """层级下钻树的一个节点"""
    name: str                          # 节点名称（"单期" | "非分期" | "新客" | ...）
    dim_key: str                       # 维度 key（"product_type" | "package" | "risk_level"）
    level: int                         # 0=overall, 1=product_type, 2=package, 3=risk_level
    overdue_rate: float = 0.0          # 当前首逾率
    due_amount: float = 0.0            # 入催本金
    baseline_overdue_rate: float = 0.0 # 基线首逾率
    change_abs: float = 0.0            # 绝对变化（pp，小数形式）
    alert_icon: str = ""               # 告警图标
    alert_level: str = "GREEN"         # 告警等级

    # 贡献度（相对父节点）
    contribution_to_parent: float = 0.0  # 贡献百分点（小数）
    contribution_pct: float = 0.0        # 贡献率（0~1）

    # 贡献度（相对整体 — 用于影响金额计算）
    contribution_to_overall: float = 0.0  # 贡献到整体的百分点（小数）

    children: List['HierarchicalNode'] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """完整的分析报告"""
    country_code: str
    country_name: str
    due_week_current: str
    due_week_baseline: str
    stage: str
    run_date: str = ""           # 运行日期（今天）
    business_date: str = ""      # 业务日期（昨天 = run_date - 1）

    # Layer 1: 整体
    overall: Optional[DimensionResult] = None

    # 层级下钻树（V1.1 新增 — 替代扁平 dimension_results）
    tree: Optional[HierarchicalNode] = None

    # 扁平维度结果（保留向后兼容）
    dimension_results: Dict[str, List[DimensionResult]] = field(default_factory=dict)

    # 贡献度
    contributions: List[ContributionItem] = field(default_factory=list)

    # 连续异常
    continuous_alert: Optional[ContinuousAlert] = None

    # 异常汇总
    has_anomaly: bool = False
    anomaly_summary: str = ""


# ============================================================
#  Raw-row 工具函数
# ============================================================

def _filter_rows(rows: List[dict], dim_key: str, bucket: str) -> List[dict]:
    """
    按维度过滤 raw_rows。

    根据 config.DIMENSIONS[dim_key] 中的配置决定过滤方式：
      - filter_fn="map": 用 ORDER_TYPE_MAP 映射后匹配
      - filter_fn="direct": 直接匹配 raw 字段值
    """
    dim = config.DIMENSIONS.get(dim_key, {})
    source_field = dim.get("source_field", dim.get("field", ""))
    filter_fn = dim.get("filter_fn", "direct")

    if filter_fn == "map":
        # 产品类型：order_type → 映射 → 比较
        return [r for r in rows
                if config.ORDER_TYPE_MAP.get(r.get(source_field, ""), "") == bucket]
    else:
        # 直接匹配
        return [r for r in rows
                if r.get(source_field, "") == bucket]


def _aggregate_rows(rows: List[dict], stage: str) -> dict:
    """
    聚合 raw_rows 为 {due, pay, overdue_rate}。

    使用统一首逾计算函数 calculate_first_overdue_rate（BI 口径）。

    Args:
        rows: 原始数据行列表
        stage: 阶段 ("D0" | "D1" | ...)

    Returns:
        {"due": float, "pay": float, "overdue_rate": float}
    """
    from .quickbi import calculate_first_overdue_rate

    due = 0.0
    pay = 0.0

    for r in rows:
        rcalc = calculate_first_overdue_rate(r, stage)
        due += rcalc["due_amt"]
        pay += rcalc["cum_pay"]

    overdue_rate = 1.0 - (pay / due) if due > 0 else 0.0
    return {"due": round(due, 2), "pay": round(pay, 2), "overdue_rate": round(overdue_rate, 4)}


# ============================================================
#  层级下钻分析
# ============================================================

def analyze_hierarchical(current_data: dict, baseline_data: dict,
                         stage: str = "D0") -> HierarchicalNode:
    """
    构建层级下钻树：整体 → 产品类型 → 包体 → 订单风控等级。

    每一层通过过滤 raw_rows 做真正的交叉下钻。
    例如 "分期" 下的包体，只统计 product_type=分期的 raw_rows。

    Returns:
        HierarchicalNode (root, level=0, name="overall")
    """
    curr_rows = current_data.get("raw_rows", [])
    base_rows = baseline_data.get("raw_rows", []) if baseline_data else []

    curr_overall = current_data.get("overall", {}).get(stage, {})
    base_overall = baseline_data.get("overall", {}).get(stage, {}) if baseline_data else {}

    # Root: overall
    overall_rate = curr_overall.get("overdue_rate", 0.0)
    base_rate = base_overall.get("overdue_rate", 0.0) if base_overall else 0.0
    overall_due = curr_overall.get("due", 0.0)
    overall_change = overall_rate - base_rate

    alert_level = "GREEN"
    if overall_change > 0:
        alert_level = _determine_alert_level_for_stage(overall_change, stage)
    alert_icon = config.ALERT_DISPLAY[alert_level]["icon"]

    root = HierarchicalNode(
        name="overall", dim_key="overall", level=0,
        overdue_rate=overall_rate,
        due_amount=overall_due,
        baseline_overdue_rate=base_rate,
        change_abs=overall_change,
        alert_icon=alert_icon,
        alert_level=alert_level,
    )

    if not curr_rows or overall_due <= 0:
        return root

    # 递归构建下钻树（按 HIERARCHY 顺序）
    _build_children(
        parent=root,
        parent_curr_rows=curr_rows,
        parent_base_rows=base_rows,
        parent_due=overall_due,
        hierarchy_idx=0,
        stage=stage,
    )

    # 后处理：计算每个节点对整体的贡献（用于影响金额显示）
    _compute_overall_contributions(root, overall_due)

    return root


def _compute_overall_contributions(node: HierarchicalNode, total_due: float):
    """递归计算每个节点对整体的贡献百分点。"""
    if node.level > 0 and total_due > 0:
        node.contribution_to_overall = calc_dimension_contribution_pp(
            bucket_due_current=node.due_amount,
            total_due_current=total_due,
            bucket_rate_current=node.overdue_rate,
            bucket_rate_baseline=node.baseline_overdue_rate,
        )
    for child in node.children:
        _compute_overall_contributions(child, total_due)


def _build_children(parent: HierarchicalNode,
                    parent_curr_rows: List[dict],
                    parent_base_rows: List[dict],
                    parent_due: float,
                    hierarchy_idx: int,
                    stage: str):
    """
    递归为父节点构建子节点。

    Args:
        parent: 父节点
        parent_curr_rows: 父节点范围内的当前 raw_rows
        parent_base_rows: 父节点范围内的基线 raw_rows
        parent_due: 父节点的入催本金
        hierarchy_idx: 当前在 HIERARCHY 列表中的位置
        stage: 分析阶段
    """
    if hierarchy_idx >= len(config.HIERARCHY):
        return

    dim_key = config.HIERARCHY[hierarchy_idx]
    dim_config = config.DIMENSIONS.get(dim_key, {})
    if not dim_config.get("enabled", False):
        # 跳过禁用维度，继续下一层
        _build_children(parent, parent_curr_rows, parent_base_rows,
                        parent_due, hierarchy_idx + 1, stage)
        return

    buckets = dim_config.get("buckets", [])

    for bucket in buckets:
        # 过滤当前父节点范围内的数据
        curr_filtered = _filter_rows(parent_curr_rows, dim_key, bucket)
        base_filtered = _filter_rows(parent_base_rows, dim_key, bucket)

        if not curr_filtered:
            continue

        # 聚合
        curr_agg = _aggregate_rows(curr_filtered, stage)
        base_agg = _aggregate_rows(base_filtered, stage) if base_filtered else {"overdue_rate": 0.0, "due": 0.0}

        bucket_rate = curr_agg["overdue_rate"]
        bucket_due = curr_agg["due"]
        bucket_base_rate = base_agg["overdue_rate"]

        # 样本过滤
        if is_sample_too_small(bucket_due):
            continue

        # 变化
        change = bucket_rate - bucket_base_rate if bucket_base_rate else 0.0
        alert_level = "GREEN"
        if change > 0:
            alert_level = _determine_alert_level_for_stage(change, stage)

        # 贡献到父节点
        contrib_pp = calc_dimension_contribution_pp(
            bucket_due_current=bucket_due,
            total_due_current=parent_due,
            bucket_rate_current=bucket_rate,
            bucket_rate_baseline=bucket_base_rate,
        )
        contrib_pct = calc_contribution(contrib_pp, parent.change_abs)

        child = HierarchicalNode(
            name=bucket,
            dim_key=dim_key,
            level=parent.level + 1,
            overdue_rate=bucket_rate,
            due_amount=bucket_due,
            baseline_overdue_rate=bucket_base_rate,
            change_abs=round(change, 4),
            alert_icon=config.ALERT_DISPLAY[alert_level]["icon"],
            alert_level=alert_level,
            contribution_to_parent=round(contrib_pp, 4),
            contribution_pct=round(contrib_pct, 4),
        )

        # 递归下一层
        _build_children(
            parent=child,
            parent_curr_rows=curr_filtered,
            parent_base_rows=base_filtered,
            parent_due=bucket_due,
            hierarchy_idx=hierarchy_idx + 1,
            stage=stage,
        )

        parent.children.append(child)


def _determine_alert_level_for_stage(change_abs: float, stage: str) -> str:
    """根据阶段阈值判定告警等级"""
    if change_abs <= 0:
        return "GREEN"
    pp = change_abs * 100
    rules = config.ALERT_RULES.get(stage, config.ALERT_RULES.get("D0", {}))
    if not rules:
        return "GREEN"
    if pp >= rules.get("red", 999):
        return "RED"
    elif pp >= rules.get("orange", 999):
        return "ORANGE"
    elif pp >= rules.get("yellow", 999):
        return "YELLOW"
    else:
        return "GREEN"


# ============================================================
#  Layer 1: 整体首逾分析
# ============================================================

def analyze_overall(current_data: dict, baseline_data: dict,
                    stage: str = "D0") -> DimensionResult:
    """整体首逾率分析。"""
    curr = current_data["overall"].get(stage, {})
    base = baseline_data.get("overall", {}).get(stage, {}) if baseline_data else {}

    curr_rate = curr.get("overdue_rate", 0.0)
    base_rate = base.get("overdue_rate", 0.0) if base else 0.0

    result = DimensionResult(
        dimension="overall", bucket="total", stage=stage,
        overdue_rate=curr_rate,
        due_amount=curr.get("due", 0.0),
        pay_amount=curr.get("pay", 0.0),
        baseline_overdue_rate=base_rate,
        baseline_due_amount=base.get("due", 0.0) if base else 0.0,
    )
    comp = compare(curr_rate, base_rate, method="vs_yesterday", stage=stage)
    result.comparisons.append(comp)
    return result


# ============================================================
#  Layer 2-3: 通用维度分析（扁平，保留向后兼容）
# ============================================================

def analyze_dimension(dim_key: str,
                      current_data: dict,
                      baseline_data: dict,
                      stage: str = "D0",
                      case_volumes: Optional[Dict] = None) -> List[DimensionResult]:
    """对任意配置维度执行拆分分析（扁平版本，供兼容使用）。"""
    dim_config = config.DIMENSIONS.get(dim_key, {})
    if not dim_config.get("enabled", False):
        return []

    field = dim_config["field"]
    curr_buckets = current_data.get("dimensions", {}).get(field, {})
    base_buckets = baseline_data.get("dimensions", {}).get(field, {}) if baseline_data else {}

    baselines = {}
    for bucket_name, stage_data in base_buckets.items():
        s = stage_data.get(stage, {})
        baselines[bucket_name] = s.get("overdue_rate", 0.0)

    results = _low_level_analyze(
        name=dim_key, buckets=curr_buckets, stage=stage,
        baselines=baselines, methods=config.DEFAULT_COMPARISONS,
    )

    filtered = []
    for dr in results:
        base_bucket = base_buckets.get(dr.bucket, {}).get(stage, {})
        dr.baseline_overdue_rate = base_bucket.get("overdue_rate", 0.0)
        dr.baseline_due_amount = base_bucket.get("due", 0.0)
        if case_volumes:
            dr.case_count = case_volumes.get((field, dr.bucket), 0)
        if is_sample_too_small(dr.due_amount, dr.case_count):
            continue
        filtered.append(dr)

    return filtered


# ============================================================
#  Layer 4: 贡献度（从层级树中提取）
# ============================================================

def analyze_contribution_from_tree(tree: HierarchicalNode) -> List[ContributionItem]:
    """
    从层级下钻树中提取贡献度（贡献到整体）。
    按贡献率降序排列。
    """
    items = []
    overall_change = tree.change_abs
    overall_due = tree.due_amount

    def _collect(node: HierarchicalNode):
        if node.level > 0 and node.due_amount > 0:
            # 计算该节点对整体的贡献
            contrib_to_overall = calc_dimension_contribution_pp(
                bucket_due_current=node.due_amount,
                total_due_current=overall_due,
                bucket_rate_current=node.overdue_rate,
                bucket_rate_baseline=node.baseline_overdue_rate,
            )
            contrib_pct_overall = calc_contribution(contrib_to_overall, overall_change)

            dim_config = config.DIMENSIONS.get(node.dim_key, {})
            items.append(ContributionItem(
                dimension=node.dim_key,
                bucket=node.name,
                change_abs=round(contrib_to_overall, 4),
                contribution_pct=round(contrib_pct_overall, 4),
                overdue_rate=node.overdue_rate,
                alert_level=node.alert_level,
                dim_label=dim_config.get("label", node.dim_key),
                bucket_label=node.name,
            ))
        for child in node.children:
            _collect(child)

    _collect(tree)
    items.sort(key=lambda x: abs(x.contribution_pct), reverse=True)
    return items


# ============================================================
#  连续异常检测
# ============================================================

def check_continuous_anomaly(overall: DimensionResult,
                             historical_data: Optional[List[dict]] = None) -> ContinuousAlert:
    """检测连续异常。"""
    alert = ContinuousAlert()
    if not config.ENABLE_CONTINUOUS_ALERT:
        return alert
    if not historical_data or len(historical_data) < 2:
        return alert

    is_up = any(c.change_abs > 0 for c in overall.comparisons)
    if is_up:
        alert.consecutive_up = 1
        for h in historical_data[1:]:
            if h.get("change_abs", 0) > 0:
                alert.consecutive_up += 1
            else:
                break

    avg_7d = sum(h.get("overdue_rate", 0) for h in historical_data[:7]) / min(7, len(historical_data))
    if overall.overdue_rate > avg_7d:
        alert.consecutive_above_avg = 1
        for h in historical_data[:6]:
            if h.get("overdue_rate", 0) > avg_7d:
                alert.consecutive_above_avg += 1
            else:
                break

    triggers = []
    if alert.consecutive_up >= config.CONTINUOUS_DAYS_UP:
        triggers.append(f"连续{alert.consecutive_up}天环比上涨")
    if alert.consecutive_above_avg >= config.CONTINUOUS_DAYS_ABOVE_AVG:
        triggers.append(f"连续{alert.consecutive_above_avg}天高于7日均值")

    if triggers:
        alert.is_continuous_anomaly = True
        alert.warning_text = "🔥 " + "，".join(triggers)

    return alert


# ============================================================
#  完整分析编排
# ============================================================

def run_full_analysis(current_data: dict, baseline_data: dict,
                      country_code: str,
                      stage: str = "D0",
                      case_volumes: Optional[Dict] = None,
                      historical_data: Optional[List[dict]] = None,
                      run_date: str = "",
                      business_date: str = "") -> AnalysisReport:
    """
    执行完整的层级下钻分析。

    流程：
      1. 整体首逾率
      2. 层级下钻树（整体→产品类型→包体→风控等级）
      3. 从树中提取贡献度
      4. 连续异常检测
    """
    country = config.COUNTRIES[country_code]

    report = AnalysisReport(
        country_code=country_code,
        country_name=country["name"],
        due_week_current=current_data["meta"]["due_week"],
        due_week_baseline=baseline_data.get("meta", {}).get("due_week", "N/A") if baseline_data else "N/A",
        stage=stage,
        run_date=run_date,
        business_date=business_date,
    )

    # Layer 1: 整体
    report.overall = analyze_overall(current_data, baseline_data, stage)

    if report.overall and is_sample_too_small(report.overall.due_amount):
        report.has_anomaly = False
        report.anomaly_summary = f"样本量不足（入催本金 {report.overall.due_amount:,.0f} < {config.MIN_AMOUNT:,}），跳过分析"
        return report

    # 层级下钻树
    report.tree = analyze_hierarchical(current_data, baseline_data, stage)

    # 扁平维度（向后兼容）
    for dim_key in sorted(config.DIMENSIONS.keys(),
                          key=lambda k: config.DIMENSIONS[k].get("order", 99)):
        dim_config = config.DIMENSIONS[dim_key]
        if not dim_config.get("enabled", False):
            continue
        results = analyze_dimension(dim_key, current_data, baseline_data, stage, case_volumes)
        if results:
            report.dimension_results[dim_key] = results

    # 贡献度（从层级树提取）
    if report.tree:
        report.contributions = analyze_contribution_from_tree(report.tree)

    # 连续异常
    report.continuous_alert = check_continuous_anomaly(report.overall, historical_data)

    # 异常判定
    overall_anomaly = any(
        is_anomaly(c.alert_level) for c in report.overall.comparisons)
    report.has_anomaly = overall_anomaly

    if overall_anomaly:
        worst = _worst_level(report.overall.comparisons)
        tree_leaves = _count_tree_nodes(report.tree)
        report.anomaly_summary = (
            f"整体首逾率异常 ({config.ALERT_DISPLAY[worst]['label']})，"
            f"已层级下钻 {tree_leaves} 个节点"
        )
    else:
        report.anomaly_summary = "整体首逾率正常"

    return report


# ============================================================
#  辅助
# ============================================================

def _level_rank(level: str) -> int:
    return {"RED": 3, "ORANGE": 2, "YELLOW": 1, "GREEN": 0}.get(level, 0)


def _worst_level(comparisons: List[ComparisonResult]) -> str:
    worst = "GREEN"
    for c in comparisons:
        if _level_rank(c.alert_level) > _level_rank(worst):
            worst = c.alert_level
    return worst


def _count_tree_nodes(node: Optional[HierarchicalNode]) -> int:
    """统计树中节点数（不含 root）"""
    if node is None:
        return 0
    count = 0 if node.level == 0 else 1
    for child in node.children:
        count += _count_tree_nodes(child)
    return count
