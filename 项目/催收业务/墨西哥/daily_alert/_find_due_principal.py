"""
临时脚本：遍历 524c3ccd429c 所有数值字段，找出与 BI「到期本金」匹配的字段。

到期本金 = Σ(D_2_due_amt + D_1_due_amt + D0_due_amt + D1_due_amt + S1_due_amt + S2_due_amt)
"""
import io as _io
import sys as _sys
if _sys.platform == "win32" and hasattr(_sys.stdout, 'buffer'):
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")

import json
from datetime import datetime, timedelta

_sys.path.insert(0, "D:/knowledge-base/scripts")
from daily_alert.config import QBI_API_RECOVERY
from daily_alert.quickbi import _sign_and_call, _to_num

# 日期计算
run_date = "2026-07-07"
run_dt = datetime.strptime(run_date, "%Y-%m-%d")
business_date = (run_dt - timedelta(days=1)).strftime("%Y-%m-%d")
biz_dt = datetime.strptime(business_date, "%Y-%m-%d")
iso = biz_dt.isocalendar()
due_week = f"{iso[0]}-{iso[1]:02d}"

print(f"业务日期: {business_date}  |  Due Week: {due_week}")
print()

# 拉取数据
extra = {"ApiId": QBI_API_RECOVERY, "Conditions": json.dumps({"due_week": due_week}, ensure_ascii=False)}
data = _sign_and_call("QueryDataService", extra)
if not data.get("Success"):
    print(f"API FAIL: {data.get('Message')}")
    _sys.exit(1)

rows = data.get("Result", {}).get("Values", [])
print(f"总行数: {len(rows)}")

# 阶段 key
STAGE_KEYS = ["D_2", "D_1", "D0", "D1", "S1", "S2"]

# 计算每行的到期本金（Σ stage_due_amt）
stage_sums = []
for row in rows:
    s = 0.0
    for sk in STAGE_KEYS:
        s += _to_num(row.get(f"{sk}_due_amt"))
    stage_sums.append(s)

non_zero = [s for s in stage_sums if s > 0]
print(f"BI到期本金(Σstages) 非零行: {len(non_zero)}/{len(stage_sums)}")
if non_zero:
    print(f"  范围: {min(non_zero):,.2f} ~ {max(non_zero):,.2f}")
print()

# 收集字段 & 识别数值型
all_fields = sorted(set().union(*(row.keys() for row in rows)))
EXCLUDE = {"app_name", "cust_type", "order_type", "order_grade", "due_week", "due_day"}

numeric_fields = []
for f in all_fields:
    if f in EXCLUDE:
        continue
    # 采样判断
    sample = []
    for row in rows[:30]:
        v = row.get(f)
        if v is not None and v != "" and v != "-":
            sample.append(v)
    if not sample:
        continue
    try:
        float(str(sample[0]).replace(",", ""))
        numeric_fields.append(f)
    except (ValueError, TypeError):
        pass

print(f"数值型字段 ({len(numeric_fields)}个):")
for f in numeric_fields:
    print(f"  {f}")
print()

# 逐一比对
print(f"{'字段名':<22} {'有值行':>7} {'匹配':>7} {'匹配率':>8} {'差值均值':>12}  {'结论'}")
print("-" * 78)

STAGE_PREFIXES = tuple(f"{sk}_" for sk in STAGE_KEYS)

best_field = None
best_match = 0
best_total = 0

for fname in numeric_fields:
    # 跳过阶段明细字段（不可能是汇总）
    if fname.startswith(STAGE_PREFIXES):
        continue

    match = 0
    total = 0
    diffs = []

    for i, row in enumerate(rows):
        val = _to_num(row.get(fname))
        ref = stage_sums[i]
        if ref <= 0:
            continue  # 只比较非零行
        total += 1
        if val > 0:
            diff = abs(val - ref)
            if diff < 0.02:
                match += 1
            else:
                diffs.append(diff)

    if total == 0:
        rate_str = "N/A"
        avg_diff_str = "N/A"
        conclusion = "无数据"
    else:
        rate = match / total * 100
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        rate_str = f"{rate:.1f}%"
        avg_diff_str = f"{avg_diff:,.2f}"

        if rate >= 99.9:
            conclusion = "MATCH!"
        elif rate >= 95:
            conclusion = "NEAR"
        elif rate >= 50:
            conclusion = "PARTIAL"
        elif total > 0 and match > 0:
            conclusion = "WEAK"
        else:
            conclusion = "NO"

        if rate > best_match:
            best_match = rate
            best_field = fname
            best_total = total

    print(f"{fname:<22} {total:>7} {match:>7} {rate_str:>8} {avg_diff_str:>12}  {conclusion}")

print()
print("=" * 78)
if best_field and best_match >= 99.9:
    print(f"RESULT: 到期本金 = [{best_field}]  匹配率 {best_match:.1f}% ({best_total}行)")
elif best_field:
    print(f"BEST: [{best_field}] 匹配率 {best_match:.1f}% — 不完全匹配，需人工确认")
    # 打印几个不匹配示例
    print()
    print("不匹配示例 (前5条):")
    shown = 0
    for i, row in enumerate(rows):
        if shown >= 5:
            break
        ref = stage_sums[i]
        if ref <= 0:
            continue
        val = _to_num(row.get(best_field))
        if val > 0 and abs(val - ref) >= 0.02:
            print(f"  [{best_field}]={val:,.2f}  vs  sum={ref:,.2f}  diff={abs(val-ref):,.2f}")
            shown += 1
else:
    print("RESULT: 未找到匹配字段 — 到期本金可能不在 raw rows 中，或需不同计算方式")

# 额外：列出所有值非零且不在 stage 字段中的数值字段
print()
print("--- 补充：各字段非零值统计 ---")
for fname in numeric_fields:
    vals = []
    for row in rows:
        v = _to_num(row.get(fname))
        if v > 0:
            vals.append(v)
    if vals:
        print(f"  {fname:<22} 非零{len(vals):>5}行  min={min(vals):,.0f}  max={max(vals):,.0f}  avg={sum(vals)/len(vals):,.0f}")
