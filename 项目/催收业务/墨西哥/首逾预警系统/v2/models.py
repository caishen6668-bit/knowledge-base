"""
V2 数据模型 — 趋势分析统一数据结构

所有后续 AI 分析、异常定位、飞书预警全部基于 TrendResult。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict


# ============================================================
#  趋势比较结果（单次比较）
# ============================================================

@dataclass
class TrendComparison:
    """一次趋势比较的完整结果"""
    method: str                      # "dod" | "3d_avg" | "7d_avg" | "target"
    method_label: str                # "较昨日" | "较近3日均值" | "较近7日均值" | "较目标值"
    current_value: float             # 当前值（首逾率，小数形式）
    baseline_value: float            # 基线值（小数形式）
    change_abs: float                # 绝对变化（百分点，小数形式）
    change_pct: float                # 相对变化（百分比，小数形式，0.1=10%）
    alert_level: str                 # "GREEN" | "YELLOW" | "ORANGE" | "RED"
    alert_icon: str                  # "🟢" | "🟡" | "🟠" | "🔴"
    alert_label: str                 # "正常" | "关注" | "警告" | "严重"
    threshold_used: float = 0.0      # 触发当前等级的阈值（pp，小数形式）
    is_improvement: bool = False     # True = 首逾率下降（改善方向）


# ============================================================
#  TrendResult — 统一趋势分析结果
# ============================================================

@dataclass
class TrendResult:
    """
    一个维度切片的完整趋势分析结果。

    这是 V2 的核心输出类型。所有下游消费者（AI 分析、异常定位、
    飞书预警）都从这个结构读取数据。
    """
    # ---- 身份 ----
    dimension: str                   # "overall" | "product_type" | "package" | "risk_level"
    bucket: str                      # "total" | "单期" | "非分期" | "A" | ...
    dim_label: str = ""              # 维度中文名: "整体" | "产品类型" | "包体" | "订单风控等级"
    bucket_label: str = ""           # 桶中文名（=bucket 大多数情况）
    stage: str = "D0"                # 分析阶段

    # ---- 当前值 ----
    current_value: float = 0.0       # 当前首逾率（小数形式）
    due_amount: float = 0.0          # 到期本金
    pay_amount: float = 0.0          # 回款金额
    case_count: int = 0              # 到期笔数

    # ---- 所有比较 ----
    comparisons: List[TrendComparison] = field(default_factory=list)

    # ---- 综合判断（取所有 comparison 中最严重的等级） ----
    overall_judgment: str = "GREEN"  # "GREEN" | "YELLOW" | "ORANGE" | "RED"
    overall_icon: str = "🟢"
    overall_label: str = "正常"

    # ---- 附加信息 ----
    historical_values: Dict[str, float] = field(default_factory=dict)
    # {"2026-07-06": 0.3185, "2026-07-05": 0.3201, ...}

    is_sample_small: bool = False    # 样本量是否太小
    warnings: List[str] = field(default_factory=list)  # 补充警告信息

    def __post_init__(self):
        """自动计算综合判断"""
        if not self.overall_judgment or self.overall_judgment == "GREEN":
            self._recalc_judgment()

    def _recalc_judgment(self):
        """根据所有 comparison 重新计算综合判断"""
        rank = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
        worst = "GREEN"
        for c in self.comparisons:
            if rank.get(c.alert_level, 0) > rank.get(worst, 0):
                worst = c.alert_level

        from .. import config as v1_config
        display = v1_config.ALERT_DISPLAY.get(worst, v1_config.ALERT_DISPLAY["GREEN"])
        self.overall_judgment = worst
        self.overall_icon = display["icon"]
        self.overall_label = display["label"]


# ============================================================
#  TrendReport — 一次完整运行的趋势分析报告
# ============================================================

@dataclass
class TrendReport:
    """单次 V2 运行的完整输出"""
    country_code: str
    country_name: str
    business_date: str               # 业务日期 "2026-07-06"
    stage: str                       # "D0" | "D1" | ...
    run_date: str = ""               # 运行日期

    # ---- 所有维度的 TrendResult ----
    results: List[TrendResult] = field(default_factory=list)

    # ---- 按维度索引（快速查询） ----
    _index: Dict[str, Dict[str, 'TrendResult']] = field(default_factory=dict, repr=False)

    # ---- 汇总 ----
    total_dimensions: int = 0
    anomaly_count: int = 0           # overall_judgment != GREEN 的结果数
    worst_overall: str = "GREEN"     # 最严重的 overall_judgment

    def __post_init__(self):
        if self._index is None:
            self._index = {}

    def get(self, dimension: str, bucket: str) -> Optional['TrendResult']:
        """按维度+桶快速查询"""
        return self._index.get(dimension, {}).get(bucket)

    def get_anomalies(self) -> List['TrendResult']:
        """获取所有非 GREEN 的结果"""
        return [r for r in self.results if r.overall_judgment != "GREEN"]

    def get_by_severity(self, level: str) -> List['TrendResult']:
        """按严重等级筛选"""
        return [r for r in self.results if r.overall_judgment == level]

    def summary(self) -> str:
        """一行摘要"""
        n = self.anomaly_count
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "ORANGE": "🟠", "RED": "🔴"}.get(self.worst_overall, "🟢")
        return (f"{self.country_name} {self.stage} | {self.business_date} | "
                f"{icon} {self.worst_overall} | {n}/{self.total_dimensions} dimensions abnormal")


# ============================================================
#  RootCause — 异常根因定位
# ============================================================

@dataclass
class RootCauseNode:
    """层级下钻树中的一个节点（用于根因定位）"""
    dim_key: str                     # "overall" | "product_type" | "order_type" | "order_grade"
    bucket: str                      # "整体" | "单期" | "分期" | "非分期" | "A" | ...
    dim_label: str = ""              # 维度中文名
    bucket_label: str = ""           # 桶中文名
    level: int = 0                   # 0=整体, 1=产品类型, 2=包体, 3=订单风控等级

    # ---- 当前 vs 基线 ----
    current_rate: float = 0.0        # 当前首逾率
    baseline_rate: float = 0.0       # 基线首逾率
    change_abs: float = 0.0          # 变化（百分点，小数形式）
    due_amount: float = 0.0          # 当前到期本金
    case_count: int = 0              # 到期笔数

    # ---- 贡献度 ----
    contribution_to_parent: float = 0.0      # 对父节点变化的贡献（pp）
    contribution_pct_to_parent: float = 0.0  # 对父节点变化的贡献率（0~1）
    contribution_to_overall: float = 0.0     # 对整体变化的贡献（pp）
    contribution_pct_to_overall: float = 0.0 # 对整体变化的贡献率（0~1）

    # ---- 影响金额 ----
    impact_amount: float = 0.0       # 影响金额 = contribution_to_overall × 整体到期本金

    # ---- 子节点 ----
    children: List['RootCauseNode'] = field(default_factory=list)


@dataclass
class RootCausePath:
    """一条完整的根因路径（从产品类型到订单风控等级）"""
    path: List[RootCauseNode] = field(default_factory=list)
    # path[0] = product_type node, path[1] = order_type node, path[2] = order_grade node

    path_label: str = ""             # "借款分期 → Grade C"
    change_pp: float = 0.0           # 叶节点自身变化（百分点，小数形式）
    contribution_pp: float = 0.0     # 叶节点对整体变化的贡献（百分点）
    contribution_pct: float = 0.0    # 叶节点对整体变化的贡献率（内部计算）
    impact_amount: float = 0.0       # 影响金额

    def __post_init__(self):
        if self.path and not self.path_label:
            labels = []
            for node in self.path:
                if node.level >= 1:  # skip overall
                    labels.append(node.bucket_label or node.bucket)
            self.path_label = " → ".join(labels)


@dataclass
class RootCauseResult:
    """完整的根因定位分析结果（面向管理层）"""
    country_code: str
    country_name: str
    business_date: str               # 业务日期
    stage: str                       # 阶段

    # ---- 整体情况 ----
    overall_current_rate: float = 0.0
    overall_baseline_rate: float = 0.0
    overall_change_abs: float = 0.0
    overall_due_amount: float = 0.0
    overall_alert_level: str = "GREEN"

    # ---- 下钻树 ----
    tree: Optional[RootCauseNode] = None

    # ---- 恶化来源（首逾上升）Top 3 ----
    worsening: List[RootCausePath] = field(default_factory=list)

    # ---- 改善来源（首逾下降）Top 3 ----
    improving: List[RootCausePath] = field(default_factory=list)

    # ---- 是否有值得关注的根因 ----
    has_root_cause: bool = False

    # ---- 汇总 ----
    summary: str = ""

    @property
    def top_causes(self) -> List[RootCausePath]:
        """合并恶化+改善（向后兼容）"""
        return self.worsening + self.improving

    def __post_init__(self):
        if not self.summary:
            parts = [f"{self.country_name} {self.stage}"]
            if self.worsening:
                items = [f"{c.path_label} {c.change_pp * 100:+.2f}pp" for c in self.worsening[:3]]
                parts.append(f"📈恶化: {', '.join(items)}")
            if self.improving:
                items = [f"{c.path_label} {c.change_pp * 100:+.2f}pp" for c in self.improving[:3]]
                parts.append(f"📉改善: {', '.join(items)}")
            self.summary = " | ".join(parts)


# ============================================================
#  ActionEngine — 行动建议（规则引擎）
# ============================================================

@dataclass
class ActionItem:
    """单个节点的行动建议（纯规则引擎生成，不使用 AI）"""
    priority: str                    # "P1" | "P2" | "P3"
    needs_human: bool                # True = YES, False = NO
    action: str                      # 建议动作文本
    target_path: str = ""            # 目标路径标签 "单期 → 非分期 → C"
    change_pp: float = 0.0           # 变化值（pp，小数形式）
    impact_amount: float = 0.0       # 影响金额
    is_worsening: bool = True        # 是否为恶化方向
    reason: str = ""                 # 决策理由（给 Phase 4 AI 用的上下文）


@dataclass
class ActionResult:
    """
    行动建议引擎的完整输出。

    基于 TrendResult + RootCauseResult 的规则匹配结果。
    Phase 4 的 AI 读取此结构生成自然语言飞书消息。
    """
    country_code: str
    country_name: str
    business_date: str               # 业务日期
    stage: str                       # 阶段

    # ---- 整体态势 ----
    overall_alert_level: str = "GREEN"     # "GREEN" | "YELLOW" | "ORANGE" | "RED"
    overall_change_pp: float = 0.0         # 整体变化（百分点，小数形式）
    overall_due_amount: float = 0.0        # 整体到期本金

    # ---- 分级行动建议 ----
    p1_actions: List[ActionItem] = field(default_factory=list)
    p2_actions: List[ActionItem] = field(default_factory=list)
    p3_actions: List[ActionItem] = field(default_factory=list)

    # ---- 整体建议 ----
    overall_action: str = ""          # 面向管理层的总体行动建议

    # ---- 摘要 ----
    summary: str = ""

    @property
    def all_actions(self) -> List[ActionItem]:
        """所有行动建议（P1 → P2 → P3）"""
        return self.p1_actions + self.p2_actions + self.p3_actions

    @property
    def has_critical(self) -> bool:
        """是否有 P1 紧急项"""
        return len(self.p1_actions) > 0

    @property
    def needs_any_human(self) -> bool:
        """是否有任何需要人工介入的项"""
        return any(a.needs_human for a in self.all_actions)

    def __post_init__(self):
        if not self.summary:
            parts = [f"{self.country_name} {self.stage}"]
            parts.append(f"P1:{len(self.p1_actions)} P2:{len(self.p2_actions)} P3:{len(self.p3_actions)}")
            if self.has_critical:
                parts.append("⚠️ 需紧急处理")
            elif self.needs_any_human:
                parts.append("📋 需人工复核")
            else:
                parts.append("✅ 继续观察")
            self.summary = " | ".join(parts)


# ============================================================
#  AlertDecision — 告警决策（是否发送飞书）
# ============================================================

@dataclass
class AlertDecision:
    """
    告警决策引擎输出。

    基于 ActionResult + TrendReport 判定是否真正需要发送飞书告警。
    避免噪声告警，仅在有意义的异常出现时才推送。

    Phase 4 的 AI/飞书发送模块读取此结构决定是否推送。
    """
    country_code: str
    country_name: str
    business_date: str               # 业务日期
    stage: str                       # 阶段

    # ---- 核心决策 ----
    should_alert: bool = False       # 是否发送飞书告警
    alert_reason: str = ""           # 告警原因（可多选，逗号分隔）
    trigger_source: str = ""         # 触发来源 "Trend" | "Target" | "RootCause" | "P1"

    # ---- 决策依据 ----
    p1_count: int = 0                # P1 项数量
    max_impact: float = 0.0          # 最大影响金额
    target_deviation_pp: float = 0.0 # 高于目标的偏离（pp，小数形式）
    consecutive_worsening_days: int = 0  # 连续恶化天数
    overall_change_pp: float = 0.0   # 整体变化（pp，小数形式）
    overall_alert_level: str = "GREEN"

    # ---- 置信度 ----
    confidence_score: float = 0.0       # 0~100，预警置信度
    confidence_breakdown: str = ""      # 置信度分解 "Trend:30 + Target:25 + Scale:25 = 80"

    # ---- 业务规模 ----
    due_amount: float = 0.0            # 整体到期本金
    case_count: int = 0                # 整体到期笔数
    business_scale_skip: bool = False  # 是否因规模过小被跳过

    # ---- 详情 ----
    details: str = ""                # 面向管理层的决策说明

    @property
    def alert_reasons_list(self) -> list:
        """解析告警原因为列表"""
        if not self.alert_reason:
            return []
        return [r.strip() for r in self.alert_reason.split(",") if r.strip()]

    def __post_init__(self):
        if not self.details:
            if self.should_alert:
                self.details = (
                    f"📢 发送飞书告警 — {self.alert_reason} "
                    f"(source: {self.trigger_source})"
                )
            else:
                self.details = (
                    f"🔇 跳过飞书告警 — 异常未达推送阈值，仅记录日志"
                )


# ============================================================
#  DimensionAnomaly — 单个维度的异常评估（多维度独立监控）
# ============================================================

@dataclass
class DimensionScoreBreakdown:
    """风险分数分解"""
    conditions_score: float = 0.0    # 0-50: 条件通过得分
    magnitude_score: float = 0.0     # 0-30: 变化幅度得分
    severity_score: float = 0.0      # 0-20: 严重等级得分
    total: float = 0.0               # 0-100

    def summary(self) -> str:
        return (f"C:{self.conditions_score:.0f} + "
                f"M:{self.magnitude_score:.0f} + "
                f"S:{self.severity_score:.0f} = {self.total:.0f}")


@dataclass
class AppBreakdown:
    """单个 APP 的首逾率细分 — 用于 Top3 卡片下钻展示"""
    app_name: str = ""
    current_rate: float = 0.0        # 今日首逾率
    dod_change_pp: float = 0.0       # 较昨日变化（pp）
    due_amount: float = 0.0          # 今日到期本金
    case_count: int = 0              # 今日到期单量
    yesterday_case_count: int = 0    # 昨日到期单量（用于量价分析）
    yesterday_rate: float = 0.0      # 昨日首逾率
    top_grades: list = field(default_factory=list)  # [(grade, case_count), ...] Top 2 重点 Grade
    volume_quality_label: str = ""   # 异常原因标签（放量/质量走弱/正常波动等）


@dataclass
class DimensionAnomaly:
    """单个维度切片的异常评估结果 — 多维度独立监控核心输出"""
    # ---- 身份 ----
    dimension: str                   # "overall" | "product_type" | "order_type" | "order_grade"
    bucket: str                      # "total" | "单期" | "借款分期" | "C" | ...
    dim_label: str = ""              # 维度中文名
    bucket_label: str = ""           # 桶中文名

    # ---- 当前值 ----
    current_rate: float = 0.0        # 当前首逾率（小数）
    due_amount: float = 0.0          # 到期本金
    case_count: int = 0              # 到期笔数

    # ---- 条件通过状态 ----
    dod_pass: bool = False
    avg3d_pass: bool = False
    avg7d_pass: bool = False
    conditions_met: int = 0
    min_conditions: int = 2          # 需要满足几个条件才算异常

    # ---- 变化值（百分点，小数形式） ----
    dod_change_pp: float = 0.0
    avg3d_change_pp: float = 0.0
    avg7d_change_pp: float = 0.0
    target_deviation_pp: float = 0.0
    target_value: float = 0.0        # 目标首逾率

    # ---- 风险评分 ----
    risk_score: float = 0.0          # 0-100
    risk_breakdown: str = ""         # 分解文本
    score_detail: Optional[DimensionScoreBreakdown] = None

    # ---- 综合判断 ----
    worst_alert_level: str = "GREEN"
    worst_alert_icon: str = "🟢"

    # ---- 过滤 ----
    is_sample_small: bool = False    # 样本太小，不参与 Top 3
    is_anomalous: bool = False       # 是否满足异常条件
    skip_reason: str = ""            # 跳过原因（如 "sample too small"）

    # ---- 建议动作 ----
    suggested_action: str = ""       # 一行建议（来自 SIMPLE_ACTION_MAP）

    # ---- 异常持续时间（辅助信息） ----
    persistence_label: str = ""      # "🔥 连续恶化：4天" / "🆕 今日首次异常" 等

    # ---- 业务模型层级 ----
    hierarchy_path: str = ""         # "分期产品 · 借款分期 · Grade D"（完整展示路径）
    parent_key: str = ""             # "product_type:分期"（用于 Top3 父子去重）
    is_merged_away: bool = False     # True=被合并到另一个维度的重复项，跳过展示
    level: int = 0                   # 0=overall, 1=产品分类, 2=分期子类型, 3=风控等级

    # ---- 产品归属（用于 Grade 维度显示父级产品上下文） ----
    primary_product: str = ""        # "分期产品" | "非分期产品" | ""（非 Grade 维度留空即可）

    # ---- APP 下钻（纯展示，不参与 Risk Score / Alert Decision） ----
    top_apps: List[AppBreakdown] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        """展示用名称: "包体 · 借款分期" """
        return f"{self.dim_label} · {self.bucket_label}"

    @property
    def condition_summary(self) -> str:
        """条件通过摘要: "3/3" """
        return f"{self.conditions_met}/{self.min_conditions}"


@dataclass
class MultiDimAlertDecision:
    """多维度独立监控的完整告警决策"""
    country_code: str
    country_name: str
    business_date: str
    stage: str

    # ---- 核心决策 ----
    should_alert: bool = False
    alert_reason: str = ""           # 简要原因

    # ---- 所有维度评分 ----
    all_anomalies: List[DimensionAnomaly] = field(default_factory=list)

    # ---- Top 3 选择 ----
    overall_anomaly: Optional[DimensionAnomaly] = None
    top3_segments: List[DimensionAnomaly] = field(default_factory=list)

    # ---- 整体数据快照 ----
    overall_rate: float = 0.0
    overall_due_amount: float = 0.0
    overall_case_count: int = 0
    overall_dod_change_pp: float = 0.0
    overall_avg3d_change_pp: float = 0.0
    overall_avg7d_change_pp: float = 0.0
    overall_target_pp: float = 0.0
    overall_target_value: float = 0.0
    overall_alert_level: str = "GREEN"
    overall_alert_icon: str = ""

    # ---- 今日结论 ----
    conclusion_text: str = ""

    # ---- 异常子维度总数（含被 Top 3 截断的） ----
    total_segment_anomalies: int = 0
    truncated_count: int = 0         # 被截断的数量

    # ---- 是否使用了 Root Cause / Action（enriched 模式） ----
    has_root_cause: bool = False
    has_actions: bool = False


@dataclass
class SimpleAction:
    """轻量动作建议 — 用于子维度异常（不修改 action_engine）"""
    target_label: str                # 维度名称
    action: str                      # 建议动作
    reason: str = ""                 # 简要理由
    priority_label: str = "关注"     # "建议" | "关注" | "观察"
