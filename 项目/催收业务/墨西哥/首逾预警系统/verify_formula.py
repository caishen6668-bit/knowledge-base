"""
公式验证工具 — 枚举所有候选公式，对比 BI 基准值，找出最匹配的公式

用法:
  python 每日数据预警.py --verify-formula --country MX --date 2026-07-07

不做任何业务分析，仅输出原始字段汇总 + 各公式计算结果。
"""

import json
from datetime import datetime, timedelta
from collections import defaultdict

from . import config
from .quickbi import _cached_query, _to_num


def _get_due_week(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def run_verify_formula(run_date, country_code):
    """
    拉取 Quick BI 原始数据，汇总所有相关字段，枚举候选公式。

    Args:
        run_date: "2026-07-07"
        country_code: "MX" | "AR"
    """
    # ---- 日期 ----
    run_dt = datetime.strptime(run_date, "%Y-%m-%d")
    business_date = (run_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    due_week = _get_due_week(business_date)
    due_day_str = business_date.replace("-", "")

    country = config.COUNTRIES[country_code]
    apps = set(country["apps"])

    # ---- 拉取数据 ----
    print(f"\n  ⏳ 拉取 Quick BI 数据 (due_week={due_week}) ...", end=" ", flush=True)
    conditions_str = json.dumps({"due_week": due_week}, ensure_ascii=False)
    data = _cached_query(config.QBI_API_RECOVERY, conditions_str)
    if not data.get("Success"):
        print(f"FAIL: {data.get('Message', 'Unknown')}")
        return
    all_rows = data.get("Result", {}).get("Values", [])
    print(f"{len(all_rows)} rows")

    # ---- 过滤 ----
    rows = [r for r in all_rows if r.get("app_name", "") in apps]
    print(f"  🔍 APP 过滤 ({country_code}): {len(rows)} rows")
    rows = [r for r in rows if r.get("due_day", "") == due_day_str]
    print(f"  🔍 due_day 过滤 ({due_day_str}): {len(rows)} rows")

    if not rows:
        print(f"\n  ❌ 无数据")
        return

    # ---- 汇总各字段 ----
    fields_to_sum = [
        "D_3_pay_amt",
        "D_2_pay_amt", "D_2_due_amt",
        "D_1_pay_amt", "D_1_due_amt",
        "D0_pay_amt",  "D0_due_amt",
        "D1_pay_amt",  "D1_due_amt",
        "S1_pay_amt",  "S1_due_amt",
        "S2_pay_amt",  "S2_due_amt",
        "due_amt",
        "due_case",
    ]

    sums = {}
    for field in fields_to_sum:
        sums[field] = sum(_to_num(r.get(field, 0)) for r in rows)

    # ---- 输出头 ----
    print(f"\n{'='*60}")
    print(f"  公式验证 — {country['name']} ({country_code})")
    print(f"{'='*60}")
    print(f"  业务日期:   {business_date}")
    print(f"  due_week:   {due_week}")
    print(f"  due_day:    {due_day_str}")
    print(f"  数据行数:   {len(rows)}")
    print(f"{'='*60}")

    # ---- 原始字段汇总 ----
    print(f"\n{'─'*60}")
    print(f"  原始字段汇总（Σ 全部 {len(rows)} 行）")
    print(f"{'─'*60}")

    pay_fields = ["D_3_pay_amt", "D_2_pay_amt", "D_1_pay_amt", "D0_pay_amt",
                  "D1_pay_amt", "S1_pay_amt", "S2_pay_amt"]
    due_fields = ["D_2_due_amt", "D_1_due_amt", "D0_due_amt", "D1_due_amt",
                  "S1_due_amt", "S2_due_amt"]

    print(f"\n  [回款字段]")
    for f in pay_fields:
        print(f"  {f:20s} = {sums[f]:>15,.2f}")

    print(f"\n  [到期本金字段]")
    for f in due_fields:
        print(f"  {f:20s} = {sums[f]:>15,.2f}")

    print(f"\n  [其它]")
    print(f"  {'due_amt':20s} = {sums['due_amt']:>15,.2f}")
    print(f"  {'due_case':20s} = {sums['due_case']:>15,.0f}")

    # ---- 衍生计算 ----
    D3_pay  = sums["D_3_pay_amt"]
    D2_pay  = sums["D_2_pay_amt"]
    D2_due  = sums["D_2_due_amt"]
    D1_pay  = sums["D_1_pay_amt"]
    D1_due  = sums["D_1_due_amt"]
    D0_pay  = sums["D0_pay_amt"]
    D0_due  = sums["D0_due_amt"]
    due_amt = sums["due_amt"]

    cum_pay_to_D0 = D3_pay + D2_pay + D1_pay + D0_pay   # 累计回款至 D0

    # ---- 候选公式 ----
    print(f"\n{'─'*60}")
    print(f"  候选公式计算")
    print(f"{'─'*60}")

    formulas = []

    # 公式1: 1 - D0_pay / D0_due
    f1 = (1 - D0_pay / D0_due) if D0_due > 0 else 0
    formulas.append(("公式1", "1 - D0_pay_amt / D0_due_amt", f1,
                     f"={1:.4f} = {f1*100:.2f}%",
                     f"D0_pay={D0_pay:,.2f}  D0_due={D0_due:,.2f}"))

    # 公式2: 1 - cum_pay / due_amt
    f2 = (1 - cum_pay_to_D0 / due_amt) if due_amt > 0 else 0
    formulas.append(("公式2", "1 - (D_3+D_2+D_1+D0)_pay / due_amt", f2,
                     f"={1 - cum_pay_to_D0/due_amt:.4f} = {f2*100:.2f}%",
                     f"cum_pay={cum_pay_to_D0:,.2f}  due_amt={due_amt:,.2f}"))

    # 公式3: 1 - cum_pay / D0_due
    f3 = (1 - cum_pay_to_D0 / D0_due) if D0_due > 0 else 0
    formulas.append(("公式3", "1 - (D_3+D_2+D_1+D0)_pay / D0_due_amt", f3,
                     f"={1 - cum_pay_to_D0/D0_due:.4f} = {f3*100:.2f}%",
                     f"cum_pay={cum_pay_to_D0:,.2f}  D0_due={D0_due:,.2f}"))

    # 公式4: 1 - D0_pay / due_amt
    f4 = (1 - D0_pay / due_amt) if due_amt > 0 else 0
    formulas.append(("公式4", "1 - D0_pay_amt / due_amt", f4,
                     f"={1 - D0_pay/due_amt:.4f} = {f4*100:.2f}%",
                     f"D0_pay={D0_pay:,.2f}  due_amt={due_amt:,.2f}"))

    # 公式5: 1 - cum_pay_to_D0 / D1_due  (another possibility)
    D1_due_val = sums["D1_due_amt"]
    f5 = (1 - cum_pay_to_D0 / D1_due_val) if D1_due_val > 0 else 0
    formulas.append(("公式5", "1 - (D_3+D_2+D_1+D0)_pay / D1_due_amt", f5,
                     f"={1 - cum_pay_to_D0/D1_due_val:.4f} = {f5*100:.2f}%",
                     f"cum_pay={cum_pay_to_D0:,.2f}  D1_due={D1_due_val:,.2f}"))

    # 公式6: 1 - cum_pay_to_D0 / D_2_due (the original due at D-2)
    f6 = (1 - cum_pay_to_D0 / D2_due) if D2_due > 0 else 0
    formulas.append(("公式6", "1 - (D_3+D_2+D_1+D0)_pay / D_2_due_amt", f6,
                     f"={1 - cum_pay_to_D0/D2_due:.4f} = {f6*100:.2f}%",
                     f"cum_pay={cum_pay_to_D0:,.2f}  D_2_due={D2_due:,.2f}"))

    # ---- 输出公式 ----
    for name, desc, val, detail, proof in formulas:
        print(f"\n  {name}: {desc}")
        print(f"         = {detail}")
        print(f"         → {val*100:.2f}%")

    # ---- 与 BI 对比 ----
    BI_TARGET = 0.3185  # 31.85%

    print(f"\n{'─'*60}")
    print(f"  与 BI 基准值对比（BI = {BI_TARGET*100:.2f}%）")
    print(f"{'─'*60}")

    best = None
    best_diff = 999

    for name, desc, val, detail, proof in formulas:
        diff = abs(val - BI_TARGET)
        pct_diff = diff * 100
        marker = ""
        if diff < 0.001:
            marker = " ✅ 精确匹配"
        elif diff < 0.01:
            marker = " 👈 最接近"
        print(f"  {name}: {val*100:.2f}%  |  偏差: {pct_diff:.2f}pp{marker}")

        if diff < best_diff:
            best_diff = diff
            best = (name, desc, val, detail, proof)

    print(f"\n  🎯 最佳匹配: {best[0]}")
    print(f"     {best[1]}")
    print(f"     = {best[3]}")
    print(f"     结果: {best[2]*100:.2f}%  (BI: {BI_TARGET*100:.2f}%, 偏差: {best_diff*100:.2f}pp)")

    # ---- 验证 due_amt 公式 ----
    print(f"\n{'─'*60}")
    print(f"  验证: due_amt = D_2_due_amt + D_3_pay_amt ?")
    print(f"{'─'*60}")
    computed_due_amt = D2_due + D3_pay
    print(f"  D_2_due_amt + D_3_pay_amt = {D2_due:,.2f} + {D3_pay:,.2f} = {computed_due_amt:,.2f}")
    print(f"  due_amt (API)              = {due_amt:,.2f}")
    print(f"  匹配: {'✅' if abs(computed_due_amt - due_amt) < 0.01 else '❌ 不匹配'}")

    print(f"\n{'='*60}")
    print(f"  验证完成")
    print(f"{'='*60}\n")
