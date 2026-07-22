#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
墨西哥每日案件及人力预估 V2

全新架构：
  - 不再依赖 Excel，所有数据从 API 获取
  - 每天自动获取案件数据和实际上班人力
  - 自动完成业务计算 → 生成图片 → 发送飞书

用法:
    python 墨西哥每日案件及人力预估_v2.py                    # 发送卡片
    python 墨西哥每日案件及人力预估_v2.py --dry-run           # 计算不发送
    python 墨西哥每日案件及人力预估_v2.py --text-only         # 纯文本降级
    python 墨西哥每日案件及人力预估_v2.py --list-chats        # 列出群聊
"""

import base64
import io
import json
import math
import os
import sys
import time
import argparse
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
from pathlib import Path

import requests

# 定位仓库根目录，加载基础设施
_repo = Path(__file__).resolve()
while not (_repo / ".git").exists() and _repo != _repo.parent:
    _repo = _repo.parent
sys.path.insert(0, str(_repo))
from 基础设施.配置.env import require, get

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ============================================================
#  1. 全局配置
# ============================================================

# -- Quick BI 配置 --
QBI_AK = require("QBI_ACCESS_KEY")
QBI_SK = require("QBI_SECRET_KEY")
QBI_ENDPOINT = "quickbi-public.cn-hangzhou.aliyuncs.com"
QBI_API_ID = "c2f93e0fa45b"  # 到期案件数据 DataService
QBI_AI_FOLLOW_API_ID = "e4ef78db8219"  # AI 催收回款监控
QBI_WS_ID = "f5e9cd70-8d92-48d6-8cc9-67f37c1c6e8d"

# -- 飞书配置 --
FEISHU_APP_ID = get("FEISHU_COLLECTION_APP_ID")
FEISHU_APP_SECRET = get("FEISHU_COLLECTION_APP_SECRET")
# 飞书接收人（可通过环境变量 FEISHU_CHAT_ID 覆盖）
#   群聊「资产管理部」: oc_8b5ef4aee4e93b29326cd8c0f3c24d90
#   私聊「我」        : ou_ffab3a07f1ff9fbca2a593c0d5e152ac
FEISHU_CHAT_ID = os.environ.get("FEISHU_CHAT_ID", "oc_8b5ef4aee4e93b29326cd8c0f3c24d90")

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
FEISHU_CHAT_LIST_URL = "https://open.feishu.cn/open-apis/im/v1/chats"

# -- 催收系统配置 --
COLLECT_URL = os.environ.get(
    "COLLECT_URL",
    "https://loan-collect.maxicredito.loan/vitech-collect-gateway/collect/staff/staffSchedulePage",
)
COLLECT_TOKEN = os.environ.get(
    "MEX_COLLECT_TOKEN",
    "",
)
COLLECT_REGION = os.environ.get("COLLECT_REGION", "MX")  # MX / AR

# queryConfig 接口（排班人力数据）
COLLECT_CONFIG_URL = os.environ.get(
    "COLLECT_CONFIG_URL",
    "https://loan-collect.maxicredito.loan/vitech-collect-gateway/collect/ailibty/queryConfig",
)

# -- 机构列表 --
#   TODO: 替换为 queryOrganPage API 动态获取
DEPT_LIST = [
    {"deptId": 26, "deptName": "PL"},
    {"deptId": 30052, "deptName": "FI"},
    {"deptId": 22, "deptName": "CX"},
]
DEPT_ORDER = {"PL": 0, "FI": 1, "CX": 2}

# -- DEBUG 模式 --
#   DEBUG=True 时，催收系统 API 读取 sample_attendance.json 代替真实请求
DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")

# sample 文件路径（与脚本同目录）
SAMPLE_ATTENDANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_attendance.json")


# ============================================================
#  2. 业务规则中心
# ============================================================

# ------------------------------------------------------------------
# 说明
# ------------------------------------------------------------------
#
# 以下为项目所有业务规则的唯一来源。
# 后续所有业务计算禁止硬编码字符串（如 if stage == "D0"），
# 必须统一引用此处的 TEAM / STAGE / REGION / RULE。
#
# ------------------------------------------------------------------

# -- 团队定义 --
TEAM = {
    "PL": "PL",
    "FI": "FI",
    "CX": "CX",
}
TEAM_LIST = ["PL", "FI", "CX"]

# -- 逾期阶段定义 --
STAGE = {
    "D-1": "D-1",   # 明日到期
    "D0":  "D0",    # 今日到期
    "D1":  "D1",    # 逾期 1 天
    "S1":  "S1",    # 逾期 2-3 天
    "S2":  "S2",    # 逾期 4-7 天
    "S3":  "S3",    # 逾期 8-15 天
}
STAGE_LIST = ["D-1", "D0", "D1", "S1", "S2", "S3"]

# -- 地区定义 --
REGION = {
    "MX": "MX",     # 墨西哥
    "AR": "AR",     # 阿根廷
}
REGION_LIST = ["MX", "AR"]

# -- 业务规则（统一读取，禁止硬编码） --
#   TARGET key 格式: "{STAGE}_{CUSTOMER}" 或 "{STAGE}"（S1/S2 不区分客群）
RULE = {
    "TARGET": {
        "D-1_NEW": 40, "D-1_OLD": 40,
        "D0_NEW":  40, "D0_OLD":  40,
        "D1_NEW":  40, "D1_OLD":  40,
        "S1": 80,
        "S2": 120,
        # S3 第一版暂不参与预警（无 target，forecast 仍生成但 warning_level=GREEN）
    },
    "WARNING_GREEN_THRESHOLD": 0.9,  # forecast_load < target × 此值 → GREEN; 0.9×target ~ target → YELLOW; > target → RED
}


# ============================================================
#  3. 工具函数
# ============================================================

def _to_int(val):
    """安全转 int，空值 / '-' 返回 0"""
    if val is None or val == "-" or val == "":
        return 0
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


def _to_num(val):
    """安全转 float，空值 / '-' 返回 0.0"""
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ============================================================
#  4. Quick BI 数据获取
# ============================================================

def _qbi_sign(action, extra_params):
    """生成阿里云 API 签名并调用 Quick BI"""
    import hmac as _hmac
    import urllib.parse as _up

    params = {
        "Format": "json",
        "Version": "2022-01-01",
        "AccessKeyId": QBI_AK,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "SignatureVersion": "1.0",
        "SignatureNonce": str(int(time.time() * 1000000) + hash(action) % 1000000),
        "Action": action,
    }
    params.update(extra_params)

    sorted_keys = sorted(params.keys())
    canonicalized = "&".join([
        f"{_up.quote(k, safe='')}={_up.quote(str(params[k]), safe='')}"
        for k in sorted_keys
    ])
    string_to_sign = (
        f"POST&{_up.quote('/', safe='')}&{_up.quote(canonicalized, safe='')}"
    )
    sig = base64.b64encode(
        _hmac.new(f"{QBI_SK}&".encode(), string_to_sign.encode(), "sha1").digest()
    ).decode()
    params["Signature"] = sig

    url = f"https://{QBI_ENDPOINT}/?" + _up.urlencode(params)
    resp = requests.post(url, timeout=60)
    return resp.json()


def fetch_quickbi_entries():
    """
    从 Quick BI DataService API 直接取数。

    返回格式:
        [
            {
                "name": "分期新客",
                "summary": {"D-1": N, "D0": N, "D1": N, "S1": N, "S2": N, "S3": N},
                "ref_date": "YYYYMMDD",
                "cohorts": [{"date": "YYYYMMDD", "到期数": N, "pd_-2": N, ...}, ...],
            },
            ...
        ]
    """
    print("[*] 从 Quick BI 取数...")

    # 日期窗口: 今天-17 天 ~ 今天+1 天
    today = date.today()
    window_start = (today - timedelta(days=17)).strftime("%Y%m%d")
    window_end = (today + timedelta(days=1)).strftime("%Y%m%d")

    fields = json.dumps([
        "app", "due_date", "order", "cust_type", "mult_no",
        "case", "pd_2", "pd_1", "pd0", "pd1", "pd2",
        "pd3", "pd4", "pd5", "pd6", "pd7",
    ])
    conditions = json.dumps({"due_date": window_start}, ensure_ascii=False)
    extra = {
        "ApiId": QBI_API_ID,
        "ReturnFields": fields,
        "Conditions": conditions,
    }
    print(f"  Conditions: {conditions}")
    data = _qbi_sign("QueryDataService", extra)
    if not data.get("Success"):
        print(f"  [!] 完整 Response: {json.dumps(data, ensure_ascii=False)[:500]}")
        raise Exception(f"Quick BI 取数失败: {data.get('Message', 'Unknown')}")

    rows = data.get("Result", {}).get("Values", [])
    print(f"  获取 {len(rows)} 行原始数据")
    rows = [r for r in rows if window_start <= r.get("due_date", "") <= window_end]
    print(f"  过滤窗口 {window_start}-{window_end}: {len(rows)} 行")

    # 墨西哥 APP 白名单
    MX_APPS = {"AndaLana", "Cridit", "Kredizo", "ServiCash", "TruCred"}

    # 订单类型映射: 非单期=分期, 非分期=单期
    order_map = {
        "借款分期": "分期", "展期N期": "分期", "展期分期": "分期",
        "非分期": "单期",
    }

    # 可用列（pd8+ 此 API 不返回）
    col_keys = [
        "case", "pd_2", "pd_1", "pd0", "pd1", "pd2",
        "pd3", "pd4", "pd5", "pd6", "pd7",
    ]
    col_labels = [
        "到期数", "pd_-2", "pd_-1", "pd0", "pd1", "pd2",
        "pd3", "pd4", "pd5", "pd6", "pd7",
    ]

    # 按 (due_date, order_prefix, cust) 聚合
    pivot = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    for r in rows:
        app = r.get("app", "")
        if app not in MX_APPS:
            continue
        order = r.get("order", "")
        if order not in order_map:
            continue
        prefix = order_map[order]
        cust = r.get("cust_type", "")
        due = r.get("due_date", "")
        if not due:
            continue
        for ck in col_keys:
            raw = r.get(ck, "-")
            try:
                v = int(raw) if raw and raw != "-" else 0
            except (ValueError, TypeError):
                v = 0
            pivot[(prefix, cust)][due][ck] += v

    # 构造输出（固定 4 入口顺序）
    entries = []
    for (prefix, cust) in [
        ("分期", "新客"), ("分期", "老客"), ("单期", "新客"), ("单期", "老客"),
    ]:
        name = f"{prefix}{cust}"
        data_map = pivot.get((prefix, cust), {})
        if not data_map:
            continue

        # 构建 cohorts 列表（按日期降序）
        cohorts = []
        for due in sorted(data_map.keys(), reverse=True):
            d = data_map[due]
            cohort = {"date": due}
            for ck, cl in zip(col_keys, col_labels):
                cohort[cl] = d.get(ck, 0)
            cohort["到期数"] = d.get("case", 0)
            cohorts.append(cohort)

        summary = _compute_summary(cohorts)
        ref_date = _find_reference_date(cohorts)

        entries.append({
            "name": name,
            "summary": summary,
            "ref_date": ref_date,
            "cohorts": cohorts,
        })

        s = summary
        print(f"  [{name}] D-1={s['D-1']} D0={s['D0']} D1={s['D1']} "
              f"S1={s['S1']} S2={s['S2']} S3={s['S3']} (ref={ref_date})")

    return entries


def _compute_summary(cohorts):
    """
    从队列数据计算 D-1 / D0 / D1 / S1 / S2 / S3。

    以"今天"为基准，按借款结束日期判断逾期阶段：
      D-1 = 明天到期   (diff = -1)
      D0  = 今天到期   (diff = 0)
      D1  = 昨天到期   (diff = 1)
      S1  = 2-3 天前   (diff = 2, 3)
      S2  = 4-7 天前   (diff = 4-7)
      S3  = 8-15 天前  (diff = 8-15)
    """
    if not cohorts:
        return {"D-1": 0, "D0": 0, "D1": 0, "S1": 0, "S2": 0, "S3": 0}

    today = date.today()
    summary = {"D-1": 0, "D0": 0, "D1": 0, "S1": 0, "S2": 0, "S3": 0}

    for c in cohorts:
        cd_str = c.get("date", "")
        if not cd_str:
            continue
        try:
            cd = date(int(cd_str[:4]), int(cd_str[4:6]), int(cd_str[6:8]))
        except Exception:
            continue
        diff = (today - cd).days  # 正数=已逾期, 负数=未到期

        if diff == -1:
            summary["D-1"] += c.get(
                "pd_-1",
                c.get("到期数", 0) if c.get("pd_-1", 0) == 0 else 0,
            )
        elif diff == 0:
            summary["D0"] += c.get("pd0", 0)
        elif diff == 1:
            summary["D1"] += c.get("pd1", 0)
        elif 2 <= diff <= 3:
            summary["S1"] += c.get(f"pd{diff}", 0)
        elif 4 <= diff <= 7:
            summary["S2"] += c.get(f"pd{diff}", 0)
        elif 8 <= diff <= 15:
            summary["S3"] += c.get(f"pd{diff}", 0)

    # D-1 fallback: 如果 pd_-1 为 0，用到期数兜底
    if summary["D-1"] == 0:
        for c in cohorts:
            cd_str = c.get("date", "")
            if not cd_str:
                continue
            try:
                cd = date(int(cd_str[:4]), int(cd_str[4:6]), int(cd_str[6:8]))
            except Exception:
                continue
            if (today - cd).days == -1:
                summary["D-1"] = c.get("到期数", 0)
                break

    return summary


def _find_reference_date(cohorts):
    """找到第一个 pd0 > 0 的日期作为参考日期"""
    for c in cohorts:
        if c.get("pd0", 0) > 0:
            return c["date"]
    return cohorts[0]["date"] if cohorts else None


# ============================================================
#  5. 催收系统 API
# ============================================================

def get_staff_schedule():
    """
    从催收系统获取排班数据。

    DEBUG=False: 请求真实 API，POST 方式，带 X-Fixed-Token 认证。
    DEBUG=True:  读取本地 sample_attendance.json。

    异常处理：
      - 请求失败 / 超时 / Token 失效 / 返回格式错误 → 打印日志，返回 None
      - 不抛出异常，不退出程序

    Returns:
        dict | None: 原始 JSON 响应，失败时返回 None
    """
    if DEBUG:
        print("[DEBUG] 催收系统: 读取本地 sample_attendance.json")
        if not os.path.exists(SAMPLE_ATTENDANCE):
            print(f"  [!] 文件不存在: {SAMPLE_ATTENDANCE}")
            return None
        try:
            with open(SAMPLE_ATTENDANCE, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"  获取 {_count_staff_records(data)} 条排班数据 (offline)")
            return data
        except json.JSONDecodeError as e:
            print(f"  [!] JSON 解析失败: {e}")
            return None
        except Exception as e:
            print(f"  [!] 读取文件失败: {e}")
            return None

    # ---- 真实 API 请求 ----
    if not COLLECT_URL:
        print("[!] 催收系统: COLLECT_URL 未配置，跳过")
        return None
    if not COLLECT_TOKEN:
        print("[!] 催收系统: COLLECT_TOKEN 未配置，跳过")
        return None

    print(f"[*] 催收系统: 请求排班数据 (region={COLLECT_REGION})")
    headers = {
        "Content-Type": "application/json",
        "X-CHOICE-TAG": "ms",
        "X-END-LANGUAGE": "zh_cn",
        "X-Fixed-Token": COLLECT_TOKEN,
    }

    # 构造请求体（墨西哥时间 UTC-6）
    mx_tz = timezone(timedelta(hours=-6))
    now_mx = datetime.now(mx_tz)
    today_mx = now_mx.date()
    week_ago_mx = today_mx - timedelta(days=7)

    # scheduleStart / scheduleEnd: 毫秒时间戳
    start_dt = datetime(week_ago_mx.year, week_ago_mx.month, week_ago_mx.day,
                        tzinfo=mx_tz)
    end_dt = datetime(today_mx.year, today_mx.month, today_mx.day,
                      tzinfo=mx_tz)
    schedule_start_ms = int(start_dt.timestamp() * 1000)
    schedule_end_ms = int(end_dt.timestamp() * 1000)

    # date: ISO8601 格式
    date_iso = now_mx.strftime("%Y-%m-%dT%H:%M:%S")

    body = {
        "region": COLLECT_REGION,
        "date": date_iso,
        "scheduleStart": schedule_start_ms,
        "scheduleEnd": schedule_end_ms,
        "page": 1,
        "rows": 200,
        "staffStatus": "1",
        "secondLevelDeptId": "",
        "thirdLevelDeptId": "",
        "fourthLevelDeptId": "",
        "staffName": "",
        "overdueLevelIdList": [],
        "scheduleStatusList": [],
    }

    try:
        resp = requests.post(
            COLLECT_URL,
            headers=headers,
            json=body,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        print(f"  [!] 请求超时 (30s)")
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"  [!] 连接失败: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"  [!] 请求异常: {e}")
        return None

    # 检查 HTTP 状态码
    if resp.status_code != 200:
        print(f"  [!] HTTP 状态码异常: {resp.status_code}")
        if resp.status_code in (401, 403):
            print(f"  [!] Token 可能已失效，请检查 COLLECT_TOKEN")
        # 打印完整请求体（脱敏 Token）
        print(f"  [DEBUG] 请求体: {json.dumps(body, ensure_ascii=False)}")
        print(f"  响应内容: {resp.text[:500]}")
        return None

    # 解析 JSON
    try:
        data = resp.json()
    except (ValueError, AttributeError) as e:
        print(f"  [!] JSON 解析失败: {e}")
        print(f"  响应内容: {resp.text[:500]}")
        return None

    # ---- 调试日志：确认接口真实返回结构 ----
    print(f"  [DEBUG] resultCode: {data.get('resultCode')}")
    print(f"  [DEBUG] message: {data.get('message')}")
    print(f"  [DEBUG] trans: {data.get('trans')}")
    d = data.get("data")
    if d is None:
        print(f"  [DEBUG] data is None")
    elif isinstance(d, list):
        print(f"  [DEBUG] data is list, len={len(d)}")
    elif isinstance(d, dict):
        print(f"  [DEBUG] data is dict, keys={list(d.keys())}")
    else:
        print(f"  [DEBUG] data type: {type(d).__name__}")
    # ----------------------------------------------------

    # 基本格式校验
    if not isinstance(data, dict):
        print(f"  [!] 返回格式异常: 期望 dict，实际 {type(data).__name__}")
        return None

    n = _count_staff_records(data)
    print(f"  催收系统连接成功，获取 {n} 条排班数据")
    return data


def fetch_organ_page():
    """
    获取全部启用机构列表。

    TODO: 替换为 queryOrganPage API 动态获取。
    当前使用硬编码 DEPT_LIST。

    返回: list[dict] — [{"deptId": int, "deptName": str}, ...]
    """
    print(f"[*] 催收系统: 机构列表 ({len(DEPT_LIST)} 个): "
          f"{', '.join(d['deptName'] for d in DEPT_LIST)}")
    return list(DEPT_LIST)


def get_query_config(dept_list=None):
    """
    从催收系统 queryConfig 接口获取人力配置。

    对每个机构分别调用 queryConfig(deptId)，合并所有 deptDetails。
    每条 deptDetails 增加 deptId、deptName 字段标识来源机构。

    Args:
        dept_list: fetch_organ_page() 的返回值，默认自动调用

    返回: dict | None — data.deptDetails 为合并后的排班人力列表
    """
    if dept_list is None:
        dept_list = fetch_organ_page()

    if not dept_list:
        print("[!] 催收系统: 无机构数据，跳过")
        return None

    if not COLLECT_CONFIG_URL:
        print("[!] 催收系统: COLLECT_CONFIG_URL 未配置，跳过")
        return None

    print(f"[*] 催收系统: 请求 queryConfig (region={COLLECT_REGION})")
    headers = {
        "Content-Type": "application/json",
        "X-CHOICE-TAG": "ms",
        "X-END-LANGUAGE": "zh_cn",
        "X-Fixed-Token": COLLECT_TOKEN,
    }

    all_details = []

    for dept in dept_list:
        dept_id = dept["deptId"]
        dept_name = dept["deptName"]
        body = {"deptId": dept_id}

        try:
            resp = requests.post(
                COLLECT_CONFIG_URL,
                headers=headers,
                json=body,
                timeout=30,
            )
        except requests.exceptions.Timeout:
            print(f"  [!] deptId={dept_id} ({dept_name}) 请求超时，跳过")
            continue
        except requests.exceptions.ConnectionError as e:
            print(f"  [!] deptId={dept_id} ({dept_name}) 连接失败: {e}，跳过")
            continue
        except requests.exceptions.RequestException as e:
            print(f"  [!] deptId={dept_id} ({dept_name}) 请求异常: {e}，跳过")
            continue

        if resp.status_code != 200:
            print(f"  [!] deptId={dept_id} ({dept_name}) HTTP {resp.status_code}，跳过")
            continue

        try:
            data = resp.json()
        except (ValueError, AttributeError):
            print(f"  [!] deptId={dept_id} ({dept_name}) JSON 解析失败，跳过")
            continue

        if not isinstance(data, dict):
            print(f"  [!] deptId={dept_id} ({dept_name}) 返回格式异常，跳过")
            continue

        inner = data.get("data", {})
        if isinstance(inner, dict):
            details = inner.get("deptDetails")
            if isinstance(details, list):
                # 为每条记录注入机构信息
                for d in details:
                    d["deptId"] = dept_id
                    d["deptName"] = dept_name
                all_details.extend(details)
                print(f"  deptId={dept_id} ({dept_name}): {len(details)} 条")
            else:
                print(f"  [!] deptId={dept_id} ({dept_name}) deptDetails 非列表")
        else:
            print(f"  [!] deptId={dept_id} ({dept_name}) data 非 dict")

    if not all_details:
        print(f"  [!] 所有机构均无数据")
        return None

    print(f"  queryConfig 合计: {len(all_details)} 条 deptDetails")
    return {"data": {"deptDetails": all_details}}


def _count_staff_records(data):
    """
    统计排班数据条数（只读，不做任何业务计算）。

    优先解析: data["data"]["rows"]
    降级查找: data["rows"] / data["records"] / data["items"] / ...
    """
    if not isinstance(data, dict):
        return 0
    # 优先: response.data.rows
    rows = _extract_rows(data)
    if rows is not None:
        return len(rows)
    return 0


def _extract_rows(data):
    """
    从催收系统响应中提取排班列表。

    优先: data["data"]["rows"]
    降级: data["rows"] / data["records"] / data["items"] / ...
    返回: list | None
    """
    if not isinstance(data, dict):
        return None
    # 优先路径: response.data.rows
    inner = data.get("data")
    if isinstance(inner, dict):
        rows = inner.get("rows")
        if isinstance(rows, list):
            return rows
    # 降级: 顶层列表字段
    for key in ("rows", "data", "list", "records", "items", "result", "schedule", "staff"):
        val = data.get(key)
        if isinstance(val, list):
            return val
    # 递归尝试嵌套
    for val in data.values():
        if isinstance(val, list):
            return val
    return None


# ============================================================
#  6. 数据检查（先预留）
# ============================================================

# TODO: 后续添加数据完整性检查
#   - Quick BI 返回数据行数校验
#   - 日期窗口内数据覆盖检查
#   - 各入口数据非空校验
#   - 异常值告警


# ============================================================
#  7. 数据标准化
# ============================================================

# ------------------------------------------------------------------
# 统一数据结构说明
# ------------------------------------------------------------------
#
# 业务计算模块只能读取以下两种统一对象，
# 不能直接读取 Quick BI JSON 或催收系统 JSON。
#
# ① Case 对象:
#     {
#         "category": str,       # "分期新客" / "分期老客" / "单期新客" / "单期老客"
#         "type": str,           # "分期" / "单期"
#         "customer": str,       # "新客" / "老客"
#         "stages": {            # 各逾期阶段单数
#             "D-1": int, "D0": int, "D1": int,
#             "S1": int, "S2": int, "S3": int,
#         },
#         "ref_date": str,       # "YYYYMMDD" 参考日期
#         "cohorts": [...],      # 原始队列数据
#     }
#
# ② Attendance 对象:
#     {
#         "stage": str,          # "D-1" / "D0" / "D1" / "S1" / "S2" / "S3"
#         "customer": str|None,  # "新客" / "老客" / None（全客群）
#         "attendance": int,     # 排班人数
#         "deptId": int,         # 机构 ID
#         "deptName": str,       # 机构名称 "PL" / "WB-FI" / "WB-CX"
#     }
# ------------------------------------------------------------------


def normalize_case(entry_data_list):
    """
    将 Quick BI 原始入口数据转换为统一的 Case 对象列表。

    Args:
        entry_data_list: fetch_quickbi_entries() 的返回值

    Returns:
        list[dict]: 统一 Case 对象列表
    """
    cases = []
    for e in entry_data_list:
        name = e.get("name", "")

        # 从 name 拆分 type
        if "分期" in name:
            case_type = "分期"
        elif "单期" in name:
            case_type = "单期"
        else:
            case_type = name

        # 从 name 拆分 customer
        if "新客" in name:
            customer = "新客"
        elif "老客" in name:
            customer = "老客"
        else:
            customer = name

        cases.append({
            "category": name,
            "type": case_type,
            "customer": customer,
            "stages": dict(e.get("summary", {})),
            "ref_date": e.get("ref_date"),
            "cohorts": list(e.get("cohorts", [])),
        })

    print(f"  [标准化] Case: {len(cases)} 条")
    return cases


# -- deptDetails 字段映射（V1 固定映射，后续可迁移至 RULE） --
_STAGE_MAP = {2: "D-1", 4: "D0", 17: "D1", 18: "S1", 19: "S2", 20: "S3"}
_CUST_MAP = {1: "新客", 2: "老客", 0: None}  # 0 → 全部客群


def normalize_attendance(raw_data):
    """
    将 queryConfig 返回的 deptDetails 转换为统一的 Attendance 对象列表。

    每条 deptDetails:
      overdueLevelId → stage (固定映射)
      platformNew    → customer (固定映射)
      tomorrowStaffAgentCount → 生成 N 条 attendance 记录

    caseCapacity、floatCaseNum 保留原始字段，暂不参与业务计算。

    Args:
        raw_data: get_query_config() 的返回值（dict 或 None）

    Returns:
        list[dict] | None
    """
    if raw_data is None:
        print(f"  [标准化] Attendance: 无数据，跳过")
        return None

    if not isinstance(raw_data, dict):
        print(f"  [标准化] Attendance: 数据格式异常 "
              f"(type={type(raw_data).__name__})，跳过")
        return None

    # 定位 deptDetails
    inner = raw_data.get("data", {})
    dept_list = None
    if isinstance(inner, dict):
        dept_list = inner.get("deptDetails")
        if not isinstance(dept_list, list):
            # 降级: 旧格式 staffSchedulePage → data.rows
            dept_list = _extract_rows(raw_data)

    if not dept_list:
        print(f"  [标准化] Attendance: 未找到 deptDetails 或 rows")
        return None

    # ---- 调试 ----
    if dept_list:
        print(f"  [DEBUG] deptDetails[0]:")
        print(f"  {json.dumps(dept_list[0], ensure_ascii=False, indent=2)}")
    # --------------

    attendance = []
    skipped = 0

    for i, rec in enumerate(dept_list):
        if not isinstance(rec, dict):
            skipped += 1
            continue

        # overdueLevelId → stage
        oid = rec.get("overdueLevelId")
        stage = _STAGE_MAP.get(oid)
        if stage is None:
            skipped += 1
            continue

        # platformNew → customer
        pn = rec.get("platformNew")
        customer = _CUST_MAP.get(pn, None)

        # tomorrowStaffAgentCount → 汇总 attendance
        count = rec.get("tomorrowStaffAgentCount", 0)
        if isinstance(count, str):
            try:
                count = int(count)
            except (ValueError, TypeError):
                count = 0
        count = max(int(count), 0)

        dept_id = rec.get("deptId", "")
        dept_name = rec.get("deptName", "")

        attendance.append({
            "stage": stage,
            "customer": customer,
            "attendance": count,
            "deptId": dept_id,
            "deptName": dept_name,
            # 保留原始字段（暂不参与业务计算）
            "_case_capacity": rec.get("caseCapacity"),
            "_float_case_num": rec.get("floatCaseNum"),
        })

    if skipped:
        print(f"  [标准化] Attendance: 跳过 {skipped} 条 (stage/customer 无映射)")
    print(f"  [标准化] Attendance: {len(attendance)} 条")
    return attendance if attendance else None


def build_manual_attendance():
    """
    临时模式：手动排班人数。

    优先读取同目录下 manual_staff_template.json，
    不存在则使用代码内置默认值。

    Returns:
        list[dict] — 与 normalize_attendance 输出格式一致
    """
    import os as _os
    template_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                  "manual_staff_template.json")
    staff_config = []
    source_label = ""
    if _os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as _f:
                data = json.load(_f)
            for item in data.get("attendance", []):
                cust = item.get("客群", item.get("customer", ""))
                staff_config.append((
                    item.get("阶段", item.get("stage", "")),
                    None if cust in ("合计", "") else cust,
                    item.get("机构", item.get("deptName", "")),
                    int(item.get("人数", item.get("count", 0))),
                ))
            source_label = f" (from {_os.path.basename(template_path)})"
        except Exception as _e:
            print(f"  [!] 读取模板失败: {_e}，使用内置默认值")
            staff_config = []
    if not staff_config:
        print(f"  [!] 未找到 {_os.path.basename(template_path)}，请先创建模板文件")
        print(f"  路径: {template_path}")
        sys.exit(1)
    attendance = []
    for stage, customer, dept_name, count in staff_config:
        attendance.append({
            "stage": stage,
            "customer": customer,
            "attendance": count,
            "deptId": "",
            "deptName": dept_name,
        })
    print(f"\n[STAFF SOURCE] manual stage-level input (no intranet){source_label}")
    print(f"  手动排班 {len(attendance)} 条:")
    for a in attendance:
        cust_str = a["customer"] if a["customer"] else "合计"
        print(f"    {a['stage']:<4} {cust_str:<4} {a['deptName']:<4} x{a['attendance']}")
    print()
    return attendance


# ============================================================
#  7.5 AI 持续跟进 — 修正 D0 Forecast
# ============================================================

def fetch_ai_follow_cases():
    """
    从 Quick BI AI催收回款监控（ApiId: e4ef78db8219）获取昨日 D-1 数据。

    筛选三种还款意愿：
      - 承诺还款-无明确还款日期
      - 承诺还款-明确还款日期
      - 还款意愿含糊

    只取 overdue_bucket=D-1 的记录。
    D-1累计回款率 = D_1_pay_amount / case_amount（"-" 视为 0）。

    返回: list[dict] — 每条记录包含:
        {"customer": "新客"/"老客", "will_type": str, "case_count": int, "d1_rate": float}
    """
    from datetime import timedelta

    yesterday = (date.today() - timedelta(days=1)).strftime("%Y%m%d")

    print(f"[*] AI 跟进: 获取昨日 D-1 数据 (call_day={yesterday})")

    conditions = json.dumps({"call_day": yesterday}, ensure_ascii=False)
    extra = {
        "ApiId": QBI_AI_FOLLOW_API_ID,
        "Conditions": conditions,
    }
    try:
        data = _qbi_sign("QueryDataService", extra)
    except Exception as e:
        print(f"  [!] AI 跟进 API 调用失败: {e}，D0 将不修正")
        return []

    if not data.get("Success"):
        print(f"  [!] AI 跟进 API 失败: {data.get('Message', 'Unknown')}，D0 将不修正")
        return []

    rows = data.get("Result", {}).get("Values", [])
    print(f"  获取 {len(rows)} 行")

    # 三种目标意愿
    TARGET_WILLS = {
        "承诺还款-无明确还款日期",
        "承诺还款-明确还款日期",
        "还款意愿含糊",
    }

    def _parse_amount(val):
        """解析金额字符串（可能含逗号、负号），返回 float"""
        if val is None or val == "-" or val == "":
            return 0.0
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0

    results = []
    for r in rows:
        # 仅取 D-1 阶段
        bucket = r.get("overdue_bucket", "")
        if bucket != "D-1":
            continue

        will = r.get("willing_to_pay", "")
        if will not in TARGET_WILLS:
            continue

        cust = r.get("cust_type", "")
        if cust == "新客":
            customer = "新客"
        elif cust == "老客":
            customer = "老客"
        else:
            continue

        case_count = _to_int(r.get("case", 0))
        case_amt = _parse_amount(r.get("case_amount", "0"))
        d1_pay = _parse_amount(r.get("D_1_pay_amount", "0"))
        d1_rate = d1_pay / case_amt if case_amt > 0 else 0.0

        results.append({
            "customer": customer,
            "will_type": will,
            "case_count": case_count,
            "d1_rate": d1_rate,
        })

    # 按客群+意愿汇总打印
    if results:
        from collections import defaultdict as _dd
        _agg = _dd(lambda: {"case": 0, "rate_sum": 0.0, "cnt": 0})
        for rec in results:
            k = (rec["customer"], rec["will_type"])
            _agg[k]["case"] += rec["case_count"]
            _agg[k]["rate_sum"] += rec["d1_rate"]
            _agg[k]["cnt"] += 1
        for (cust, will), v in sorted(_agg.items()):
            avg_rate = v["rate_sum"] / v["cnt"] if v["cnt"] > 0 else 0
            print(f"  [{cust}] {will}: case={v['case']} avg_d1_rate={avg_rate:.2%}")

    print(f"  AI 跟进有效记录: {len(results)} 条")
    return results


def apply_ai_follow_correction(cases, ai_data):
    """
    在 Business Engine 之前修正 D0 案件量。

    对每种还款意愿: AI继续跟进 = case_count × (1 - d1_rate)，取整
    新客 / 老客分别汇总三种意愿的 AI 继续跟进数量，从 D0 中扣减。

    Args:
        cases: normalize_case() 的输出
        ai_data: fetch_ai_follow_cases() 的输出

    Returns:
        dict: {"NEW": {"quickbi_d0": int, "ai_follow": int, "manual_d0": int},
               "OLD": {...}}
    """
    # 汇总 AI 跟进数量（按客群）
    ai_follow = {"新客": 0, "老客": 0}
    for rec in ai_data:
        cust = rec["customer"]
        case_count = rec["case_count"]
        d1_rate = rec["d1_rate"]
        follow = round(case_count * (1 - d1_rate))
        ai_follow[cust] += follow

    correction = {}
    for cust_cn, label in [("新客", "NEW"), ("老客", "OLD")]:
        # 该客群所有 case 条目的 D0 合计
        d0_total = 0
        matching = []
        for c in cases:
            if c.get("customer") == cust_cn:
                d0 = c.get("stages", {}).get("D0", 0)
                d0_total += d0
                matching.append(c)

        ai_count = ai_follow[cust_cn]
        d0_corrected_total = max(d0_total - ai_count, 0)

        correction[label] = {
            "quickbi_d0": d0_total,
            "ai_follow": ai_count,
            "manual_d0": d0_corrected_total,
        }

        # 按比例分配到各 case 条目
        if d0_total > 0:
            ratio = d0_corrected_total / d0_total
            for c in matching:
                c["stages"]["D0"] = round(c["stages"]["D0"] * ratio)

    return correction


# ============================================================
#  8. 业务计算 — Business Engine V1
# ============================================================

# ------------------------------------------------------------------
# 注意：本模块只能读取 Case / Attendance / RULE，
#       不能直接读取 Quick BI JSON 或催收系统原始 JSON。
# ------------------------------------------------------------------


def _get_attendance(attendance_list, stage, customer):
    """
    从 Attendance 列表中查找指定 (stage, customer) 的汇总排班人数。

    Attendance 为汇总结构（非员工列表），直接返回 attendance 值。

    返回: int
    """
    if not attendance_list:
        return 0
    for a in attendance_list:
        a_stage = a.get("stage", "")
        a_cust = a.get("customer")
        if a_stage != stage:
            continue
        # customer 匹配: None 通配 "ALL"，其余精确匹配
        if customer is None:
            if a_cust is not None:
                continue
        else:
            if a_cust != customer:
                continue
        return a.get("attendance", 0)
    return 0


# -- 客群名称映射（Case 中文名 → RULE 英文 key） --
_CUST_KEY = {"新客": "NEW", "老客": "OLD"}


def _resolve_target(rule, stage, customer):
    """
    从 RULE.TARGET 解析持单标准。

    查找顺序: "{STAGE}_{CUST_KEY}" → "{STAGE}" → None（不参与预警）
    示例: stage="D0", customer="新客" → 查找 "D0_NEW" → 40
    """
    target_map = rule.get("TARGET", {}) if rule else {}
    # 精确匹配: customer 非 None 时拼接 "{STAGE}_{CUST_KEY}"
    if customer:
        cust_key = _CUST_KEY.get(customer, customer)
        key = f"{stage}_{cust_key}"
        if key in target_map:
            return target_map[key]
    # 仅按 stage 匹配: 如 "S1"
    if stage in target_map:
        return target_map[stage]
    # 未配置 target → 不参与预警
    return None


def compute_forecast(cases, attendance, rule=None):
    """
    Business Engine V1 — 核心计算引擎。

    输入 Case + Attendance + RULE，输出 Forecast + Warning。

    Args:
        cases:      normalize_case() 的输出   (list[dict])
        attendance: normalize_attendance() 的输出 (list[dict] | None)
        rule:       RULE 配置字典，默认使用全局 RULE

    Returns:
        (forecasts, warnings)
        forecasts: list[dict] — Forecast 对象列表（含 GREEN/YELLOW/RED）
        warnings:  list[dict] — Warning 对象列表（仅 YELLOW + RED）
    """
    if rule is None:
        rule = RULE

    if not cases:
        print("  [Business Engine] 无 Case 数据，跳过")
        return [], []

    warning_green_threshold = rule.get("WARNING_GREEN_THRESHOLD", 0.9)

    # ================================================================
    #  Step 1: 汇总 (stage, customer) → 案件量
    #   S1/S2/S3 合并新客+老客（customer = None）
    # ================================================================
    # 标记哪些 stage 需要合并客群
    MERGE_STAGES = {"S1", "S2", "S3"}

    stage_cust_cases = {}  # {(stage, customer|None): total_cases}
    for c in cases:
        cust = c.get("customer", "")
        stages = c.get("stages", {})
        for stage_key in STAGE_LIST:
            case_count = stages.get(stage_key, 0)
            if case_count <= 0:
                continue
            if stage_key in MERGE_STAGES:
                key = (stage_key, None)
            else:
                key = (stage_key, cust)
            stage_cust_cases[key] = stage_cust_cases.get(key, 0) + case_count

    # ================================================================
    #  Step 2: 聚合 Attendance → 按 (stage, customer) 汇总 teams
    # ================================================================
    forecasts = []
    warnings_list = []

    for (stage, cust), forecast_case in sorted(stage_cust_cases.items()):

        # 业务规则：仅保留 D0/D1/S1/S2
        if stage in ("D-1", "S3"):
            continue

        # Step 3: 解析该组合的持单标准
        target_load = _resolve_target(rule, stage, cust)

        # Step 2(续): 按 (stage, customer) 聚合所有机构的排班人数
        teams = []
        total_attendance = 0
        if attendance:
            for a in attendance:
                a_stage = a.get("stage", "")
                a_cust = a.get("customer")
                if a_stage != stage:
                    continue
                # customer 匹配: None 通配，其余精确匹配
                if cust is None:
                    if a_cust is not None:
                        continue
                else:
                    if a_cust != cust:
                        continue
                count = a.get("attendance", 0)
                total_attendance += count
                dept_name = a.get("deptName", "")
                teams.append({"team": dept_name, "attendance": count})

        # 固定机构顺序: PL → FI → CX
        teams.sort(key=lambda t: DEPT_ORDER.get(t.get("team", ""), 99))

        att_count = total_attendance

        # 计算 forecast_load
        if att_count > 0:
            forecast_load = round(forecast_case / att_count, 1)
        else:
            forecast_load = None

        # target_load 为 None → 该阶段不参与预警
        if target_load is None:
            need_staff_total = 0
            need_add_staff = 0
            suggestion = "暂不预警"
            warning_level = "GREEN"
            exceed_amount = "-"
        else:
            # Step 4: 计算 need_staff_total
            if forecast_case > 0 and target_load > 0:
                need_staff_total = math.ceil(forecast_case / target_load)
            else:
                need_staff_total = 0

            # Step 5: 计算 need_add_staff
            need_add_staff = max(need_staff_total - att_count, 0)

            # Step 6: 计算 warning_level（V2 规则）
            #   GREEN: forecast_load <= target_load × threshold
            #   YELLOW: target_load × threshold < forecast_load <= target_load
            #   RED: forecast_load > target_load
            if att_count == 0:
                warning_level = "RED"
                exceed_amount = "-"
            elif forecast_load is None:
                warning_level = "RED"
                exceed_amount = "-"
            elif forecast_load <= target_load * warning_green_threshold:
                warning_level = "GREEN"
                exceed_amount = "-"
            elif forecast_load <= target_load:
                warning_level = "YELLOW"
                exceed_amount = "-"
            else:
                warning_level = "RED"
                exceed_amount = round(forecast_load - target_load, 1)

            # Step 5(续): 生成 suggestion
            if att_count == 0:
                suggestion = f"需{need_staff_total}人"
            elif need_add_staff > 0:
                suggestion = f"+{need_add_staff}人"
            else:
                suggestion = "充足"

        # Step 7: 组装 Forecast 对象
        forecast = {
            "stage": stage,
            "customer": cust,
            "order_type": "ALL",
            "teams": teams,
            "forecast_case": forecast_case,
            "attendance": att_count,
            "forecast_load": forecast_load,
            "target_load": target_load,
            "need_staff_total": need_staff_total,
            "need_add_staff": need_add_staff,
            "suggestion": suggestion,
            "warning_level": warning_level,
            "exceed_amount": exceed_amount,
        }
        forecasts.append(forecast)

        # Warning 仅保留 YELLOW + RED
        if warning_level in ("RED", "YELLOW"):
            warnings_list.append({
                "stage": stage,
                "customer": cust,
                "order_type": "ALL",
                "teams": teams,
                "warning_level": warning_level,
                "forecast_load": forecast_load,
                "target_load": target_load,
                "attendance": att_count,
                "suggestion": suggestion,
                "exceed_amount": exceed_amount,
            })

    n_red = sum(1 for w in warnings_list if w["warning_level"] == "RED")
    n_yellow = sum(1 for w in warnings_list if w["warning_level"] == "YELLOW")
    print(f"  [Business Engine] Forecast: {len(forecasts)} 条, "
          f"Warning: RED={n_red} YELLOW={n_yellow}")

    return forecasts, warnings_list


# ================================================================
#  Report Builder — 程序唯一数据出口
# ================================================================

def build_report(forecasts, warnings, forecast_date_mx=None, region="MX"):
    """
    组装统一 report 对象。

    图片模块、飞书模块、日志模块只能读取此 report，
    不得直接访问 Forecast / Warning / Quick BI / 催收系统。

    Args:
        forecasts:         compute_forecast() 的输出 (list[dict])
        warnings:          compute_forecast() 的输出 (list[dict])
        forecast_date_mx:  墨西哥业务日期 (str "YYYY-MM-DD")，默认当天墨西哥时间
        region:            地区代码，默认 "MX"

    Returns:
        dict: 统一 report 对象
    """
    # -- meta --
    cn_tz = timezone(timedelta(hours=8))
    run_time_cn = datetime.now(cn_tz).strftime("%Y-%m-%d %H:%M:%S")

    if forecast_date_mx is None:
        mx_tz = timezone(timedelta(hours=-6))
        forecast_date_mx = datetime.now(mx_tz).strftime("%Y-%m-%d")

    # -- 统计（只统计，不计算） --
    green_count = sum(1 for f in forecasts if f.get("warning_level") == "GREEN")
    yellow_count = sum(1 for f in forecasts if f.get("warning_level") == "YELLOW")
    red_count = sum(1 for f in forecasts if f.get("warning_level") == "RED")

    report = {
        "meta": {
            "run_time_cn": run_time_cn,
            "forecast_date_mx": forecast_date_mx,
            "region": region,
            "version": "V2",
        },
        "summary": {
            "forecast_count": len(forecasts),
            "warning_count": len(warnings),
            "green_count": green_count,
            "yellow_count": yellow_count,
            "red_count": red_count,
        },
        "forecast": forecasts,
        "warning": warnings,
    }

    print(f"  [Report Builder] report 组装完成 "
          f"(forecast={len(forecasts)}, warning={len(warnings)}, "
          f"G={green_count} Y={yellow_count} R={red_count})")

    return report


# ------------------------------------------------------------------
#  临时兼容函数（后续由完整的 compute_* 系列替换）
# ------------------------------------------------------------------

def _build_report(entry_data_list):
    """
    [临时] 将 Quick BI 入口数据整理为报告格式。

    后续业务计算开发完成后，此函数将被完整的 compute_* 系列函数替换。
    当前仅做数据透传 + 汇总，不包含任何业务逻辑。
    """
    ref_dates = [e["ref_date"] for e in entry_data_list if e["ref_date"]]
    ref_date = min(ref_dates) if ref_dates else None

    all_dates = []
    for e in entry_data_list:
        for c in e["cohorts"]:
            all_dates.append(c["date"])
    source_date = max(all_dates) if all_dates else None

    entries = []
    totals = {"D-1": 0, "D0": 0, "D1": 0, "S1": 0, "S2": 0, "S3": 0}
    for e in entry_data_list:
        s = e["summary"]
        entries.append({
            "name": e["name"],
            "D-1": s["D-1"], "D0": s["D0"],
            "D1": s["D1"], "S1": s["S1"],
            "S2": s["S2"], "S3": s["S3"],
        })
        for k in totals:
            totals[k] += s[k]

    return {
        "ref_date": ref_date,
        "source_date": source_date,
        "entries": entries,
        "totals": totals,
        # --- 以下字段由后续业务计算填充 ---
        "summary_stages": [],
        "hr": {},
        "schedule": [],
        "schedule_dates": [],
        "total": {},
    }


# ============================================================
#  9. 图片生成
# ============================================================

def _forecast_to_case_table(forecast):
    """
    从 forecast 数据重建表1行（入口 + 各阶段单数）。

    D-1/D0/D1 按客群分组，S1/S2/S3 合并为全客群。
    返回: (rows, stage_totals)
      rows:  [["新客", D-1, D0, D1, S1, S2], ...]
      stage_totals: {"D-1": N, "D0": N, ...}
    """
    stage_cols = ["D-1", "D0", "D1", "S1", "S2"]
    STAGE_CUST = {"S1", "S2", "S3"}  # 不区分客群的阶段

    # {(stage, customer): case_count}
    stage_cust_cases = {}
    for f in forecast:
        stage = f["stage"]
        cust = f["customer"]
        key = (stage, cust)
        if key not in stage_cust_cases:
            stage_cust_cases[key] = f["forecast_case"]

    # 构建行: 新客、老客、全客群(S1/S2)
    rows = []
    for cust_label, cust_val in [("新客", "新客"), ("老客", "老客")]:
        cells = [cust_label]
        for s in stage_cols:
            cells.append(stage_cust_cases.get((s, cust_val), 0))
        rows.append(cells)

    # S 阶段单独一行
    cells = ["全客群"]
    for s in stage_cols:
        cells.append(stage_cust_cases.get((s, None), 0))
    rows.append(cells)

    # 各阶段合计
    stage_totals = {}
    for s in stage_cols:
        stage_totals[s] = sum(
            stage_cust_cases.get((s, c), 0)
            for c in ("新客", "老客", None)
        )

    return rows, stage_totals


def draw_table_image(report):
    """
    绘制日报图片 — 1448px Excel 驾驶舱风格，12 列含 AI 接管案件。

    布局（从上到下）：
      ① 顶栏：墨西哥每日案件及人力预估日报 · 日期 · 更新时间
      ② 金色大标题：喜 象 盘
      ③ 表头：金黄色背景 / 黑色边框 / AI接管案件蓝色
      ④ 数据行：白底黑边 / 前 6 列合并单元格 / AI接管案件蓝字
      ⑤ 预警说明（左） + 数据来源（右）
      ⑥ 今日总览：横向四卡片（预计到期 → AI接管 → 人工催收 → 排班）
    """
    from PIL import Image, ImageDraw, ImageFont

    # ================================================================
    #  1. 数据提取
    # ================================================================
    meta = report.get("meta", {})
    forecast = report.get("forecast", [])
    target_date = meta.get("business_day", meta.get("forecast_date_mx",
                          date.today().strftime("%Y-%m-%d")))
    run_time_cn = meta.get("run_time_cn", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    # 表格内日期用中文格式 "7月3日"
    try:
        _parts = target_date.split("-")
        table_date = f"{int(_parts[1])}月{int(_parts[2])}日"
    except (ValueError, IndexError):
        table_date = target_date

    DISPLAY_STAGES = ["D0", "D1", "S1", "S2"]
    hr_rows = [f for f in forecast if f.get("stage") in DISPLAY_STAGES]

    stage_order = {s: i for i, s in enumerate(DISPLAY_STAGES)}
    hr_rows.sort(key=lambda f: (
        stage_order.get(f.get("stage", ""), 99),
        f.get("customer") or "",
        f.get("order_type") or "",
    ))

    # ---- 展平（仅人数 > 0 的机构） ----
    # AI 扣减查找表
    ai_corr_lookup = report.get("ai_correction", {})
    ai_deduct_map = {
        ("D0", "新客"): ai_corr_lookup.get("NEW", {}).get("ai_follow", 0),
        ("D0", "老客"): ai_corr_lookup.get("OLD", {}).get("ai_follow", 0),
    }
    table_rows = []
    for f in hr_rows:
        stage = f.get("stage", "")
        customer = f.get("customer") or ""
        order_type = f.get("order_type") or ""
        forecast_case = f.get("forecast_case", 0)
        target_load = f.get("target_load")
        target_str = str(target_load) if target_load is not None else "-"
        overall_load = f.get("forecast_load")
        overall_warning = f.get("warning_level", "GREEN")
        overall_exceed = f.get("exceed_amount", "-")
        teams = f.get("teams", [])

        # AI 扣减（仅 D0 有值）
        _ai_ded = ai_deduct_map.get((stage, customer), 0)
        ai_deduct_str = str(_ai_ded) if _ai_ded > 0 else ""

        if teams:
            for t in teams:
                dept_att = t.get("attendance", 0)
                if dept_att <= 0:
                    continue
                load_str = str(int(round(overall_load))) if overall_load is not None else "-"
                exc = str(overall_exceed) if overall_exceed != "-" else "-"
                table_rows.append({
                    "date": table_date,
                    "stage": stage,
                    "customer": customer if customer else "-",
                    "order_type": order_type if order_type else "-",
                    "forecast_case": forecast_case,
                    "ai_deduct": ai_deduct_str,
                    "dept_name": t.get("team", ""),
                    "dept_att": dept_att,
                    "load_str": load_str,
                    "target_str": target_str,
                    "warning": overall_warning,
                    "exceed_str": exc,
                })

    # ---- 合并分组：连续相同 (stage, customer) 的行 ----
    MERGE_COLS = 6
    merge_groups = []
    if table_rows:
        i = 0
        while i < len(table_rows):
            r = table_rows[i]
            key = (r["stage"], r["customer"])
            n = 1
            while i + n < len(table_rows):
                if (table_rows[i + n]["stage"], table_rows[i + n]["customer"]) == key:
                    n += 1
                else:
                    break
            merge_groups.append((i, n))
            i += n

    # ================================================================
    #  2. 字体（1400px 画布专用）
    # ================================================================
    FONT_PATHS = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    _ftop = _fgold = _fhdr = _fbody = _fsmall = _fsum = _fxsmall = None
    for _fp in FONT_PATHS:
        try:
            _ftop   = ImageFont.truetype(_fp, 26)   # 顶栏
            _fgold  = ImageFont.truetype(_fp, 50)   # 喜象盘（+5%）
            _fhdr   = ImageFont.truetype(_fp, 22)   # 表头
            _fbody  = ImageFont.truetype(_fp, 20)   # 数据
            _fsmall = ImageFont.truetype(_fp, 17)   # 说明文字
            _fsum   = ImageFont.truetype(_fp, 22)   # 今日总览
            _fxsmall = ImageFont.truetype(_fp, 14)  # 数据说明（弱化）
            break
        except Exception:
            continue
    if _fbody is None:
        _ftop = _fgold = _fhdr = _fbody = _fsmall = _fsum = _fxsmall = ImageFont.load_default()

    # 加粗（表头用）
    _fbold = None
    for _fp in FONT_PATHS:
        for _suffix in ("bd.ttc", "bd.ttf", "b.ttf", "B.ttf"):
            try:
                _fbold = ImageFont.truetype(
                    _fp.replace(".ttc", _suffix).replace(".ttf", _suffix), 22)
                break
            except Exception:
                continue
        if _fbold:
            break
    if not _fbold:
        _fbold = _fhdr

    # 今日总览大数字（38px）
    _fbig = None
    for _fp in FONT_PATHS:
        try:
            _fbig = ImageFont.truetype(_fp, 38)
            break
        except Exception:
            continue
    if not _fbig:
        _fbig = _fsum

    # ================================================================
    #  3. 颜色
    # ================================================================
    C_TOP_BG      = (21, 32, 50)     # 顶栏深蓝
    C_TOP_TEXT    = (255, 255, 255)   # 顶栏白字
    C_GOLD        = (180, 135, 10)    # 喜象盘金色
    C_HDR_BG      = (244, 196, 48)    # 表头金色 #F4C430
    C_HDR_TEXT    = (0, 0, 0)         # 表头黑字
    C_BODY_BG     = (255, 255, 255)   # 白底
    C_BODY_TEXT   = (0, 0, 0)         # 黑字
    C_GRID        = (26, 26, 26)      # 边框（接近纯黑）
    C_DOT_GREEN   = (0, 176, 80)      # 绿色预警
    C_DOT_YELLOW  = (255, 170, 0)     # 黄色预警
    C_DOT_RED     = (220, 38, 38)     # 红色预警
    C_BOTTOM_BG   = (246, 247, 249)   # 底部浅灰
    C_SUMMARY_BG  = (235, 239, 247)   # 今日总览浅蓝灰
    C_SUBTEXT     = (140, 140, 140)   # 次要文字
    C_SEP         = (200, 205, 212)   # 区块分隔线
    C_HL_YELLOW   = (255, 248, 230)   # 预警黄行高亮 #FFF8E6
    C_HL_RED      = (255, 236, 236)   # 预警红行高亮 #FFECEC
    C_AI_BLUE     = (59, 130, 246)    # AI 接管蓝色 #3B82F6

    # ================================================================
    #  4. 尺寸（1448px 画布，12 列含 AI 接管）
    # ================================================================
    PAD = 24
    # 12 列（总和 1400）：催单+20 AI接管+25 机构-20 人数-10 日期-5 人均-5 标准-5
    COL_W = [129, 75, 75, 105, 172, 145, 58, 63, 147, 162, 100, 169]
    TABLE_W = sum(COL_W)            # 1400
    TOTAL_W = PAD * 2 + TABLE_W     # 1448

    ROW_H     = 54   # 数据行高
    HDR_H     = 64   # 表头高
    TOP_H     = 78   # 顶栏高
    GOLD_H    = 100  # 喜象盘高
    LEGEND_H  = 170  # 预警说明 + 数据来源（紧凑布局）
    SUMMARY_H = 190  # 今日总览（横向四卡片，紧凑布局）
    GAP       = 10   # 区块间距
    TOP_MGN   = 20   # 顶部留白

    # 列边界
    COL_X = [PAD]
    for cw in COL_W:
        COL_X.append(COL_X[-1] + cw)

    N_ROWS = len(table_rows)
    TABLE_BODY_H = ROW_H * max(N_ROWS, 1)
    IMG_H = TOP_MGN + TOP_H + GOLD_H + HDR_H + TABLE_BODY_H + LEGEND_H + SUMMARY_H + 32

    # ================================================================
    #  5. 创建画布 & 辅助函数
    # ================================================================
    img = Image.new("RGB", (TOTAL_W, IMG_H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    def _ctext(x, y, w, h, txt, font, color):
        bbox = draw.textbbox((0, 0), str(txt), font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((x + (w - tw) // 2, y + (h - th) // 2), str(txt), fill=color, font=font)

    def _dot(cx, cy, r, color):
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    def _fill(x, y, w, h, color):
        draw.rectangle([x, y, x + w, y + h], fill=color)

    def _hline(x1, x2, y, color=C_GRID, w=1):
        draw.line([(x1, y), (x2, y)], fill=color, width=w)

    def _vline(x, y1, y2, color=C_GRID, w=1):
        draw.line([(x, y1), (x, y2)], fill=color, width=w)

    def _box(x, y, w, h, color=C_GRID, width=2):
        """画矩形边框"""
        draw.rectangle([x, y, x + w, y + h], outline=color, width=width)

    # ---- 关键 x 坐标 ----
    MERGE_R = COL_X[MERGE_COLS]       # 合并区域右边界
    TABLE_R = COL_X[-1]               # 表格右边界
    DEPT_L = MERGE_R                  # 机构区域左边界

    y = TOP_MGN

    # ================================================================
    #  ① 顶栏
    # ================================================================
    _fill(0, y, TOTAL_W, TOP_H, C_TOP_BG)
    top_line = (
        f"墨西哥每日案件及人力预估日报    "
        f"{target_date}    "
        f"更新时间：{run_time_cn}"
    )
    _ctext(PAD, y, TOTAL_W - PAD * 2, TOP_H, top_line, _ftop, C_TOP_TEXT)
    y += TOP_H

    # ================================================================
    #  ② 金色大标题：喜 象 盘
    # ================================================================
    _fill(0, y, TOTAL_W, GOLD_H, (255, 255, 255))
    _ctext(PAD, y, TOTAL_W - PAD * 2, GOLD_H, "喜  象  盘", _fgold, C_GOLD)
    y += GOLD_H

    # ================================================================
    #  ③④ 表头
    # ================================================================
    HEADERS = [
        "日期", "阶段", "客群", "订单类型", "人工催单数", "AI接管案件",
        "机构", "人数", "人均持单", "持单标准", "预警", "超标数量",
    ]
    TABLE_TOP = y

    # 表头背景 + 外框
    _fill(PAD, y, TABLE_W, HDR_H, C_HDR_BG)
    _box(PAD, y, TABLE_W, HDR_H, C_GRID, width=2)
    for j, (hdr, cw) in enumerate(zip(HEADERS, COL_W)):
        _ctext(COL_X[j], y, cw, HDR_H, hdr, _fbold, C_HDR_TEXT)
        if j > 0:
            _vline(COL_X[j], y, y + HDR_H, C_GRID, w=2)
    # 表头底边加粗
    _hline(PAD, TABLE_R, y + HDR_H, C_GRID, w=2)
    y += HDR_H

    # ================================================================
    #  ⑤⑥⑦⑧ 数据行 — 真正合并单元格
    # ================================================================
    if table_rows:
        row_y = y

        for g_idx, (group_start, group_count) in enumerate(merge_groups):
            merge_top = row_y
            merge_h = ROW_H * group_count
            first = table_rows[group_start]

            # --- 合并区域（列 0-5）：统一背景 + 文字 ---
            # 预警行高亮：YELLOW→浅黄 / RED→浅红 / GREEN→白底
            _wl = first.get("warning", "GREEN")
            if _wl == "YELLOW":
                _merge_bg = C_HL_YELLOW
            elif _wl == "RED":
                _merge_bg = C_HL_RED
            else:
                _merge_bg = C_BODY_BG
            _fill(PAD, merge_top, MERGE_R - PAD, merge_h, _merge_bg)
            merge_texts = [
                first["date"], first["stage"], first["customer"],
                first["order_type"], str(first["forecast_case"]),
                first.get("ai_deduct", ""),
            ]
            for j, (txt, cw) in enumerate(zip(merge_texts, COL_W[:MERGE_COLS])):
                if j == 5:  # AI接管案件 — 蓝色
                    _ctext(COL_X[j], merge_top, cw, merge_h, txt, _fbody, C_AI_BLUE)
                else:
                    _ctext(COL_X[j], merge_top, cw, merge_h, txt, _fbody, C_BODY_TEXT)

            # --- 逐行绘制机构区域（列 6-11） ---
            for offset in range(group_count):
                row = table_rows[group_start + offset]
                cy = row_y

                # 预警行高亮背景
                _rwl = row.get("warning", "GREEN")
                if _rwl == "YELLOW":
                    _row_bg = C_HL_YELLOW
                elif _rwl == "RED":
                    _row_bg = C_HL_RED
                else:
                    _row_bg = C_BODY_BG
                _fill(DEPT_L, cy, TABLE_R - DEPT_L, ROW_H, _row_bg)

                for j in range(MERGE_COLS, 12):
                    cx, cw = COL_X[j], COL_W[j]
                    rel = j - MERGE_COLS

                    if rel == 0:    # 机构
                        txt = row["dept_name"]
                        _ctext(cx, cy, cw, ROW_H, txt, _fbody, C_BODY_TEXT)
                    elif rel == 1:  # 人数
                        _ctext(cx, cy, cw, ROW_H, str(row["dept_att"]), _fbody, C_BODY_TEXT)
                    elif rel == 2:  # 单账号持单量
                        _ctext(cx, cy, cw, ROW_H, row["load_str"], _fbody, C_BODY_TEXT)
                    elif rel == 3:  # 持单标准
                        _ctext(cx, cy, cw, ROW_H, row["target_str"], _fbody, C_BODY_TEXT)
                    elif rel == 4:  # 预警 — 彩色圆点（放大 20%）
                        wl = row["warning"]
                        if wl == "GREEN":
                            _dot(cx + cw // 2, cy + ROW_H // 2, 12, C_DOT_GREEN)
                        elif wl == "YELLOW":
                            _dot(cx + cw // 2, cy + ROW_H // 2, 12, C_DOT_YELLOW)
                        elif wl == "RED":
                            _dot(cx + cw // 2, cy + ROW_H // 2, 12, C_DOT_RED)
                        else:
                            _ctext(cx, cy, cw, ROW_H, "-", _fbody, C_SUBTEXT)
                    elif rel == 5:  # 超标数量
                        _ctext(cx, cy, cw, ROW_H, row["exceed_str"], _fbody, C_BODY_TEXT)

                row_y += ROW_H

            # ---- 合并区域边框（不画内部横线） ----
            _box(PAD, merge_top, MERGE_R - PAD, merge_h, C_GRID, width=2)
            # 内竖线
            for j in range(1, MERGE_COLS):
                _vline(COL_X[j], merge_top, merge_top + merge_h, C_GRID, w=2)

            # ---- 机构区域边框 ----
            # 左边界（与合并区共享的竖线）
            _vline(DEPT_L, merge_top, merge_top + merge_h, C_GRID, w=2)
            # 右边界
            _vline(TABLE_R, merge_top, merge_top + merge_h, C_GRID, w=2)
            # 底边
            _hline(DEPT_L, TABLE_R, merge_top + merge_h, C_GRID, w=2)

            for offset in range(group_count):
                cy = merge_top + offset * ROW_H
                # 顶边（第一行与合并区共享，不重复画）
                if offset > 0:
                    _hline(DEPT_L, TABLE_R, cy, C_GRID, w=1)
                # 机构区域内竖线
                for j in range(MERGE_COLS + 1, 12):
                    _vline(COL_X[j], cy, cy + ROW_H, C_GRID, w=1)
                # 行底边
                _hline(DEPT_L, TABLE_R, cy + ROW_H, C_GRID, w=1)

        # 表格外框补全
        # 顶边（已在表头底边）
        # 左边（全高）
        _vline(PAD, TABLE_TOP, row_y, C_GRID, w=2)
        # 右边（全高）
        _vline(TABLE_R, TABLE_TOP, row_y, C_GRID, w=2)

        y = row_y
    else:
        _fill(PAD, y, TABLE_W, ROW_H, C_BODY_BG)
        _box(PAD, y, TABLE_W, ROW_H, C_GRID, width=2)
        _ctext(PAD, y, TABLE_W, ROW_H, "暂无预估数据", _fsmall, C_SUBTEXT)
        y += ROW_H

    y += GAP

    # ================================================================
    #  ⑭ 底部：预警说明（左 50%） + 数据来源（右 50%）
    # ================================================================
    _fill(0, y, TOTAL_W, LEGEND_H, C_BOTTOM_BG)
    _hline(0, TOTAL_W, y, C_SEP, w=1)

    mid_x = TOTAL_W // 2  # 724 — 左右分界
    half_w = mid_x - PAD  # 700 — 每侧可用宽度

    # -- 左侧 50%：预警说明（居中） --
    _ctext(PAD, y + 6, half_w, 28, "预警说明", _fbold, C_BODY_TEXT)
    warn_items = [
        (C_DOT_GREEN,  "正常"),
        (C_DOT_YELLOW, "接近标准"),
        (C_DOT_RED,    "超过标准"),
    ]
    for idx, (dc, label) in enumerate(warn_items):
        line_y = y + 40 + idx * 28
        # 计算 dot + text 总宽度，在左侧区域内居中
        tbbox = draw.textbbox((0, 0), label, font=_fbody)
        tw = tbbox[2] - tbbox[0]
        block_w = 22 + 10 + tw  # dot直径 + 间距 + 文字宽
        block_x = PAD + (half_w - block_w) // 2
        _dot(block_x + 11, line_y + 8, 11, dc)
        draw.text((block_x + 28, line_y), label, fill=C_BODY_TEXT, font=_fbody)

    # -- 右侧 50%：数据来源（每行居中） --
    _ctext(mid_x, y + 6, half_w, 28, "数据来源", _fbold, C_BODY_TEXT)
    ds_lines = [
        "预计到期案件：Quick BI Forecast",
        "AI接管案件：AI持续跟进",
        "人工催收案件：预计到期案件 − AI接管案件",
        "排班账号：queryConfig",
        f"更新时间：auto {datetime.now().strftime('%m/%d %H:%M')}",
    ]
    for idx, line in enumerate(ds_lines):
        _ctext(mid_x, y + 40 + idx * 22, half_w, 22, line, _fsmall, C_BODY_TEXT)

    _hline(0, TOTAL_W, y + LEGEND_H, C_SEP, w=1)
    y += LEGEND_H

    # ================================================================
    #  ⑮ 今日总览（横向四卡片，驾驶舱风格）
    # ================================================================
    _fill(0, y, TOTAL_W, SUMMARY_H, C_SUMMARY_BG)

    # 统计
    total_cases = 0
    seen = set()
    for g_start, _ in merge_groups:
        r = table_rows[g_start]
        key = (r["stage"], r["customer"])
        if key not in seen:
            seen.add(key)
            total_cases += r["forecast_case"]

    staff_totals = report.get("staff_totals", {})
    dept_list_ordered = [dn for dn in ["PL", "FI", "CX"] if dn in staff_totals and staff_totals[dn] > 0]
    total_staff = sum(staff_totals.values())

    # AI 继续跟进总数
    ai_corr_img = report.get("ai_correction", {})
    ai_follow_img = sum(
        ai_corr_img.get(k, {}).get("ai_follow", 0) for k in ["NEW", "OLD"]
    )

    # Quick BI 原始总量 + AI 节省比例
    quickbi_total = total_cases + ai_follow_img
    ai_save_pct = round(ai_follow_img / quickbi_total * 100, 1) if quickbi_total > 0 else 0.0

    # 标题
    _ctext(PAD, y + 5, TOTAL_W - PAD * 2, 26, "今 日 总 览", _fsum, C_BODY_TEXT)

    # 四张卡片横向排列（等间距 18px）
    CARD_W = 336
    CARD_H = 134
    CARD_GAP = 18
    CARDS_TOP = y + 36
    card_x0 = PAD + (TABLE_W - CARD_W * 4 - CARD_GAP * 3) // 2

    C_CARD_TITLE = (102, 102, 102)  # 卡片标题 #666666

    dept_detail = "｜".join(
        [f"{dn} {staff_totals[dn]}" for dn in dept_list_ordered]
    ) if dept_list_ordered else "-"

    cards = [
        ("预计到期案件", str(quickbi_total), "", C_BODY_TEXT),
        ("AI接管案件", "", "", C_AI_BLUE),  # 双色手动绘制
        ("人工催收案件", str(total_cases), "", C_BODY_TEXT),
        ("排班账号", str(total_staff), dept_detail, C_BODY_TEXT),
    ]

    for i, (title, number, detail, num_color) in enumerate(cards):
        cx = card_x0 + i * (CARD_W + CARD_GAP)
        # 白底卡片 + 浅边框
        _fill(cx, CARDS_TOP, CARD_W, CARD_H, (255, 255, 255))
        _box(cx, CARDS_TOP, CARD_W, CARD_H, C_SEP, width=1)
        # 标题（统一 #666）
        _ctext(cx, CARDS_TOP + 10, CARD_W, 20, title, _fsmall, C_CARD_TITLE)
        # 数字
        if i == 1:  # AI接管 — 数字蓝色 + 百分比灰色
            num_str = str(ai_follow_img) if ai_follow_img > 0 else "0"
            pct_str = f"（{ai_save_pct}%）" if ai_follow_img > 0 else ""
            nbbox = draw.textbbox((0, 0), num_str, font=_fbig)
            pbbox = draw.textbbox((0, 0), pct_str, font=_fsmall) if pct_str else (0, 0, 0, 0)
            nw = nbbox[2] - nbbox[0]
            pw = pbbox[2] - pbbox[0] if pct_str else 0
            nh = nbbox[3] - nbbox[1]
            ph = pbbox[3] - pbbox[1] if pct_str else 0
            total_w = nw + pw
            sx = cx + (CARD_W - total_w) // 2
            num_area_top = CARDS_TOP + 34
            num_area_h = 56
            draw.text((sx, num_area_top + (num_area_h - nh) // 2),
                      num_str, fill=C_AI_BLUE, font=_fbig)
            if pct_str:
                draw.text((sx + nw, num_area_top + (num_area_h - ph) // 2),
                          pct_str, fill=C_SUBTEXT, font=_fsmall)
        else:
            _ctext(cx, CARDS_TOP + 34, CARD_W, 56, number, _fbig, num_color)
        # 底部明细（仅排班卡片）
        if detail:
            _ctext(cx, CARDS_TOP + 100, CARD_W, 18, detail, _fxsmall, C_SUBTEXT)

    _hline(0, TOTAL_W, y + SUMMARY_H, C_SEP, w=1)

    # ================================================================
    #  返回 PNG
    # ================================================================
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ============================================================
#  10. 飞书发送
# ============================================================

class FeishuClient:
    """飞书 API 客户端"""

    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expire = 0

    def _get_token(self):
        """获取 tenant_access_token（带缓存）"""
        if self._token and time.time() < self._token_expire:
            return self._token
        resp = requests.post(
            FEISHU_TOKEN_URL,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"飞书认证失败: {data.get('msg')}")
        self._token = data["tenant_access_token"]
        self._token_expire = time.time() + data.get("expire", 7200) - 300
        return self._token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _detect_id_type(receive_id):
        """根据 ID 前缀判断类型"""
        return "open_id" if receive_id.startswith("ou_") else "chat_id"

    def list_chats(self):
        """列出所有群聊"""
        all_chats = []
        page_token = None
        while True:
            params = {"page_size": 50}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(
                FEISHU_CHAT_LIST_URL,
                headers=self._headers(),
                params=params,
                timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"获取群列表失败: {data.get('msg')}")
            items = data.get("data", {}).get("items", [])
            all_chats.extend(items)
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token")
        return all_chats

    def upload_image(self, image_bytes):
        """上传图片到飞书，返回 image_key"""
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {self._get_token()}"},
            files={
                "image_type": (None, "message"),
                "image": ("table.png", image_bytes, "image/png"),
            },
            timeout=30,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"上传图片失败: {data.get('msg')}")
        return data["data"]["image_key"]

    def send_card(self, receive_id, card):
        """发送 interactive 卡片消息"""
        id_type = self._detect_id_type(receive_id)
        if isinstance(card, dict):
            card = json.dumps(card, ensure_ascii=False)
        payload = {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": card,
        }
        resp = requests.post(
            FEISHU_MSG_URL + f"?receive_id_type={id_type}",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"发送失败: {data.get('msg')} (code={data.get('code')})")
        return data

    def send_text(self, receive_id, text):
        """发送纯文本消息"""
        id_type = self._detect_id_type(receive_id)
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        resp = requests.post(
            FEISHU_MSG_URL + f"?receive_id_type={id_type}",
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"发送失败: {data.get('msg')} (code={data.get('code')})")
        return data


def build_card(report, feishu_client):
    """
    构建含图片的飞书卡片消息。

    流程：Pillow 生成图片 → 上传飞书 → 嵌入卡片。
    """
    sd = report.get("source_date", "")
    target_date = (
        f"{sd[:4]}-{sd[4:6]}-{sd[6:8]}"
        if sd and len(sd) == 8
        else date.today().strftime("%Y-%m-%d")
    )

    img_bytes = draw_table_image(report)
    image_key = feishu_client.upload_image(img_bytes)

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {
                "tag": "plain_text",
                "content": f"墨西哥分案预估 - {target_date}",
            },
        },
        "elements": [
            {
                "tag": "img",
                "img_key": image_key,
                "alt": {"tag": "plain_text", "content": "分案预估表"},
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": f"auto {datetime.now().strftime('%m/%d %H:%M')}",
                    }
                ],
            },
        ],
    }


def build_text(report):
    """纯文本降级消息（不含图片）"""
    meta = report.get("meta", {})
    forecast = report.get("forecast", [])
    target_date = meta.get("forecast_date_mx", date.today().strftime("%Y-%m-%d"))

    DISPLAY_STAGES = ["D0", "D1", "S1", "S2"]
    hr_rows = [f for f in forecast if f.get("stage") in DISPLAY_STAGES]

    lines = [f"墨西哥分案预估 - {target_date}", ""]

    # 明细（仅显示有出勤的机构）
    for f in hr_rows:
        stage = f.get("stage", "")
        cust = f.get("customer") or "-"
        otype = f.get("order_type") or "-"
        fcase = f.get("forecast_case", 0)
        load_val = f.get("forecast_load")
        load_str = str(int(round(load_val))) if load_val is not None else "-"
        target_val = f.get("target_load")
        target_str = str(target_val) if target_val is not None else "-"
        warning = f.get("warning_level", "GREEN")
        exceed = f.get("exceed_amount", "-")
        exceed_str = str(exceed) if exceed != "-" else "-"
        teams_list = f.get("teams", [])

        if teams_list:
            has_any = False
            for t in teams_list:
                dept_att = t.get("attendance", 0)
                if dept_att <= 0:
                    continue
                has_any = True
                lines.append(
                    f"{stage:<4} {cust:<4} {otype:<4} "
                    f"单数={fcase:>5}  机构={t.get('team',''):<4} 人数={dept_att:>2}  "
                    f"持单={load_str:>5}  标准={target_str:>3}  "
                    f"{warning:<6}  超标={exceed_str:>4}"
                )
            if not has_any:
                pass  # 所有机构人数为 0，不显示
        else:
            lines.append(
                f"{stage:<4} {cust:<4} {otype:<4} "
                f"单数={fcase:>5}  账号=0  "
                f"持单={load_str:>5}  标准={target_str:>3}  "
                f"{warning:<6}  超标={exceed_str:>4}"
            )

    # 今日总览（仅统计有出勤的机构）
    total_cases = sum(f.get("forecast_case", 0) for f in hr_rows)
    # 排班账号：直接从 queryConfig 原始 attendance 累加
    staff_totals = report.get("staff_totals", {})
    dept_parts = []
    for dn in ["PL", "FI", "CX"]:
        if dn in staff_totals and staff_totals[dn] > 0:
            dept_parts.append(f"{dn}：{staff_totals[dn]}")
    total_staff = sum(staff_totals.values())
    staff_line = f"{total_staff}（{'｜'.join(dept_parts)}）" if dept_parts else str(total_staff)

    # AI 继续跟进
    ai_corr_text = report.get("ai_correction", {})
    ai_follow_text = sum(
        ai_corr_text.get(k, {}).get("ai_follow", 0) for k in ["NEW", "OLD"]
    )
    quickbi_total = total_cases + ai_follow_text
    ai_save_pct = round(ai_follow_text / quickbi_total * 100, 1) if quickbi_total > 0 else 0.0

    lines.append("")
    lines.append("今日总览")
    lines.append(f"Quick BI预计案件：{quickbi_total} 单")
    if ai_follow_text > 0:
        lines.append(f"AI继续跟进：{ai_follow_text}（{ai_save_pct}%）")
    lines.append(f"人工预计案件：{total_cases} 单")
    lines.append(f"排班账号：{staff_line}")

    return "\n".join(lines)


# ============================================================
#  11. main()
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="墨西哥每日案件及人力预估 V2")
    parser.add_argument("--dry-run", action="store_true", help="仅计算不发送")
    parser.add_argument("--text-only", action="store_true", help="纯文本降级")
    parser.add_argument("--list-chats", action="store_true", help="列出可用群聊")
    parser.add_argument("--chat-id", type=str, default=None, help="指定接收者")
    parser.add_argument("--manual-staff", action="store_true",
                        help="临时模式：使用手动排班人数，不访问内网催收系统")
    args = parser.parse_args()

    print("=" * 50)
    print("  墨西哥每日案件及人力预估 V2")
    print("=" * 50)

    # ---- Date Debug ----
    cn_tz = timezone(timedelta(hours=8))
    mx_tz = timezone(timedelta(hours=-6))

    today = date.today()
    business_day = today
    forecast_date_mx = datetime.now(mx_tz).strftime("%Y-%m-%d")
    run_time_cn = datetime.now(cn_tz).strftime("%Y-%m-%d %H:%M:%S")

    quickbi_due_date = (today - timedelta(days=17)).strftime("%Y%m%d")
    ai_call_day = (today - timedelta(days=1)).strftime("%Y%m%d")

    # 图片标题与表格日期统一使用 business_day
    biz_str = str(business_day)
    try:
        _parts = biz_str.split("-")
        table_date_biz = f"{int(_parts[1])}月{int(_parts[2])}日"
    except (ValueError, IndexError):
        table_date_biz = biz_str

    print()
    print("=" * 30)
    print("Date Debug")
    print("=" * 30)
    print(f"today               : {today}")
    print(f"business_day        : {biz_str}")
    print(f"forecast_date (MX)  : {forecast_date_mx}")
    print()
    print(f"Quick BI due_date   : {quickbi_due_date}")
    print(f"AI Follow call_day  : {ai_call_day}")
    print()
    print(f"图片标题日期         : {biz_str}")
    print(f"表格日期             : {table_date_biz}")
    print(f"更新时间             : {run_time_cn}")
    print("-" * 30)
    print("确认：business_day = 图片标题 = 表格日期  ✓")
    print("=" * 30)
    print()

    # ---- Step 1: 获取 Quick BI 数据 ----
    entry_data_list = fetch_quickbi_entries()

    if not entry_data_list:
        print("[!] 无入口数据，退出")
        sys.exit(1)

    # ---- Step 2: 获取排班人力 ----
    if args.manual_staff:
        # 临时模式：手动排班，不访问内网催收系统
        attendance = build_manual_attendance()
    else:
        dept_list = fetch_organ_page()
        staff_data = get_query_config(dept_list)
        attendance = normalize_attendance(staff_data)

    # ---- Step 3: 数据标准化 ----
    cases = normalize_case(entry_data_list)

    # ---- Step 3.5: AI 持续跟进修正（D0 扣减） ----
    ai_data = fetch_ai_follow_cases()
    ai_correction = apply_ai_follow_correction(cases, ai_data)

    # ---- Step 4: 业务计算 ----
    forecasts, warnings_list = compute_forecast(cases, attendance)

    # 注入 forecast_case_raw（Quick BI 原始值，AI 修正前）
    for f in forecasts:
        f["forecast_case_raw"] = f["forecast_case"]
    for cust_cn, cust_en in [("新客", "NEW"), ("老客", "OLD")]:
        raw = ai_correction.get(cust_en, {}).get("quickbi_d0", 0)
        for f in forecasts:
            if f["stage"] == "D0" and f["customer"] == cust_cn:
                f["forecast_case_raw"] = raw

    # ---- Step 5: 组装 Report ----
    report = build_report(forecasts, warnings_list)
    report["ai_correction"] = ai_correction  # AI 跟进修正结果
    report["meta"]["business_day"] = str(business_day)  # 统一日报展示日期

    # 今日总览排班账号统计口径：
    #   仅统计日报展示阶段（D0/D1/S1/S2），排除 D-1 和 S3
    staff_totals = {}
    if attendance:
        for a in attendance:
            if a.get("stage") not in ("D0", "D1", "S1", "S2"):
                continue
            dn = a.get("deptName", "")
            cnt = a.get("attendance", 0)
            if dn and cnt > 0:
                staff_totals[dn] = staff_totals.get(dn, 0) + cnt
    report["staff_totals"] = staff_totals

    # ---- Step 6: 飞书发送 ----
    feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)
    chat_id = args.chat_id or FEISHU_CHAT_ID

    if args.list_chats:
        print("\n[*] 群聊列表:")
        for c in feishu.list_chats():
            print(f"  {c.get('name', '?')} — {c.get('chat_id', '?')}")

    if args.dry_run:
        DISPLAY_STAGES = ["D0", "D1", "S1", "S2"]
        hr_rows = [f for f in report["forecast"] if f.get("stage") in DISPLAY_STAGES]
        stage_order = {s: i for i, s in enumerate(DISPLAY_STAGES)}
        hr_rows.sort(key=lambda f: (
            stage_order.get(f.get("stage", ""), 99),
            f.get("customer") or "",
            f.get("order_type") or "",
        ))

        # ---- AI 持续跟进修正 ----
        ai_corr = report.get("ai_correction", {})
        if ai_corr:
            print("\n" + "=" * 24)
            print("AI持续跟进修正")
            print("=" * 24)
            for idx, cust_label in enumerate(["NEW", "OLD"]):
                c = ai_corr.get(cust_label, {})
                if not c:
                    continue
                if idx > 0:
                    print("-" * 24)
                print(f"\n{cust_label}")
                print(f"  Quick BI：{c.get('quickbi_d0', 0)}")
                print(f"  AI继续跟进：{c.get('ai_follow', 0)}")
                print(f"  人工D0：{c.get('manual_d0', 0)}")
            print()

        print("\n" + "=" * 90)
        print("Forecast Preview（Excel 风格，仅显示有出勤的机构）")
        print("=" * 90)
        header = (f"{'阶段':<5} {'客群':<5} {'订单类型':<6} "
                  f"{'预估单数':>7} {'机构':<8} {'人数':>4} "
                  f"{'人均持单':>7} {'标准':>5} {'预警':<6} {'超标':>5}")
        print(header)
        print("-" * 90)
        for f in hr_rows:
            stage = f.get("stage", "") or ""
            customer = f.get("customer") or ""
            order_type = f.get("order_type") or ""
            forecast_case = f.get("forecast_case", 0) or 0
            target_load = f.get("target_load")
            target_str = str(target_load) if target_load is not None else "-"
            overall_load = f.get("forecast_load")
            overall_warning = f.get("warning_level", "GREEN")
            overall_exceed = f.get("exceed_amount", "-")
            teams = f.get("teams", [])

            if teams:
                has_any = False
                for t in teams:
                    dept_name = t.get("team", "")
                    dept_att = t.get("attendance", 0)
                    if dept_att <= 0:
                        continue  # 人数为 0 的机构不显示
                    has_any = True
                    load_str = str(int(round(overall_load))) if overall_load is not None else "-"
                    exc = str(overall_exceed) if overall_exceed != "-" else "-"
                    print(f"{stage:<5} {customer:<5} {order_type:<6} "
                          f"{forecast_case:>7} {dept_name:<8} {dept_att:>4} "
                          f"{load_str:>7} {target_str:>5} {overall_warning:<6} {exc:>5}")
                if not has_any:
                    # 所有机构人数都为 0 → 跳过该行
                    pass
            else:
                load_str = str(int(round(overall_load))) if overall_load is not None else "-"
                exc = str(overall_exceed) if overall_exceed != "-" else "-"
                print(f"{stage:<5} {customer:<5} {order_type:<6} "
                      f"{forecast_case:>7} {'-':<8} {'0':>4} "
                      f"{load_str:>7} {target_str:>5} {overall_warning:<6} {exc:>5}")
        print("=" * 90)

        # 今日总览 — 排班账号直接用 queryConfig 原始 attendance 累加
        total_cases = sum(f.get("forecast_case", 0) for f in hr_rows)
        staff_totals = report.get("staff_totals", {})
        dept_parts = []
        for dn in ["PL", "FI", "CX"]:
            if dn in staff_totals and staff_totals[dn] > 0:
                dept_parts.append(f"{dn}：{staff_totals[dn]}")
        total_staff = sum(staff_totals.values())
        staff_line = f"{total_staff}（{'｜'.join(dept_parts)}）" if dept_parts else str(total_staff)
        ai_follow_total = sum(
            ai_corr.get(k, {}).get("ai_follow", 0) for k in ["NEW", "OLD"]
        )
        quickbi_total = total_cases + ai_follow_total
        ai_save_pct = round(ai_follow_total / quickbi_total * 100, 1) if quickbi_total > 0 else 0.0
        print(f"\n今日总览")
        print(f"Quick BI预计案件：{quickbi_total} 单")
        if ai_follow_total > 0:
            print(f"AI继续跟进：{ai_follow_total}（{ai_save_pct}%）")
        print(f"人工预计案件：{total_cases} 单")
        print(f"排班账号：{staff_line}")
        print("=" * 90)
        return

    if not chat_id:
        print("\n[!] 未设置 chat_id，无法发送")
        sys.exit(1)

    try:
        if args.text_only:
            feishu.send_text(chat_id, build_text(report))
        else:
            card = build_card(report, feishu)
            feishu.send_card(chat_id, card)
        print(f"\n[OK] 已发送 -> {chat_id}")
    except Exception as e:
        print(f"\n[!] 发送失败: {e}")
        print("[*] 降级纯文本...")
        try:
            feishu.send_text(chat_id, build_text(report))
            print("[OK] 纯文本已发送")
        except Exception as e2:
            print(f"[!] 降级也失败: {e2}")
            sys.exit(1)


if __name__ == "__main__":
    main()
