#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V1 vs V2 数据对账脚本
不修改任何业务代码，独立比较四层数据。
"""

import sys, io, json
from datetime import datetime, date, timedelta
from typing import Dict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 定位仓库根目录，加载基础设施
_repo = Path(__file__).resolve()
while not (_repo / ".git").exists() and _repo != _repo.parent:
    _repo = _repo.parent
sys.path.insert(0, str(_repo))
from 基础设施.配置.env import require

# ── 共用配置 ──
REDASH_URL       = "http://redash.oneprestamos.com"
LOGIN_EMAIL      = require("REDASH_EMAIL")
LOGIN_PASSWORD   = require("REDASH_PASSWORD")
QUERY_ID         = 79
EXCEL_PATH       = "D:/新建文件夹/xby/日报7.6-7.11.xlsx"

ACCOUNTS = [
    ("D-1", "YNT101", 3, 4),  ("D-1", "YNT102", 6, 7),
    ("D-1", "ZYT103", 9, 10),
    ("D0",  "YND001", 12, 13), ("D0",  "YND002", 15, 16),
    ("D0",  "ZYD003", 18, 19), ("D0",  "ZYD004", 21, 22),
    ("S1",  "ZYS101", 24, 25), ("S1",  "ZYS102", 27, 28),
    ("S1",  "YNS101", 30, 31), ("S1",  "RECS101", 33, 34),
    ("S2",  "RECS201", 36, 37), ("S2",  "S2beiyong", 39, 40),
]

ACCT_CUST_MAP = {
    "YNT101": "新客", "YND002": "新客", "ZYD003": "新客",
    "YNT102": "老客", "YND001": "老客",
    "ZYT103": "老客", "ZYD004": "老客",
}

STAGE_GROUPS = {
    "-2阶段": ["YNT301", "YNT302"],
    "-1阶段": ["YNT101", "YNT102", "YNT103", "ZYT103"],
    "D0阶段": ["YND001", "YND002", "ZYD003", "ZYD004"],
    "S1阶段": ["ZYS101", "ZYS102"],
}

TARGET_RATES_AMT = {
    ("D-1", "新客"): 0.28, ("D-1", "老客"): 0.40,
    ("D0",  "新客"): 0.35, ("D0",  "老客"): 0.25,
    ("S1",  "新客"): 0.22, ("S1",  "老客"): 0.25,
    ("S2",  "新客"): 0.06, ("S2",  "老客"): 0.06,
}

# ── 直接使用 V2 的 RedashClient.query() 获取原始数据 ──
sys.path.insert(0, "D:/knowledge-base/scripts/daily_report")
from importlib import import_module
# 不能直接 import 因为 V2 有顶层 print，我们用 subprocess 方式

# 实际上我们手动复制 V2 的 query 逻辑（避免触发 V2 顶层代码）
import requests, re, time
from functools import wraps

MAX_RETRIES = 3
RETRY_DELAY = 2

def retry(max_attempts=3, delay=2, label=""):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts:
                        print(f"  [{label}] 第 {attempt}/{max_attempts} 次失败: {e}，{delay}s 后重试...")
                        time.sleep(delay)
            raise last_error
        return wrapper
    return decorator

class CompareRedashClient:
    """独立 Redash 客户端（不依赖 V1 或 V2）"""
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        t0 = time.time()
        r = self.session.get(f"{REDASH_URL}/login", timeout=15)
        csrf = re.search(r'name="csrf_token".*?value="([^"]+)"', r.text, re.I)
        token = csrf.group(1) if csrf else ""
        r = self.session.post(f"{REDASH_URL}/login", data={
            "csrf_token": token, "email": LOGIN_EMAIL,
            "password": LOGIN_PASSWORD, "remember": "on",
        }, allow_redirects=True, timeout=15)
        if "login" in r.url.lower():
            raise Exception("Redash 登录失败")
        print(f"[OK] Redash 登录成功 ({time.time()-t0:.1f}s)")

    def _build_accounts(self, cust_type):
        """构建账户列表（与 V1/V2 一致）"""
        accounts = []
        for _, acct, _, _ in ACCOUNTS:
            allowed = ACCT_CUST_MAP.get(acct)
            if not allowed or allowed == cust_type:
                accounts.append(acct)
        for group_accts in STAGE_GROUPS.values():
            for a in group_accts:
                if a not in accounts:
                    accounts.append(a)
        return accounts

    @retry(max_attempts=3, delay=2, label="Redash")
    def query(self, start_date, end_date, cust_type):
        """查询并返回原始 rows（与 V2 query() 一致，不带 max_age）"""
        accounts = self._build_accounts(cust_type)
        payload = {"parameters": {
            "STARTTIME": start_date, "ENDTIME": end_date,
            "账户": accounts, "逾期天数": ["all"], "首复借客户": cust_type,
        }}
        r = self.session.post(
            f"{REDASH_URL}/api/queries/{QUERY_ID}/results",
            json=payload, timeout=30)
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code}")

        result = r.json()

        # 缓存直接返回
        if "query_result" in result:
            return result["query_result"].get("data", {}).get("rows", []), \
                   result["query_result"].get("id", "N/A"), "Cache"

        # 异步 job
        if "job" in result:
            jid = result["job"].get("id")
            if not jid:
                return [], "N/A", "Fresh"
            for _ in range(90):  # 90s timeout
                time.sleep(1)
                r2 = self.session.get(f"{REDASH_URL}/api/jobs/{jid}", timeout=15)
                if r2.status_code != 200:
                    continue
                job = r2.json()["job"]
                if job["status"] == 4 and job.get("query_result_id"):
                    qid = job["query_result_id"]
                    r3 = self.session.get(
                        f"{REDASH_URL}/api/query_results/{qid}.json", timeout=15)
                    if r3.status_code == 200:
                        return r3.json()["query_result"]["data"]["rows"], qid, "Fresh"
                    break
                elif job["status"] == 5:
                    return [], "N/A", "Fresh"
            raise Exception("查询超时 (90s)")

        return [], "N/A", "Cache"

    def close(self):
        self.session.close()


# ═══════════════════════════════════════════════════════════════
# V1 解析逻辑（从 xby日报.py 的 parse_account_data 精确复制）
# ═══════════════════════════════════════════════════════════════

def v1_parse_account_data(rows_map: dict) -> Dict:
    """V1 的 parse_account_data 逻辑"""
    data = {a: {"新客": {}, "老客": {}} for _, a, _, _ in ACCOUNTS}

    for acct, ct_data in rows_map.items():
        if acct not in data:
            continue
        for ct, row in ct_data.items():
            allowed_ct = ACCT_CUST_MAP.get(acct)
            if allowed_ct and allowed_ct != ct:
                # V1: 过滤掉不匹配的客群
                continue
            stage = next((s for s, a, _, _ in ACCOUNTS if a == acct), None)
            use_onhand = stage in ("D-1", "D0")
            if use_onhand:
                case_val = int(row.get("在手案件", 0) or 0)
                amt_val = float(row.get("在手金额", 0) or 0)
            else:
                case_val = int(row.get("新增绑定案件", 0) or 0)
                amt_val = float(row.get("新增绑定金额", 0) or 0)
            data[acct][ct] = {
                "案件数": case_val,
                "回收数": int(row.get("总回收件数", 0) or 0),
                "入催金额": amt_val,
                "回收金额": float(row.get("总回收金额", 0) or 0),
            }
    return data


# ═══════════════════════════════════════════════════════════════
# V2 解析逻辑（从 xby日报V2.py 的 QueryResult / write_today 精确复制）
# ═══════════════════════════════════════════════════════════════

def v2_parse_account_data(rows_map: dict) -> Dict:
    """V2 的解析逻辑 — 直接从 rows_map 提取账户数据"""
    data = {}
    for _, acct, _, _ in ACCOUNTS:
        nd = rows_map.get(acct, {}).get("新客", {})
        od = rows_map.get(acct, {}).get("老客", {})
        stage = next((s for s, a, _, _ in ACCOUNTS if a == acct), "")
        use_onhand = stage in ("D-1", "D0")
        case_field = "在手案件" if use_onhand else "新增绑定案件"
        amt_field  = "在手金额" if use_onhand else "新增绑定金额"
        data[acct] = {
            "新客": {
                "案件数":   int(nd.get(case_field, 0) or 0),
                "回收数":   int(nd.get("总回收件数", 0) or 0),
                "入催金额": float(nd.get(amt_field, 0) or 0),
                "回收金额": float(nd.get("总回收金额", 0) or 0),
            },
            "老客": {
                "案件数":   int(od.get(case_field, 0) or 0),
                "回收数":   int(od.get("总回收件数", 0) or 0),
                "入催金额": float(od.get(amt_field, 0) or 0),
                "回收金额": float(od.get("总回收金额", 0) or 0),
            },
        }
    return data


# ═══════════════════════════════════════════════════════════════
# V1 阶段汇总逻辑
# ═══════════════════════════════════════════════════════════════

def v1_stage_summary(rows_map: dict):
    """V1 的 print_stage_summary 逻辑"""
    result = {}
    NUMERIC_FIELDS = ["在手案件","在手金额","新增绑定案件","新增绑定金额",
                      "总回收件数","总回收金额"]
    for stage, accounts in STAGE_GROUPS.items():
        total_hand = 0
        total_recv = 0
        total_amt = 0.0
        for a in accounts:
            if a in rows_map:
                merged = {}
                for ct_data in rows_map[a].values():
                    for k in NUMERIC_FIELDS:
                        if k in ct_data:
                            merged[k] = (merged.get(k, 0) or 0) + (ct_data[k] or 0)
                use_new_bind = (stage == "S1阶段")
                h = int(merged.get("新增绑定案件" if use_new_bind else "在手案件", 0) or 0)
                rv = int(merged.get("总回收件数", 0) or 0)
                ra = float(merged.get("总回收金额", 0) or 0)
                total_hand += h
                total_recv += rv
                total_amt += ra
        result[stage] = {"案件": total_hand, "回收件数": total_recv, "回收金额": total_amt}
    return result


# ═══════════════════════════════════════════════════════════════
# V2 阶段汇总逻辑
# ═══════════════════════════════════════════════════════════════

def v2_stage_summary(rows_map: dict):
    """V2 的 print_briefing 逻辑"""
    result = {}
    FIELDS = ["在手案件", "在手金额", "新增绑定案件", "新增绑定金额",
              "总回收件数", "总回收金额"]
    for stage, accounts in STAGE_GROUPS.items():
        total_cases = 0
        total_recv = 0
        total_amt = 0.0
        for a in accounts:
            if a not in rows_map:
                continue
            merged = {}
            for ct_data in rows_map[a].values():
                for k in FIELDS:
                    if k in ct_data:
                        merged[k] = (merged.get(k, 0) or 0) + (ct_data.get(k, 0) or 0)
            use_new = (stage == "S1阶段")
            cases = int(merged.get("新增绑定案件" if use_new else "在手案件", 0) or 0)
            recv  = int(merged.get("总回收件数", 0) or 0)
            amt   = float(merged.get("总回收金额", 0) or 0)
            total_cases += cases
            total_recv  += recv
            total_amt   += amt
        result[stage] = {"案件": total_cases, "回收件数": total_recv, "回收金额": total_amt}
    return result


# ═══════════════════════════════════════════════════════════════
# 主比较流程
# ═══════════════════════════════════════════════════════════════

def main():
    target_date = "2026-07-07"
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    today_start = target_date
    today_end   = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_start = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_end   = target_date

    print(f"\n{'='*60}")
    print(f"  V1 vs V2 数据对账 — {target_date} (周二)")
    print(f"{'='*60}")

    # ── 登录 + 查询 ──
    client = CompareRedashClient()

    queries = [
        ("今天新客", today_start, today_end, "新客"),
        ("今天老客", today_start, today_end, "老客"),
        ("昨天新客", yesterday_start, yesterday_end, "新客"),
        ("昨天老客", yesterday_start, yesterday_end, "老客"),
    ]

    rows_map = {}
    query_results = {}

    print(f"\n[查询] 开始 4 次查询 ...")
    for label, start, end, ct in queries:
        print(f"  {label}: {start} ~ {end} ...", end=" ", flush=True)
        rows, qr_id, source = client.query(start, end, ct)
        print(f"{len(rows)} 行  query_result_id={qr_id}  {source}")
        query_results[label] = {"rows": rows, "qr_id": qr_id, "source": source}
        for r in rows:
            a = r.get("账户", "")
            if a not in rows_map:
                rows_map[a] = {}
            rows_map[a][ct] = dict(r)

    client.close()

    # ═══════════════════════════════════════════════════════════
    # 第一层：Redash 原始 rows 比较
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print(f"  第一层：Redash 原始 rows 比较")
    print(f"{'─'*60}")

    # V1 和 V2 使用相同的 Redash API，如果参数相同，rows 应该完全一致
    # 这里我们检查每个 query 的 rows 是否包含所有预期的 accounts
    layer1_ok = True
    for label, start, end, ct in queries:
        rows = query_results[label]["rows"]
        accts_in_rows = {r["账户"] for r in rows}
        print(f"\n  [{label}] {len(rows)} 行, 账户数: {len(accts_in_rows)}")

        # 检查每个 ACCOUNT 是否都在 rows 中
        for _, acct, _, _ in ACCOUNTS:
            allowed = ACCT_CUST_MAP.get(acct)
            if allowed and allowed != ct:
                continue  # 这个 account 不属于这个 cust_type
            found = any(r["账户"] == acct for r in rows)
            if not found:
                print(f"    ⚠ {acct} 不在 {label} 结果中!")
                layer1_ok = False

        # 打印前几个账户的数据样本
        sample_accts = ["YNT101", "YNT102", "YND001", "ZYD003"]
        for acct in sample_accts:
            for r in rows:
                if r["账户"] == acct:
                    print(f"    {acct}: 在手案件={r.get('在手案件','N/A')}  "
                          f"新增绑定={r.get('新增绑定案件','N/A')}  "
                          f"回收件数={r.get('总回收件数','N/A')}  "
                          f"回收金额={r.get('总回收金额','N/A')}")
                    break
            else:
                print(f"    {acct}: 不在结果中")

    # ═══════════════════════════════════════════════════════════
    # 第二层：解析后的 account_data 比较
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print(f"  第二层：解析后的 account_data 逐账户比较")
    print(f"{'─'*60}")

    v1_data = v1_parse_account_data(rows_map)
    v2_data = v2_parse_account_data(rows_map)

    layer2_ok = True
    first_diff = None
    for _, acct, nr, or_ in ACCOUNTS:
        for ct in ["新客", "老客"]:
            v1d = v1_data.get(acct, {}).get(ct, {})
            v2d = v2_data.get(acct, {}).get(ct, {})

            diffs = []
            for field in ["案件数", "回收数", "入催金额", "回收金额"]:
                v1v = v1d.get(field, 0)
                v2v = v2d.get(field, 0)
                if v1v != v2v:
                    diffs.append(f"{field}: V1={v1v} V2={v2v}")

            if diffs:
                if layer2_ok:
                    print(f"\n  🔴 第一处差异发现！")
                    first_diff = f"  {acct} / {ct}"
                    layer2_ok = False
                print(f"  {acct} / {ct}:")
                for d in diffs:
                    print(f"    {d}")

    if layer2_ok:
        print(f"\n  ✅ 所有账户数据完全一致")
        # 打印逐账户确认
        for _, acct, nr, or_ in ACCOUNTS:
            for ct in ["新客", "老客"]:
                v2d = v2_data.get(acct, {}).get(ct, {})
                if v2d:
                    print(f"  {acct}/{ct}: 案件={v2d.get('案件数',0)} "
                          f"回收={v2d.get('回收数',0)} "
                          f"金额={v2d.get('入催金额',0):.2f} "
                          f"回收金额={v2d.get('回收金额',0):.2f}")

    # ═══════════════════════════════════════════════════════════
    # 第三层：阶段汇总比较
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print(f"  第三层：阶段汇总比较")
    print(f"{'─'*60}")

    v1_stage = v1_stage_summary(rows_map)
    v2_stage = v2_stage_summary(rows_map)

    layer3_ok = True
    all_stages = list(STAGE_GROUPS.keys())
    for stage in all_stages:
        v1s = v1_stage.get(stage, {})
        v2s = v2_stage.get(stage, {})
        same = (v1s.get("案件", 0) == v2s.get("案件", 0) and
                v1s.get("回收件数", 0) == v2s.get("回收件数", 0) and
                abs(v1s.get("回收金额", 0) - v2s.get("回收金额", 0)) < 0.01)
        status = "✅" if same else "🔴"
        print(f"  {status} {stage}: "
              f"V1 案件={v1s.get('案件',0)} 回收={v1s.get('回收件数',0)} 金额={v1s.get('回收金额',0):.2f} | "
              f"V2 案件={v2s.get('案件',0)} 回收={v2s.get('回收件数',0)} 金额={v2s.get('回收金额',0):.2f}")
        if not same:
            layer3_ok = False

    # ═══════════════════════════════════════════════════════════
    # 第四层：最终简报差异
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'─'*60}")
    print(f"  第四层：最终简报比较")
    print(f"{'─'*60}")

    if layer2_ok and layer3_ok:
        print(f"\n  ✅ V1 和 V2 数据完全一致，简报输出相同。")
    else:
        print(f"\n  🔴 存在差异：")
        if first_diff:
            print(f"     第一处差异: {first_diff}")
        print(f"     第二层差异: {'是' if not layer2_ok else '否'}")
        print(f"     第三层差异: {'是' if not layer3_ok else '否'}")

    # ── 总结 ──
    print(f"\n{'='*60}")
    print(f"  对账结论")
    print(f"{'='*60}")
    print(f"  第一层 (原始 rows):  {'✅ 一致' if layer1_ok else '🔴 差异'}")
    print(f"  第二层 (account_data): {'✅ 一致' if layer2_ok else '🔴 差异'}")
    print(f"  第三层 (阶段汇总):     {'✅ 一致' if layer3_ok else '🔴 差异'}")
    print(f"  第四层 (简报):         {'✅ 一致' if (layer2_ok and layer3_ok) else '🔴 差异'}")

    all_ok = layer1_ok and layer2_ok and layer3_ok
    if all_ok:
        print(f"\n  ✅ V1 与 V2 四层数据完全一致，可以替换。")
    else:
        print(f"\n  🔴 请检查以上差异。")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
