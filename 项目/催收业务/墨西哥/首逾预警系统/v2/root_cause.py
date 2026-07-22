"""
V2 根因定位引擎 — 异常来源自动下钻

当 TrendResult 判定为 🟠 或 🔴 时，自动下钻定位异常来源。

下钻顺序:
  整体 → 产品类型（单期/分期） → 包体 → 订单风控等级（A~F）

计算每一层:
  - 变化(pp)
  - 贡献率
  - 影响金额

输出 Top 3 RootCausePath → RootCauseResult

不修改 V1 代码。不修改 TrendEngine。
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from .. import config as v1_config
from ..quickbi import (
    fetch_overdue_data,
    calculate_first_overdue_rate,
    _to_int,
    warm_cache_recovery,
)
from . import config as v2_config
from .models import TrendReport, TrendResult, RootCauseNode, RootCausePath, RootCauseResult


# ============================================================
#  层级下钻配置
# ============================================================

# 下钻层级定义: (dim_key, dim_label, source_field, filter_fn, buckets_getter)
# filter_fn: "map" = ORDER_TYPE_MAP 映射后匹配, "direct" = 直接字段匹配
_DRILL_DOWN_LEVELS = [
    {
        "key": "product_type",
        "label": "产品分类",
        "field": "order_type",
        "filter_fn": "map",
        "buckets": ["单期", "分期"],
    },
    {
        "key": "order_type",
        "label": "分期子类型",
        "field": "order_type",
        "filter_fn": "direct",
        "buckets": ["非分期", "借款分期", "展期分期", "展期N期"],
    },
    {
        "key": "order_grade",
        "label": "订单风控等级",
        "field": "order_grade",
        "filter_fn": "direct",
        "buckets": ["A", "B", "C", "D", "E", "F"],
    },
]

# 分桶显示名称映射 — 将内部 bucket 名转换为新业务名称
# 不修改算法，仅影响展示
_BUCKET_DISPLAY_NAME = {
    "单期": "非分期产品",
    "非分期": "非分期产品",
}


# ============================================================
#  数据聚合
# ============================================================

def _agg_rows(rows: List[dict], stage: str) -> dict:
    """
    聚合 raw_rows 为一个节点的指标。

    使用统一首逾计算函数 calculate_first_overdue_rate（BI 口径）。

    Returns:
        {"due": float, "pay": float, "overdue_rate": float, "cases": int}
    """
    due = 0.0
    pay = 0.0
    cases = 0

    for r in rows:
        rcalc = calculate_first_overdue_rate(r, stage)
        due += rcalc["due_amt"]
        pay += rcalc["cum_pay"]
        cases += _to_int(r.get("due_case", 0))

    overdue_rate = 1.0 - (pay / due) if due > 0 else 0.0
    return {
        "due": round(due, 2),
        "pay": round(pay, 2),
        "overdue_rate": round(overdue_rate, 4),
        "cases": cases,
    }


def _filter_rows(rows: List[dict], level_cfg: dict, bucket: str) -> List[dict]:
    """
    按维度过滤 raw_rows。

    Args:
        rows: 当前范围内的 raw_rows
        level_cfg: _DRILL_DOWN_LEVELS 中的一项
        bucket: 目标桶值

    Returns:
        过滤后的行列表
    """
    field = level_cfg["field"]
    filter_fn = level_cfg["filter_fn"]

    if filter_fn == "map":
        # 用 ORDER_TYPE_MAP 映射后匹配
        return [r for r in rows
                if v1_config.ORDER_TYPE_MAP.get(r.get(field, ""), "") == bucket]
    else:
        # 直接字段值匹配
        return [r for r in rows if r.get(field, "") == bucket]


# ============================================================
#  层级树构建
# ============================================================

def _build_tree(rows: List[dict], stage: str,
                level: int = 0,
                parent_bucket: str = "整体") -> RootCauseNode:
    """
    递归构建层级下钻树。

    Args:
        rows: 当前层级的 raw_rows
        stage: 分析阶段
        level: 当前层级 (0=整体, 1=产品类型, 2=包体, 3=订单风控等级)
        parent_bucket: 父节点 bucket 名

    Returns:
        RootCauseNode（含 children）
    """
    agg = _agg_rows(rows, stage)

    if level == 0:
        dim_key = "overall"
        dim_label = "整体"
        bucket_label = "整体"
    else:
        cfg = _DRILL_DOWN_LEVELS[level - 1]
        dim_key = cfg["key"]
        dim_label = cfg["label"]
        # 应用业务名称映射（单期/非分期 → 非分期产品）
        bucket_label = _BUCKET_DISPLAY_NAME.get(parent_bucket, parent_bucket)

    node = RootCauseNode(
        dim_key=dim_key,
        bucket=parent_bucket,
        dim_label=dim_label,
        bucket_label=bucket_label,
        level=level,
        current_rate=agg["overdue_rate"],
        due_amount=agg["due"],
        case_count=agg["cases"],
        children=[],
    )

    # 到达叶子层级或没有更多维度 → 停止
    if level >= len(_DRILL_DOWN_LEVELS) or not rows:
        return node

    # 当前层级的维度配置
    cfg = _DRILL_DOWN_LEVELS[level]

    # 对每个 bucket 过滤并递归
    for bucket in cfg["buckets"]:
        child_rows = _filter_rows(rows, cfg, bucket)
        if not child_rows:
            continue

        child_agg = _agg_rows(child_rows, stage)
        if child_agg["due"] <= 0:
            continue  # 无到期本金，跳过

        child = _build_tree(child_rows, stage, level + 1, bucket)
        node.children.append(child)

    return node


# ============================================================
#  变化 & 贡献计算
# ============================================================

def _compute_changes(today_node: RootCauseNode,
                     yesterday_node: Optional[RootCauseNode],
                     overall_due: float,
                     overall_change: float):
    """
    递归计算每个节点的变化和贡献度。

    遍历 today_node 的树，从 yesterday_node 中查找对应节点的基线值。

    Args:
        today_node: 今天的节点
        yesterday_node: 昨天的对应节点（可能为 None）
        overall_due: 整体到期本金（今天）
        overall_change: 整体首逾率变化（今天 - 昨天）
    """
    # 找昨天的对应节点
    yesterday_child_map = {}
    if yesterday_node and yesterday_node.children:
        yesterday_child_map = {c.bucket: c for c in yesterday_node.children}

    for child in today_node.children:
        yest_child = yesterday_child_map.get(child.bucket)

        if yest_child and yest_child.current_rate > 0:
            child.baseline_rate = yest_child.current_rate
        else:
            child.baseline_rate = 0.0

        child.change_abs = round(child.current_rate - child.baseline_rate, 4)

        # 贡献到父节点
        parent_change = today_node.change_abs
        if abs(parent_change) > 0.0001 and today_node.due_amount > 0:
            child.contribution_to_parent = round(
                (child.due_amount / today_node.due_amount) * child.change_abs, 4
            )
            child.contribution_pct_to_parent = round(
                child.contribution_to_parent / parent_change, 4
            ) if abs(parent_change) > 0.0001 else 0.0
        else:
            child.contribution_to_parent = 0.0
            child.contribution_pct_to_parent = 0.0

        # 贡献到整体
        if abs(overall_change) > 0.0001 and overall_due > 0:
            child.contribution_to_overall = round(
                (child.due_amount / overall_due) * child.change_abs, 4
            )
            child.contribution_pct_to_overall = round(
                child.contribution_to_overall / overall_change, 4
            ) if abs(overall_change) > 0.0001 else 0.0
        else:
            child.contribution_to_overall = 0.0
            child.contribution_pct_to_overall = 0.0

        # 影响金额
        child.impact_amount = round(
            abs(child.contribution_to_overall) * overall_due, 2
        )

        # 递归
        _compute_changes(child, yest_child, overall_due, overall_change)


# ============================================================
#  根因路径收集
# ============================================================

def _collect_root_cause_paths(node: RootCauseNode,
                               overall_change: float) -> List[RootCausePath]:
    """
    从层级树中收集所有根因路径（从产品类型到订单风控等级）。

    只收集叶节点（level=3, order_grade）的路径，
    按 |贡献到整体的变化(pp)| 降序排列。

    Args:
        node: 树的根节点
        overall_change: 整体变化

    Returns:
        [RootCausePath, ...] 按 |change_pp| 降序
    """
    paths = []

    def _dfs(current: RootCauseNode, ancestors: List[RootCauseNode]):
        if current.level == 3:
            full_path = list(ancestors)  # [product_type, order_type, order_grade]
            if len(full_path) >= 2:
                leaf = full_path[-1]
                paths.append(RootCausePath(
                    path=full_path,
                    change_pp=leaf.contribution_to_overall,
                    contribution_pp=leaf.contribution_to_overall,
                    contribution_pct=abs(leaf.contribution_pct_to_overall),
                    impact_amount=leaf.impact_amount,
                ))
            return

        if not current.children:
            return

        for child in current.children:
            _dfs(child, ancestors + [child])

    # 从 level 1 开始（跳过 overall）
    for product_node in node.children:
        _dfs(product_node, [product_node])

    # 按 |变化值(pp)| 降序排列
    paths.sort(key=lambda p: abs(p.change_pp), reverse=True)
    return paths


# ============================================================
#  主入口
# ============================================================

def analyze_root_cause(
    trend_report: TrendReport,
    country_code: str,
    business_date: str,
    stage: str = "D0",
) -> Optional[RootCauseResult]:
    """
    根因定位分析 — V2 Phase 2 核心入口。

    触发条件: TrendReport 中存在 🟠 或 🔴 的维度。

    流程:
      1. 判断是否需要下钻（有 🟠/🔴）
      2. 拉取今天 + 昨天的 raw_rows
      3. 构建两天的层级下钻树
      4. 计算每层变化 & 贡献度
      5. 收集所有根因路径
      6. 输出 Top 3 → RootCauseResult

    Args:
        trend_report: Phase 1 趋势分析报告
        country_code: "MX" | "AR"
        business_date: 业务日期 "2026-07-06"
        stage: 分析阶段

    Returns:
        RootCauseResult 或 None（无需下钻 / 无异常 / 数据不足）
    """
    country = v1_config.COUNTRIES[country_code]
    anomalies = trend_report.get_anomalies()

    # 检查是否有 🟠 或 🔴
    triggered = [a for a in anomalies
                 if a.overall_judgment in ("ORANGE", "RED")]
    if not triggered:
        print(f"\n  [RootCause] 无 🟠/🔴 异常，跳过根因定位")
        return None

    print(f"\n{'='*55}")
    print(f"  V2 根因定位引擎")
    print(f"  触发维度: {len(triggered)} 个 🟠/🔴")
    for t in triggered:
        print(f"    {t.overall_icon} {t.dim_label}·{t.bucket_label}: "
              f"{t.current_value:.2%} ({t.overall_label})")
    print(f"{'='*55}")

    # ---- Step 1: 拉取今天 + 昨天的 raw_rows ----
    print(f"\n  [1/3] 拉取今天 & 昨天原始数据 ...")

    dt = datetime.strptime(business_date, "%Y-%m-%d")
    yesterday = (dt - timedelta(days=1)).strftime("%Y-%m-%d")

    # 预热缓存
    try:
        warm_cache_recovery(_get_due_week(business_date))
        warm_cache_recovery(_get_due_week(yesterday))
    except Exception:
        pass

    today_data = fetch_overdue_data(
        _get_due_week(business_date), country_code, business_date=business_date
    )
    yesterday_data = fetch_overdue_data(
        _get_due_week(yesterday), country_code, business_date=yesterday
    )

    if today_data is None or not today_data.get("raw_rows"):
        print(f"  ❌ 今天无数据，无法下钻")
        return None

    today_rows = today_data["raw_rows"]
    yesterday_rows = yesterday_data.get("raw_rows", []) if yesterday_data else []

    print(f"  今天: {len(today_rows)} rows  |  昨天: {len(yesterday_rows)} rows")

    # ---- Step 2: 构建两天的层级树 ----
    print(f"\n  [2/3] 构建层级下钻树 ...")

    today_tree = _build_tree(today_rows, stage)
    yesterday_tree = _build_tree(yesterday_rows, stage) if yesterday_rows else None

    # 计算整体变化
    overall_current = today_tree.current_rate
    overall_baseline = yesterday_tree.current_rate if yesterday_tree else 0.0
    overall_change = round(overall_current - overall_baseline, 4)
    overall_due = today_tree.due_amount

    today_tree.baseline_rate = overall_baseline
    today_tree.change_abs = overall_change

    print(f"  整体: {overall_current:.2%} (今天) vs {overall_baseline:.2%} (昨天) "
          f"→ 变化 {overall_change:+.4f}")

    # ---- Step 3: 计算变化 & 贡献度 ----
    _compute_changes(today_tree, yesterday_tree, overall_due, overall_change)

    # ---- Step 4: 收集根因路径 ----
    print(f"\n  [3/3] 收集根因路径 ...")

    all_paths = _collect_root_cause_paths(today_tree, overall_change)

    # 过滤：有实际变化 + 至少 2 层
    valid_paths = [p for p in all_paths
                   if len(p.path) >= 2 and abs(p.change_pp) > 0.0001]

    # 分流：恶化（上升）vs 改善（下降）
    worsening = [p for p in valid_paths if p.change_pp > 0]
    improving = [p for p in valid_paths if p.change_pp < 0]

    # 各自按 |change_pp| 降序取 Top 3
    top_worsening = worsening[:3]
    top_improving = improving[:3]

    if not top_worsening and not top_improving:
        print(f"  ⚠️ 无有效根因路径")
        return None

    print(f"  收集到 {len(valid_paths)} 条路径 "
          f"(恶化 {len(worsening)}, 改善 {len(improving)})")

    # 确定整体告警等级
    overall_tr = trend_report.get("overall", "total")
    overall_alert = overall_tr.overall_judgment if overall_tr else "GREEN"

    result = RootCauseResult(
        country_code=country_code,
        country_name=country["name"],
        business_date=business_date,
        stage=stage,
        overall_current_rate=overall_current,
        overall_baseline_rate=overall_baseline,
        overall_change_abs=overall_change,
        overall_due_amount=overall_due,
        overall_alert_level=overall_alert,
        tree=today_tree,
        worsening=top_worsening,
        improving=top_improving,
        has_root_cause=True,
    )

    # ---- 打印（管理层格式：pp + 影响金额，不展示贡献率） ----
    print(f"\n  📊 根因定位结果")
    print(f"  整体 {stage} 首逾: {overall_current:.2%} "
          f"(昨天 {overall_baseline:.2%}, 变化 {overall_change:+.4f})")
    print(f"  到期本金: {overall_due:,.0f}")

    if top_worsening:
        print(f"\n  📈 恶化来源（首逾上升）")
        for i, cause in enumerate(top_worsening):
            print(f"  {i+1}. {cause.path_label}")
            print(f"     {cause.change_pp * 100:+.2f}pp  |  影响金额: {cause.impact_amount:,.0f}")
            for node in cause.path:
                n_arrow = "↑" if node.change_abs >= 0 else "↓"
                n_sign = "+" if node.change_abs >= 0 else ""
                print(f"       {node.dim_label}·{node.bucket_label}: "
                      f"{node.current_rate:.2%} {n_arrow}{n_sign}{abs(node.change_abs):.4f} "
                      f"(本金 {node.due_amount:,.0f})")

    if top_improving:
        print(f"\n  📉 改善来源（首逾下降）")
        for i, cause in enumerate(top_improving):
            print(f"  {i+1}. {cause.path_label}")
            print(f"     {cause.change_pp * 100:+.2f}pp  |  影响金额: {cause.impact_amount:,.0f}")
            for node in cause.path:
                n_arrow = "↑" if node.change_abs >= 0 else "↓"
                n_sign = "+" if node.change_abs >= 0 else ""
                print(f"       {node.dim_label}·{node.bucket_label}: "
                      f"{node.current_rate:.2%} {n_arrow}{n_sign}{abs(node.change_abs):.4f} "
                      f"(本金 {node.due_amount:,.0f})")

    print(f"\n  {result.summary}")

    return result


# ============================================================
#  格式化输出
# ============================================================

def print_root_cause_report(result: RootCauseResult):
    """完整打印 RootCauseResult（管理层格式）。"""
    if not result.has_root_cause:
        print(f"\n  🟢 无需根因定位")
        return

    print(f"\n{'='*70}")
    print(f"  V2 RootCause Report — {result.country_name} ({result.country_code})")
    print(f"  业务日期: {result.business_date}  |  阶段: {result.stage}")
    print(f"{'='*70}")

    change_arrow = "↑" if result.overall_change_abs >= 0 else "↓"
    print(f"\n  整体 {result.stage} 首逾率: "
          f"{result.overall_current_rate:.2%} "
          f"({change_arrow} {result.overall_change_abs:+.4f} vs 昨天 {result.overall_baseline_rate:.2%})")
    print(f"  整体到期本金: {result.overall_due_amount:,.0f}")

    # ---- 恶化来源 ----
    if result.worsening:
        print(f"\n  📈 恶化来源（首逾上升）Top {len(result.worsening)}")
        print(f"  {'─'*60}")
        for i, cause in enumerate(result.worsening):
            print(f"  {i+1}. {cause.path_label}")
            print(f"     {cause.change_pp * 100:+.2f}pp  |  影响金额: {cause.impact_amount:,.0f}")
            for node in cause.path:
                n_arrow = "↑" if node.change_abs >= 0 else "↓"
                n_sign = "+" if node.change_abs >= 0 else ""
                print(f"       {node.dim_label}·{node.bucket_label}: "
                      f"{node.current_rate:.2%} {n_arrow}{n_sign}{abs(node.change_abs):.4f} "
                      f"(本金 {node.due_amount:,.0f})")

    # ---- 改善来源 ----
    if result.improving:
        print(f"\n  📉 改善来源（首逾下降）Top {len(result.improving)}")
        print(f"  {'─'*60}")
        for i, cause in enumerate(result.improving):
            print(f"  {i+1}. {cause.path_label}")
            print(f"     {cause.change_pp * 100:+.2f}pp  |  影响金额: {cause.impact_amount:,.0f}")
            for node in cause.path:
                n_arrow = "↑" if node.change_abs >= 0 else "↓"
                n_sign = "+" if node.change_abs >= 0 else ""
                print(f"       {node.dim_label}·{node.bucket_label}: "
                      f"{node.current_rate:.2%} {n_arrow}{n_sign}{abs(node.change_abs):.4f} "
                      f"(本金 {node.due_amount:,.0f})")

    # ---- 下钻树 ----
    print(f"\n  🌳 完整下钻树 (变化 / 影响金额):")
    print(f"  {'─'*60}")
    _print_tree(result.tree, overall_due=result.overall_due_amount)

    print(f"\n{'='*70}")
    print(f"  {result.summary}")
    print(f"{'='*70}\n")


def _print_tree(node: RootCauseNode, indent: int = 0,
                overall_due: float = 0.0):
    """递归打印下钻树（管理层格式：变化pp + 影响金额）"""
    prefix = "  " * indent

    if node.level == 0:
        arrow = "↑" if node.change_abs >= 0 else "↓"
        sign = "+" if node.change_abs >= 0 else ""
        print(f"{prefix}📦 整体  首逾 {node.current_rate:.2%}  "
              f"{arrow}{sign}{abs(node.change_abs):.4f}  "
              f"本金 {node.due_amount:,.0f}")
    elif node.level <= 3:
        arrow = "↑" if node.change_abs >= 0 else "↓"
        sign = "+" if node.change_abs >= 0 else ""
        impact = abs(node.contribution_to_overall) * overall_due if overall_due > 0 else 0

        indent_mark = "  ├─" if node.level >= 1 else ""
        print(f"{prefix}{indent_mark} {node.bucket_label}  "
              f"首逾 {node.current_rate:.2%}  "
              f"{arrow}{sign}{abs(node.change_abs):.4f}  "
              f"影响 {impact:,.0f}  "
              f"本金 {node.due_amount:,.0f}")

    for child in node.children:
        _print_tree(child, indent + 1, overall_due)


# ============================================================
#  辅助
# ============================================================

def _get_due_week(date_str: str) -> str:
    """日期 → ISO week"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"
