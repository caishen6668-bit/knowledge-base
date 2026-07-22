"""
分析引擎核心 — 比较函数 + 告警判定 + 指标计算

所有比较函数返回统一的 ComparisonResult，方便上层组合使用。
后续新增比较维度只需添加一个函数。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import config


# ============================================================
#  数据结构
# ============================================================

@dataclass
class ComparisonResult:
    """一次比较的结果"""
    method: str                      # "vs_yesterday" | "vs_7days" | "vs_target"
    current_value: float             # 当前值
    baseline_value: float            # 基线值
    change_abs: float                # 绝对变化（百分点，小数形式）
    change_pct: float                # 相对变化（百分比，小数形式）
    alert_level: str                 # "RED" | "ORANGE" | "YELLOW" | "GREEN"
    alert_icon: str                  # "🔴" | "🟠" | "🟡" | "🟢"
    alert_label: str                 # "严重" | "警告" | "关注" | "正常"


@dataclass
class DimensionResult:
    """一个维度切片的分析结果"""
    dimension: str                   # "overall" | "package" | "risk_level"
    bucket: str                      # "分期" | "新客" | ... (overall 用 "total")
    stage: str                       # "D0" | "D1"
    overdue_rate: float              # 当前首逾率
    due_amount: float                # 入催本金
    pay_amount: float                # 回款金额
    baseline_overdue_rate: float = 0.0    # 基线首逾率（用于贡献度计算）
    baseline_due_amount: float = 0.0      # 基线入催本金（用于贡献度计算）
    case_count: int = 0                   # 案件数（用于样本过滤）
    comparisons: List[ComparisonResult] = field(default_factory=list)


# ============================================================
#  比较函数（封装，后续可扩展）
# ============================================================

def compare(current_value: float, baseline_value: float,
            method: str = "vs_yesterday",
            stage: str = "D0") -> ComparisonResult:
    """
    通用比较函数。

    Args:
        current_value: 当前指标值（如 0.3354 = 33.54%）
        baseline_value: 基线值（如 0.3230 = 32.30%）
        method: 比较方法标识
        stage: 分析阶段（用于读取该阶段的告警阈值）

    Returns:
        ComparisonResult
    """
    if baseline_value == 0:
        # 无基线 → 无法比较，默认 GREEN
        return ComparisonResult(
            method=method,
            current_value=current_value,
            baseline_value=baseline_value,
            change_abs=0.0,
            change_pct=0.0,
            alert_level="GREEN",
            alert_icon=config.ALERT_DISPLAY["GREEN"]["icon"],
            alert_label=config.ALERT_DISPLAY["GREEN"]["label"],
        )

    change_abs = current_value - baseline_value          # 百分点差
    change_pct = change_abs / abs(baseline_value) if baseline_value != 0 else 0.0

    alert_level = _determine_alert_level(change_abs, stage)
    display = config.ALERT_DISPLAY[alert_level]

    return ComparisonResult(
        method=method,
        current_value=current_value,
        baseline_value=baseline_value,
        change_abs=round(change_abs, 4),
        change_pct=round(change_pct, 4),
        alert_level=alert_level,
        alert_icon=display["icon"],
        alert_label=display["label"],
    )


def compare_today_vs_yesterday(current: float, yesterday: float,
                               stage: str = "D0") -> ComparisonResult:
    """今日 vs 昨日"""
    return compare(current, yesterday, method="vs_yesterday", stage=stage)


def compare_today_vs_7days(current: float, avg_7days: float,
                           stage: str = "D0") -> ComparisonResult:
    """今日 vs 过去 7 日均值"""
    return compare(current, avg_7days, method="vs_7days", stage=stage)


def compare_today_vs_target(current: float, target: float,
                            stage: str = "D0") -> ComparisonResult:
    """今日 vs 预设目标"""
    return compare(current, target, method="vs_target", stage=stage)


# ============================================================
#  告警等级判定（按阶段读取阈值）
# ============================================================

def _determine_alert_level(change_abs: float, stage: str = "D0") -> str:
    """
    根据绝对变化 + 阶段判定四级告警。

    change_abs 是小数形式（如 0.05 = 5 百分点），阈值定义在 config.ALERT_RULES 中。
    只有首逾率上升（正数）才算恶化；下降（负数）永远为 GREEN。

    Returns:
        "RED" | "ORANGE" | "YELLOW" | "GREEN"
    """
    # 首逾率下降 = 改善，不告警
    if change_abs <= 0:
        return "GREEN"

    # 转为百分点与阈值比较
    pp = change_abs * 100

    # 读取该阶段的阈值（fallback 到 D0）
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


def is_anomaly(alert_level: str) -> bool:
    """是否为异常等级（需要关注）"""
    return alert_level in ("RED", "ORANGE", "YELLOW")


def is_sample_too_small(due_amount: float, case_count: int = 0) -> bool:
    """
    样本过滤：订单太少或本金太小 → 不报警。
    阈值来自 config.MIN_CASE / config.MIN_AMOUNT。
    """
    if due_amount < config.MIN_AMOUNT:
        return True
    if case_count > 0 and case_count < config.MIN_CASE:
        return True
    return False


# ============================================================
#  展示辅助（V1 Release）
# ============================================================

def change_arrow(change_abs: float) -> str:
    """变化方向箭头：↑ 上涨 / ↓ 下降 / → 持平"""
    if change_abs > 0.0005:
        return "↑"
    elif change_abs < -0.0005:
        return "↓"
    else:
        return "→"


def calc_impact_amount(contribution_pp: float, total_due: float) -> float:
    """
    计算影响金额 = 贡献百分点 × 整体入催本金。
    例如: 贡献 0.1083pp × 入催 1,086,717 = 117,692
    """
    return abs(contribution_pp) * total_due


def format_impact(amount: float) -> str:
    """格式化影响金额为可读字符串（中文习惯：万/亿）"""
    if amount >= 100_000_000:       # ≥ 1亿
        return f"{amount / 100_000_000:.1f}亿"
    elif amount >= 10_000:           # ≥ 1万
        return f"{amount / 10_000:.1f}万"
    else:
        return f"{amount:,.0f}"


# ============================================================
#  指标计算
# ============================================================

def calc_overdue_rate(pay: float, due: float) -> float:
    """计算首逾率 = 1 - 回收率"""
    if due <= 0:
        return 0.0
    return 1.0 - (pay / due)


def calc_recovery_rate(pay: float, due: float) -> float:
    """计算回收率"""
    if due <= 0:
        return 0.0
    return pay / due


def calc_contribution(bucket_change_abs: float,
                      overall_change_abs: float) -> float:
    """
    计算异常贡献率。

    贡献率 = 该维度贡献(pp) / 整体变化(pp)
    调用前应先通过 calc_dimension_contribution_pp() 算出维度贡献。

    Args:
        bucket_change_abs: 该维度的绝对变化（百分点，小数形式）
        overall_change_abs: 整体的绝对变化（百分点，小数形式）

    Returns:
        贡献率 (0.0 ~ 1.0)
    """
    if abs(overall_change_abs) < 0.0001:
        return 0.0
    return bucket_change_abs / overall_change_abs


def calc_dimension_contribution_pp(
        bucket_due_current: float,
        total_due_current: float,
        bucket_rate_current: float,
        bucket_rate_baseline: float,
) -> float:
    """
    计算某维度对整体首逾率变化的贡献（百分点）— 管理层版本。

    使用"率效应"公式（保持当前权重不变）：
      contribution = weight_current × (rate_current - rate_baseline)

    其中 weight_current = bucket_due_current / total_due_current。
    所有 bucket 贡献之和 ≤ 整体变化，"其它" = 整体变化 - 已解释部分。

    这样展示的贡献率永远清晰：A包贡献59.6%, B包贡献26.9%, 其它13.5%。

    Args:
        bucket_due_current: 当前期 bucket 入催本金
        total_due_current: 当前期整体入催本金
        bucket_rate_current: 当前期 bucket 首逾率
        bucket_rate_baseline: 基线期 bucket 首逾率

    Returns:
        贡献百分点（小数形式，如 0.0062 = 0.62pp）
    """
    if total_due_current <= 0:
        return 0.0

    w_curr = bucket_due_current / total_due_current
    return w_curr * (bucket_rate_current - bucket_rate_baseline)


# ============================================================
#  多维度分析辅助
# ============================================================

def analyze_dimension(name: str, buckets: Dict[str, dict],
                      stage: str, baselines: Optional[Dict[str, float]] = None,
                      methods: Optional[List[str]] = None) -> List[DimensionResult]:
    """
    对某个维度的所有切片执行分析。

    Args:
        name: 维度名 ("product" | "cust_type" | ...)
        buckets: {bucket_name: {stage: {overdue_rate, due, pay}}}
        stage: 分析阶段 ("D0" | "D1")
        baselines: 基线数据 {bucket_name: overdue_rate}，用于比较
        methods: 启用的比较方法列表

    Returns:
        [DimensionResult, ...]
    """
    if methods is None:
        methods = config.DEFAULT_COMPARISONS
    if baselines is None:
        baselines = {}

    results = []
    for bucket_name, stage_data in sorted(buckets.items()):
        s = stage_data.get(stage, {})
        current_rate = s.get("overdue_rate", 0.0)

        comparisons = []
        for method in methods:
            baseline_val = baselines.get(bucket_name, 0.0)
            comp = compare(current_rate, baseline_val, method=method, stage=stage)
            comparisons.append(comp)

        results.append(DimensionResult(
            dimension=name,
            bucket=bucket_name,
            stage=stage,
            overdue_rate=current_rate,
            due_amount=s.get("due", 0.0),
            pay_amount=s.get("pay", 0.0),
            comparisons=comparisons,
        ))

    return results
