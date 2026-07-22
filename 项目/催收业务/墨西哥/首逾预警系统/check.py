"""
人工 UAT 对数工具 — 输出原始计算结果，不做任何业务分析

用法:
  python 每日数据预警.py --check --country MX --stage D0
  python 每日数据预警.py --check --country AR --stage D1

输出:
  1. 业务日期 / 国家 / 阶段
  2. 整体: 到期本金 / 到期笔数 / 回款金额 / 金额首逾率
  3. 单期: 同上
  4. 分期: 同上
  5. 每个包体 (order_type): 同上
  6. 每个 order_grade (A~F): 同上

所有数值保留两位小数。
"""

import json
from datetime import datetime, timedelta
from collections import defaultdict

from . import config
from .quickbi import _cached_query, _to_int


# ============================================================
#  日期工具
# ============================================================

def _get_due_week(date_str):
    """日期 → ISO week"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


# ============================================================
#  数据拉取
# ============================================================

def _fetch_recovery_raw(due_week):
    """拉取回收率 API 原始数据（全量，不过滤国家）"""
    conditions_str = json.dumps({"due_week": due_week}, ensure_ascii=False)
    data = _cached_query(config.QBI_API_RECOVERY, conditions_str)
    if not data.get("Success"):
        raise RuntimeError(f"回收率 API 调用失败: {data.get('Message', 'Unknown')}")
    return data.get("Result", {}).get("Values", [])


# ============================================================
#  聚合计算
# ============================================================

def _compute_group(rows, stage):
    """
    对一组 raw_rows 计算指定阶段的 4 项指标。
    使用统一首逾计算函数 calculate_first_overdue_rate（BI 口径）。

    Args:
        rows: raw_rows 子集
        stage: "D0" | "D1" | ...

    Returns:
        {"due_amt": float, "due_case": int, "pay_amt": float, "overdue_rate": float}
    """
    from .quickbi import calculate_first_overdue_rate

    due_amt = 0.0
    due_case = 0
    cum_pay = 0.0

    for r in rows:
        rcalc = calculate_first_overdue_rate(r, stage)
        due_amt += rcalc["due_amt"]
        cum_pay += rcalc["cum_pay"]
        due_case += _to_int(r.get("due_case", 0))

    overdue_rate = (1.0 - cum_pay / due_amt) if due_amt > 0 else 0.0

    return {
        "due_amt": round(due_amt, 2),
        "due_case": due_case,
        "pay_amt": round(cum_pay, 2),
        "overdue_rate": round(overdue_rate, 4),
    }


# ============================================================
#  格式化输出
# ============================================================

def _fmt_amount(val):
    """格式化金额: 两位小数 + 千分位"""
    return f"{val:,.2f}"


def _fmt_rate(val):
    """格式化比率: 百分比两位小数"""
    return f"{val * 100:.2f}%"


def _print_group(title, result, indent=0):
    """打印一组计算结果"""
    prefix = "  " * indent
    print(f"{prefix}{title}")
    print(f"{prefix}  到期本金:   {_fmt_amount(result['due_amt'])}")
    print(f"{prefix}  到期笔数:   {result['due_case']}")
    print(f"{prefix}  回款金额:   {_fmt_amount(result['pay_amt'])}")
    print(f"{prefix}  金额首逾率: {_fmt_rate(result['overdue_rate'])}")


# ============================================================
#  主入口
# ============================================================

def run_check(run_date, country_code, stage):
    """
    执行对数检查 — 从 Quick BI 拉取原始数据，按维度拆分计算并输出。

    Args:
        run_date: 运行日期 "2026-07-07"
        country_code: "MX" | "AR"
        stage: "D0" | "D1" | ...
    """
    # ---- 日期计算 ----
    run_dt = datetime.strptime(run_date, "%Y-%m-%d")
    business_date = (run_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    due_week = _get_due_week(business_date)

    country = config.COUNTRIES[country_code]
    apps = set(country["apps"])

    # ---- 拉取数据 ----
    print(f"\n  ⏳ 拉取 Quick BI 数据 (due_week={due_week}) ...", end=" ", flush=True)
    all_rows = _fetch_recovery_raw(due_week)
    print(f"{len(all_rows)} rows")

    # 按 APP 过滤
    rows = [r for r in all_rows if r.get("app_name", "") in apps]
    print(f"  🔍 APP 过滤 ({country_code}): {len(rows)} rows (apps={apps})")

    # 按 due_day 过滤（单日口径 — 与 BI 页面一致）
    due_day_str = business_date.replace("-", "")
    before = len(rows)
    rows = [r for r in rows if r.get("due_day", "") == due_day_str]
    print(f"  🔍 due_day 过滤 ({due_day_str}): {len(rows)} rows (was {before})")

    if not rows:
        print(f"\n  ❌ 无数据: 国家={country_code}, due_week={due_week}")
        return

    # ================================================================
    #  输出
    # ================================================================

    print(f"\n{'='*55}")
    print(f"  人工对数 — 原始计算结果")
    print(f"{'='*55}")
    print(f"  业务日期: {business_date}")
    print(f"  国家:     {country['name']} ({country_code})")
    print(f"  阶段:     {stage}")
    print(f"  due_week: {due_week}")
    print(f"{'='*55}")

    # ---- 1. 整体 ----
    overall = _compute_group(rows, stage)
    print(f"\n{'─'*55}")
    _print_group("1. 整体", overall)

    # ---- 2. 单期 / 3. 分期 ----
    single_rows = [r for r in rows
                   if config.ORDER_TYPE_MAP.get(r.get("order_type", ""), "") == "单期"]
    installment_rows = [r for r in rows
                        if config.ORDER_TYPE_MAP.get(r.get("order_type", ""), "") == "分期"]

    single_result = _compute_group(single_rows, stage)
    installment_result = _compute_group(installment_rows, stage)

    print(f"\n{'─'*55}")
    _print_group("2. 单期", single_result)
    print()
    _print_group("3. 分期", installment_result)

    # ---- 4. 每个包体 (order_type) ----
    order_types = ["非分期", "借款分期", "展期分期", "展期N期"]
    print(f"\n{'─'*55}")
    print(f"4. 包体 (order_type)")

    for i, ot in enumerate(order_types):
        subset = [r for r in rows if r.get("order_type", "") == ot]
        if not subset:
            print(f"\n  [{ot}] — 无数据")
            continue
        result = _compute_group(subset, stage)
        _print_group(f"4-{i+1}. {ot}", result)

    # ---- 5. 每个 order_grade (A~F) ----
    grades = ["A", "B", "C", "D", "E", "F"]
    print(f"\n{'─'*55}")
    print(f"5. 订单风控等级 (order_grade)")

    for i, grade in enumerate(grades):
        subset = [r for r in rows if r.get("order_grade", "") == grade]
        if not subset:
            print(f"\n  [{grade}] — 无数据")
            continue
        result = _compute_group(subset, stage)
        _print_group(f"5-{i+1}. {grade}", result)

    print(f"\n{'='*55}")
    print(f"  对数完成")
    print(f"{'='*55}\n")
