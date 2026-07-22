#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿根廷每日案件及人力预估 V1

基于墨西哥 V2 架构适配：
  - 不再依赖 Excel，所有数据从 API 获取
  - 每天自动获取案件数据和实际上班人力
  - 自动完成业务计算 → 生成图片 → 发送飞书

用法:
    python 阿根廷每日案件及人力预估.py                    # 发送卡片
    python 阿根廷每日案件及人力预估.py --dry-run           # 计算不发送
    python 阿根廷每日案件及人力预估.py --text-only         # 纯文本降级
    python 阿根廷每日案件及人力预估.py --list-chats        # 列出群聊
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
QBI_AI_FOLLOW_API_ID = "e4ef78db8219"  # AI 催收回款监控（墨西哥专用，阿根廷暂不使用）
QBI_WS_ID = "f5e9cd70-8d92-48d6-8cc9-67f37c1c6e8d"

# -- 飞书配置 --
FEISHU_APP_ID = get("FEISHU_COLLECTION_APP_ID")
FEISHU_APP_SECRET = get("FEISHU_COLLECTION_APP_SECRET")

# 飞书接收人（可通过环境变量 FEISHU_CHAT_ID 覆盖）
#   群聊「阿根廷每日案件预估」: oc_8b5ef4aee4e93b29326cd8c0f3c24d90
#   私聊「我」              : ou_ffab3a07f1ff9fbca2a593c0d5e152ac
FEISHU_CHAT_ID = os.environ.get(
    "FEISHU_CHAT_ID",
    "oc_8b5ef4aee4e93b29326cd8c0f3c24d90",
)

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
FEISHU_CHAT_LIST_URL = "https://open.feishu.cn/open-apis/im/v1/chats"

# -- 催收系统配置 --
COLLECT_URL = os.environ.get(
    "COLLECT_URL",
    "https://loan-collect.middlela.com/vitech-collect-gateway/collect/staff/staffSchedulePage",
)
COLLECT_TOKEN = os.environ.get(
    "ARG_COLLECT_TOKEN",
    "",
)
COLLECT_REGION = os.environ.get("COLLECT_REGION", "AR")  # AR

# queryConfig 接口（排班人力数据）
COLLECT_CONFIG_URL = os.environ.get(
    "COLLECT_CONFIG_URL",
    "https://loan-collect.middlela.com/vitech-collect-gateway/collect/ailibty/queryConfig",
)

# -- 机构列表（fallback，优先使用 queryOrganPage API 动态获取） --
DEPT_LIST = [
    {"deptId": 4, "deptName": "WB-CX-AR"},
    {"deptId": 5, "deptName": "WB-FI-AR"},
    {"deptId": 10, "deptName": "AR_IN"},
]
DEPT_ORDER = {"AR_IN": 0, "WB-FI-AR": 1, "WB-CX-AR": 2}

# -- queryOrganPage 接口地址 --
COLLECT_ORGAN_URL = os.environ.get(
    "COLLECT_ORGAN_URL",
    "https://loan-collect.middlela.com/vitech-collect-gateway/collect/ailibty/queryOrganPage",
)

# -- DEBUG 模式 --
#   DEBUG=True 时，催收系统 API 读取 sample_attendance.json 代替真实请求
DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")

# sample 文件路径（与脚本同目录）
SAMPLE_ATTENDANCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_attendance.json")

# weekend 离线排班模板路径（周末无法连接内网时使用）
WEEKEND_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weekend_attendance.json")
WEEKEND_TEMPLATE_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weekend_attendance.csv")

# 周末模板: 机构简称 → 全称 + deptId
_WEEKEND_DEPT_MAP = {
    "CX": {"deptId": 4, "deptName": "WB-CX-AR"},
    "FI": {"deptId": 5, "deptName": "WB-FI-AR"},
    "PL": {"deptId": 10, "deptName": "AR_IN"},
}


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
#   阿根廷: S3 保留定义但暂不展示
STAGE = {
    "D-1": "D-1",   # 明日到期
    "D0":  "D0",    # 今日到期
    "D1":  "D1",    # 逾期 1 天
    "S1":  "S1",    # 逾期 2-3 天
    "S2":  "S2",    # 逾期 4-7 天
    "S3":  "S3",    # 逾期 8-15 天（阿根廷暂不展示，保留定义）
}
STAGE_LIST = ["D-1", "D0", "D1", "S1", "S2"]

# -- 展示阶段（Forecast / Business Engine / 图片 / 飞书 统一使用） --
#   Quick BI 取数不受影响，始终拉取全量阶段
ENABLED_STAGES = ["D-1", "D0", "D1", "S1"]

# -- 地区定义 --
REGION = {
    "MX": "MX",     # 墨西哥
    "AR": "AR",     # 阿根廷
}
REGION_LIST = ["MX", "AR"]

# -- 业务规则（统一读取，禁止硬编码） --
#   TARGET key 格式: "{STAGE}_{CUSTOMER}" 或 "{STAGE}"（S1/S2 不区分客群）
#   D-1/D0/D1 区分新老客，S1/S2/S3 不区分
RULE = {
    "TARGET": {
        "D-1_NEW": 50, "D-1_OLD": 50,
        "D0_NEW":  50, "D0_OLD":  50,
        "D1_NEW":  50, "D1_OLD":  50,
        "S1": 80,
        "S2": 120,
        "S3": 120,  # 阿根廷暂不展示，保留定义
    },
    "WARNING_GREEN_THRESHOLD": 0.9,  # forecast_load < target × 此值 → GREEN; 0.9×target ~ target → YELLOW; > target → RED
}

# -- AI 接管比例（阿根廷固定公式） --
AI_RATIO = 0.45         # D-1老客 × 45% = AI接管
AI_RATIO_NEW = 1.00    # D-1新客 × 100% = 全部AI接管

# -- 客群合并阶段（不区分新老客） --
#   阿根廷: D1/S1/S2 不区分客群；S3 暂不展示
MERGE_STAGES = {"D1", "S1", "S2"}


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

    # 阿根廷 APP 白名单
    AR_APPS = {"Instamonei"}

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
        if app not in AR_APPS:
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

    # 构造请求体（阿根廷时间 UTC-3）
    ar_tz = timezone(timedelta(hours=-3))
    now_ar = datetime.now(ar_tz)
    today_ar = now_ar.date()
    week_ago_ar = today_ar - timedelta(days=7)

    # scheduleStart / scheduleEnd: 毫秒时间戳
    start_dt = datetime(week_ago_ar.year, week_ago_ar.month, week_ago_ar.day,
                        tzinfo=ar_tz)
    end_dt = datetime(today_ar.year, today_ar.month, today_ar.day,
                      tzinfo=ar_tz)
    schedule_start_ms = int(start_dt.timestamp() * 1000)
    schedule_end_ms = int(end_dt.timestamp() * 1000)

    # date: ISO8601 格式
    date_iso = now_ar.strftime("%Y-%m-%dT%H:%M:%S")

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


def _fetch_query_organ_page():
    """
    调用 queryOrganPage API 动态获取机构列表。

    URL 优先使用环境变量 COLLECT_ORGAN_URL，
    未设置时由 COLLECT_CONFIG_URL 推导。

    返回: list[dict] | None — [{"deptId": int, "deptName": str}, ...]
    """
    if not COLLECT_TOKEN:
        print("[!] queryOrganPage: COLLECT_TOKEN 未配置，跳过")
        return None

    # 确定 URL：优先环境变量，其次推导
    organ_url = COLLECT_ORGAN_URL
    if not organ_url and COLLECT_CONFIG_URL:
        organ_url = COLLECT_CONFIG_URL.replace("queryConfig", "queryOrganPage")

    if not organ_url:
        print("[!] queryOrganPage: 无法确定 URL，跳过")
        return None

    print(f"[*] 催收系统: 请求 queryOrganPage (region={COLLECT_REGION})")
    print(f"  URL: {organ_url}")
    headers = {
        "Content-Type": "application/json",
        "X-CHOICE-TAG": "ms",
        "X-END-LANGUAGE": "zh_cn",
        "X-Fixed-Token": COLLECT_TOKEN,
    }

    body = {}

    try:
        resp = requests.post(
            organ_url,
            headers=headers,
            json=body,
            timeout=30,
        )
    except requests.exceptions.Timeout:
        print(f"  [!] queryOrganPage 请求超时")
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"  [!] queryOrganPage 连接失败: {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"  [!] queryOrganPage 请求异常: {e}")
        return None

    if resp.status_code != 200:
        print(f"  [!] queryOrganPage HTTP {resp.status_code}")
        print(f"  URL: {organ_url}")
        print(f"  响应: {resp.text[:500]}")
        return None

    try:
        data = resp.json()
    except (ValueError, AttributeError) as e:
        print(f"  [!] queryOrganPage JSON 解析失败: {e}")
        print(f"  响应: {resp.text[:500]}")
        return None

    # 尝试解析机构列表
    # 常见路径: data (list) / data.rows / data.list / data.result / ...
    dept_list = None
    inner = data.get("data")
    if isinstance(inner, list):
        dept_list = inner
    elif isinstance(inner, dict):
        for key in ("rows", "list", "records", "items", "result", "organList", "deptList"):
            val = inner.get(key)
            if isinstance(val, list) and val:
                dept_list = val
                break
        # data 本身可能是单条机构
        if dept_list is None and inner:
            dept_list = [inner]

    if not dept_list:
        print(f"  [!] queryOrganPage: 无法解析机构列表（完整响应已打印）")
        return None

    # 提取 deptId + deptName
    result = []
    for item in dept_list:
        if not isinstance(item, dict):
            continue
        did = item.get("deptId") or item.get("id") or item.get("organId")
        dname = item.get("deptName") or item.get("name") or item.get("organName")
        if did is not None:
            result.append({"deptId": int(did), "deptName": str(dname) if dname else str(did)})

    if not result:
        print(f"  [!] queryOrganPage: 未找到 deptId/deptName 字段（完整响应已打印）")
        return None

    return result


def fetch_organ_page():
    """
    获取全部启用机构列表。

    优先从 queryOrganPage API 动态获取，
    失败时降级使用硬编码 DEPT_LIST。

    返回: list[dict] — [{"deptId": int, "deptName": str}, ...]
    """
    # 1) 尝试 API 动态获取
    dept_list = _fetch_query_organ_page()
    if dept_list:
        parts = [f"{d['deptId']}={d['deptName']}" for d in dept_list]
        print(f"[*] 催收系统: 机构列表 ({len(dept_list)} 个): {', '.join(parts)}")
        return dept_list

    # 2) Fallback: 硬编码
    parts_fb = [f"{d['deptId']}={d['deptName']}" for d in DEPT_LIST]
    print(f"[*] 催收系统: 机构列表 (fallback, {len(DEPT_LIST)} 个): {', '.join(parts_fb)}")
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
                # 为每条记录注入机构信息（覆盖 deptId/deptName）
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


# -- deptDetails 字段映射（与墨西哥一致，已确认） --
_STAGE_MAP = {2: "D-1", 4: "D0", 17: "D1", 18: "S1", 19: "S2", 20: "S3"}
_CUST_MAP = {1: "新客", 2: "老客", 0: None}  # 0 → 全部客群


def load_weekend_attendance():
    """
    周末离线模式: 读取 weekend_attendance.json 模板，直接返回标准化 attendance 列表。

    JSON 格式（与墨西哥一致）:
      [
        {"阶段": "D-1", "客群": "新客", "机构": "CX", "人数": 0},
        ...
      ]

    也兼容旧版 CSV 格式（weekend_attendance.csv）。

    机构简称: CX → WB-CX-AR (deptId=4)
              FI → WB-FI-AR (deptId=5)
              PL → AR_IN       (deptId=10)

    客群映射: 新客→新客, 老客→老客, 合计/全客群→None

    Returns:
        list[dict] | None: 标准化 attendance 列表
    """
    import csv

    # 优先 JSON，其次 CSV
    template_path = WEEKEND_TEMPLATE
    is_csv = False
    if not os.path.exists(template_path):
        if os.path.exists(WEEKEND_TEMPLATE_CSV):
            template_path = WEEKEND_TEMPLATE_CSV
            is_csv = True
        else:
            print(f"  [!] 周末模板不存在: {WEEKEND_TEMPLATE}")
            return None

    rows = []
    if is_csv or template_path.endswith(".csv"):
        try:
            with open(template_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            print(f"  [!] 周末模板读取失败: {e}")
            return None
        print(f"[*] 周末离线模式: 读取 {template_path}")
        print(f"  共 {len(rows)} 条排班记录 (CSV)")

        attendance = []
        for i, row in enumerate(rows):
            stage = row.get("阶段", "").strip()
            cust_label = row.get("客群", "").strip()
            dept_short = row.get("机构（CX/WB-CX-AR｜FI/WB-FI-AR｜PL/AR_IN）", "").strip()
            if not dept_short:
                dept_short = row.get("机构", "").strip()
            count_str = row.get("上班人数", "").strip()

            if not stage and not dept_short:
                continue
            if stage not in ENABLED_STAGES:
                print(f"  [!] 第{i+2}行: 无效阶段 '{stage}'，跳过")
                continue
            dept_info = _WEEKEND_DEPT_MAP.get(dept_short)
            if dept_info is None:
                print(f"  [!] 第{i+2}行: 无效机构 '{dept_short}'，跳过")
                continue
            if cust_label in ("全客群", "合计"):
                customer = None
            elif cust_label in ("新客", "老客"):
                customer = cust_label
            else:
                print(f"  [!] 第{i+2}行: 无效客群 '{cust_label}'，跳过")
                continue
            try:
                count = int(count_str)
            except (ValueError, TypeError):
                count = 0
            count = max(count, 0)
            if count > 0:
                attendance.append({
                    "stage": stage,
                    "customer": customer,
                    "attendance": count,
                    "deptId": dept_info["deptId"],
                    "deptName": dept_info["deptName"],
                })
    else:
        # JSON 格式
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            print(f"  [!] 周末模板读取失败: {e}")
            return None
        if not isinstance(rows, list):
            print(f"  [!] 周末模板格式错误: 应为 JSON 数组")
            return None
        print(f"[*] 周末离线模式: 读取 {template_path}")
        print(f"  共 {len(rows)} 条排班记录 (JSON)")

        attendance = []
        for i, row in enumerate(rows):
            stage = row.get("阶段", "").strip()
            cust_label = row.get("客群", "").strip()
            dept_short = row.get("机构", "").strip()
            count = row.get("人数", 0)

            if not stage and not dept_short:
                continue
            if stage not in ENABLED_STAGES:
                print(f"  [!] 第{i+1}行: 无效阶段 '{stage}'，跳过")
                continue
            dept_info = _WEEKEND_DEPT_MAP.get(dept_short)
            if dept_info is None:
                print(f"  [!] 第{i+1}行: 无效机构 '{dept_short}'，跳过")
                continue
            if cust_label in ("全客群", "合计"):
                customer = None
            elif cust_label in ("新客", "老客"):
                customer = cust_label
            else:
                print(f"  [!] 第{i+1}行: 无效客群 '{cust_label}'，跳过")
                continue
            try:
                count = int(count)
            except (ValueError, TypeError):
                count = 0
            count = max(count, 0)
            if count > 0:
                attendance.append({
                    "stage": stage,
                    "customer": customer,
                    "attendance": count,
                    "deptId": dept_info["deptId"],
                    "deptName": dept_info["deptName"],
                })

    # 去重合并: 相同 (stage, customer, deptName) 累加
    merged = {}
    for a in attendance:
        key = (a["stage"], a["customer"], a["deptName"])
        if key in merged:
            merged[key]["attendance"] += a["attendance"]
        else:
            merged[key] = dict(a)

    result = list(merged.values())
    nonzero = len([a for a in result if a['attendance'] > 0])
    print(f"  [周末模式] 生成 {len(result)} 条 attendance (含 {nonzero} 条有人上班)")
    return result  # 即使全 0 也返回列表，让 Forecast 正常计算


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


# ============================================================
#  7.5 AI 持续跟进 — 修正 D-1 Forecast（阿根廷固定公式）
# ============================================================

def fetch_ai_follow_cases():
    """
    阿根廷: 目前没有 AI 接口，使用固定公式。

    固定公式:
      AI案件   = D-1新客 × 100%  +  D-1老客 × AI_RATIO
      人工案件 = D-1新客 × 0%     +  D-1老客 × (1 - AI_RATIO)

    返回空列表，实际修正逻辑在 apply_ai_follow_correction 中完成。
    保留接口签名与墨西哥一致，后续接入真实 API 时只需替换函数体。

    返回: list[dict] — 当前返回空列表
    """
    print(f"[*] AI 跟进: Argentina 固定公式 (D-1新客×100% + D-1老客×{AI_RATIO:.0%})，无需 API 调用")
    return []


def apply_ai_follow_correction(cases, ai_data):
    """
    阿根廷: 对 D-1 应用固定 AI 公式。

    D-1新客: 100% AI（人工=0）
    D-1老客: AI案件 = D-1老客 × AI_RATIO, 人工 = D-1老客 × (1 - AI_RATIO)

    直接在 cases 中修改 D-1 的案件量。

    Args:
        cases: normalize_case() 的输出
        ai_data: fetch_ai_follow_cases() 的输出（当前忽略）

    Returns:
        dict: {"NEW": {"quickbi_d_1": int, "ai_follow": int, "manual_d_1": int},
               "OLD": {...}}
    """
    # 汇总 D-1 老客总数
    d1_old_total = 0
    for c in cases:
        if c.get("customer") == "老客":
            d1_old_total += c.get("stages", {}).get("D-1", 0)

    # 固定公式: 老客
    ai_follow_old = round(d1_old_total * AI_RATIO)
    manual_old = d1_old_total - ai_follow_old

    # D-1 新客: 100% AI 接管
    d1_new_total = 0
    for c in cases:
        if c.get("customer") == "新客":
            d1_new_total += c.get("stages", {}).get("D-1", 0)

    ai_follow_new = round(d1_new_total * AI_RATIO_NEW)  # = d1_new_total
    manual_new = d1_new_total - ai_follow_new  # = 0

    correction = {
        "NEW": {
            "quickbi_d_1": d1_new_total,
            "ai_follow": ai_follow_new,
            "manual_d_1": manual_new,
        },
        "OLD": {
            "quickbi_d_1": d1_old_total,
            "ai_follow": ai_follow_old,
            "manual_d_1": manual_old,
        },
    }

    # 修正 cases: D-1新客归零，D-1老客只留人工
    for c in cases:
        if c.get("customer") == "老客":
            c["stages"]["D-1"] = manual_old
        elif c.get("customer") == "新客":
            c["stages"]["D-1"] = manual_new  # 0

    print(f"  AI 跟进: D-1新客={d1_new_total} (100%AI, AI接管={ai_follow_new}, 人工={manual_new}), "
          f"D-1老客={d1_old_total}, AI接管={ai_follow_old}, 人工={manual_old}")
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
        # customer 匹配: None=ALL 通配所有客群
        if a_cust is not None and customer is not None and a_cust != customer:
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

    阿根廷: D-1/D0/D1 区分新老客，S1/S2 不区分。无 S3 阶段。

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
    #   MERGE_STAGES 合并新客+老客（customer = None）
    #   其余阶段保留新老客区分
    # ================================================================
    stage_cust_cases = {}  # {(stage, customer|None): total_cases}
    for c in cases:
        cust = c.get("customer", "")
        stages = c.get("stages", {})
        for stage_key in ENABLED_STAGES:
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

        # 业务规则：仅保留 D-1/D0/D1/S1/S2（阿根廷无 S3）
        if stage == "S3":
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
                # None 表示 ALL → 匹配任何客群
                if a_cust is not None and cust is not None and a_cust != cust:
                    continue
                count = a.get("attendance", 0)
                total_attendance += count
                dept_name = a.get("deptName", "")
                teams.append({"team": dept_name, "attendance": count})

        # 固定机构顺序
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

def build_report(forecasts, warnings, forecast_date_ar=None, region="AR"):
    """
    组装统一 report 对象。

    图片模块、飞书模块、日志模块只能读取此 report，
    不得直接访问 Forecast / Warning / Quick BI / 催收系统。

    Args:
        forecasts:         compute_forecast() 的输出 (list[dict])
        warnings:          compute_forecast() 的输出 (list[dict])
        forecast_date_ar:  阿根廷业务日期 (str "YYYY-MM-DD")，默认当天阿根廷时间
        region:            地区代码，默认 "AR"

    Returns:
        dict: 统一 report 对象
    """
    # -- meta --
    cn_tz = timezone(timedelta(hours=8))
    run_time_cn = datetime.now(cn_tz).strftime("%Y-%m-%d %H:%M:%S")

    if forecast_date_ar is None:
        ar_tz = timezone(timedelta(hours=-3))
        forecast_date_ar = datetime.now(ar_tz).strftime("%Y-%m-%d")

    # -- 统计（只统计，不计算） --
    green_count = sum(1 for f in forecasts if f.get("warning_level") == "GREEN")
    yellow_count = sum(1 for f in forecasts if f.get("warning_level") == "YELLOW")
    red_count = sum(1 for f in forecasts if f.get("warning_level") == "RED")

    report = {
        "meta": {
            "run_time_cn": run_time_cn,
            "forecast_date_ar": forecast_date_ar,
            "region": region,
            "version": "V1",
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
#  9. Data Validation — 全链路数据一致性校验
# ============================================================

def _snapshot_case_totals(cases):
    """快照 cases 各阶段案件量（AI 修正前）。"""
    STAGES = ENABLED_STAGES
    totals = {s: 0 for s in STAGES}
    for c in cases:
        stages = c.get("stages", {})
        for s in STAGES:
            totals[s] += stages.get(s, 0)
    return totals


def validate_data(entry_data_list, cases_pre_ai_totals, ai_correction, forecasts, attendance, report):
    """
    Data Validation — 校验全链路 6 项数据一致性。

    1. Quick BI 原始案件 ↔ cases（AI 修正前）
    2. AI 扣减公式正确
    3. Forecast 人工案件 ↔ cases（AI 修正后）
    4. Forecast 阶段汇总 ↔ 全量汇总
    5. Business Engine 内部一致性
    6. 图片数据（今日总览）↔ Forecast

    Returns:
        bool: True = 全部通过
    """
    errors = []
    STAGES = ENABLED_STAGES
    DISPLAY_STAGES = ENABLED_STAGES

    # ================================================================
    #  Check 1: Quick BI 原始案件 ↔ cases（AI 修正前）
    # ================================================================
    qbi_totals = {s: 0 for s in STAGES}
    for e in entry_data_list:
        for s in STAGES:
            qbi_totals[s] += e.get("summary", {}).get(s, 0)

    for s in STAGES:
        if qbi_totals[s] != cases_pre_ai_totals[s]:
            errors.append(
                f"[1] Quick BI → cases: {s} QBI={qbi_totals[s]} ≠ cases={cases_pre_ai_totals[s]}"
            )

    # ================================================================
    #  Check 2: AI 扣减公式
    # ================================================================
    old_corr = ai_correction.get("OLD", {})
    new_corr = ai_correction.get("NEW", {})
    qbi_d1_old = old_corr.get("quickbi_d_1", 0)
    ai_follow_old = old_corr.get("ai_follow", 0)
    manual_old = old_corr.get("manual_d_1", 0)
    qbi_d1_new = new_corr.get("quickbi_d_1", 0)
    ai_follow_new = new_corr.get("ai_follow", 0)
    manual_new = new_corr.get("manual_d_1", 0)

    expected_ai = round(qbi_d1_old * AI_RATIO)
    if expected_ai != ai_follow_old:
        errors.append(
            f"[2] AI 扣减: D-1老客×{AI_RATIO:.0%}={expected_ai} ≠ 实际={ai_follow_old}"
        )
    if qbi_d1_old != ai_follow_old + manual_old:
        errors.append(
            f"[2] AI 扣减: {qbi_d1_old} ≠ AI({ai_follow_old}) + 人工({manual_old})"
        )
    if new_corr.get("ai_follow", 0) != new_corr.get("quickbi_d_1", 0):
        errors.append(
            f"[2] AI 扣减: D-1新客应100%AI接管 "
            f"(QBI={new_corr.get('quickbi_d_1', 0)}, AI={new_corr.get('ai_follow', 0)})"
        )

    # ================================================================
    #  Check 3: Forecast 人工案件 ↔ cases（AI 修正后）
    # ================================================================
    post_ai_expected = dict(cases_pre_ai_totals)
    post_ai_expected["D-1"] -= (ai_follow_old + new_corr.get("ai_follow", 0))

    fc_totals = {s: 0 for s in STAGES}
    for f in forecasts:
        stage = f.get("stage", "")
        if stage in STAGES:
            fc_totals[stage] += f.get("forecast_case", 0)

    for s in STAGES:
        if fc_totals[s] != post_ai_expected[s]:
            errors.append(
                f"[3] Forecast 人工案件: {s} forecast={fc_totals[s]} ≠ expected={post_ai_expected[s]}"
            )

    # ================================================================
    #  Check 4: Forecast 汇总
    # ================================================================
    fc_display = [f for f in forecasts if f.get("stage") in DISPLAY_STAGES]
    fc_display_sum = sum(f.get("forecast_case", 0) for f in fc_display)
    fc_all_sum = sum(fc_totals.values())
    if fc_display_sum != fc_all_sum:
        errors.append(
            f"[4] Forecast 汇总: display={fc_display_sum} ≠ all={fc_all_sum}"
        )

    # ================================================================
    #  Check 5: Business Engine 内部一致性
    # ================================================================
    for f in forecasts:
        stage = f.get("stage", "")
        cust = f.get("customer") or "ALL"
        fc = f.get("forecast_case", 0)
        att = f.get("attendance", 0)
        fl = f.get("forecast_load")
        target = f.get("target_load")
        nst = f.get("need_staff_total", 0)

        if att > 0:
            if fl is None:
                errors.append(
                    f"[5] Business Engine: {stage}/{cust} att={att}>0 but load=None"
                )
            else:
                expected_fl = round(fc / att, 1)
                if fl != expected_fl:
                    errors.append(
                        f"[5] Business Engine: {stage}/{cust} load={fl} ≠ {expected_fl} "
                        f"({fc}/{att})"
                    )

        if target is not None and target > 0 and fc > 0:
            expected_nst = math.ceil(fc / target)
            if nst != expected_nst:
                errors.append(
                    f"[5] Business Engine: {stage}/{cust} "
                    f"need_staff={nst} ≠ ceil({fc}/{target})={expected_nst}"
                )

    # ================================================================
    #  Check 6: 图片今日总览 ↔ Forecast
    # ================================================================
    report_fc_sum = sum(
        f.get("forecast_case", 0) for f in report.get("forecast", [])
    )
    if report_fc_sum != fc_all_sum:
        errors.append(
            f"[6] 图片数据: report forecast sum={report_fc_sum} ≠ computed={fc_all_sum}"
        )

    # 排班总数: report.staff_totals ↔ attendance 直接累加
    staff_from_report = sum(report.get("staff_totals", {}).values())
    staff_from_att = 0
    if attendance:
        for a in attendance:
            if a.get("stage") in DISPLAY_STAGES:
                staff_from_att += a.get("attendance", 0)
    if staff_from_report != staff_from_att:
        errors.append(
            f"[6] 图片数据: staff_totals={staff_from_report} ≠ attendance={staff_from_att}"
        )

    # ================================================================
    #  Output
    # ================================================================
    total_checks = 6
    print()
    print("=" * 24)
    print("Data Validation")
    print("=" * 24)
    if errors:
        print("❌ FAIL")
        for err in errors:
            print(f"  {err}")
    else:
        print(f"✓ PASS ({total_checks}/6)")
        print(f"  [1] Quick BI → cases: {'✓':>1} ({'+'.join(str(qbi_totals[s]) for s in STAGES)})")
        print(f"  [2] AI 扣减: {'✓':>1} (D-1新客 {qbi_d1_new}→AI {ai_follow_new}+人工 {manual_new}, D-1老客 {qbi_d1_old}→AI {ai_follow_old}+人工 {manual_old})")
        print(f"  [3] Forecast 案件: {'✓':>1} ({'+'.join(str(fc_totals[s]) for s in STAGES)})")
        print(f"  [4] Forecast 汇总: {'✓':>1} (display={fc_display_sum})")
        print(f"  [5] Business Engine: {'✓':>1} ({len(forecasts)} entries)")
        print(f"  [6] 图片数据: {'✓':>1} (staff={staff_from_report})")
    print("=" * 24)

    return len(errors) == 0


# ============================================================
#  10. 图片生成
# ============================================================

def _forecast_to_case_table(forecast):
    """
    从 forecast 数据重建表1行（入口 + 各阶段单数）。

    D-1/D0/D1 按客群分组，S1/S2 合并为全客群。
    返回: (rows, stage_totals)
      rows:  [["新客", D-1, D0, D1, S1, S2], ...]
      stage_totals: {"D-1": N, "D0": N, ...}
    """
    stage_cols = ENABLED_STAGES
    STAGE_CUST = MERGE_STAGES  # 不区分客群的阶段（引用全局配置）

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


# -- 机构简称映射（仅图片展示） --
_DEPT_SHORT = {"WB-CX-AR": "CX", "WB-FI-AR": "FI", "AR_IN": "PL"}

# -- 负责机构映射：无实际排班时显示对应负责机构 --
_RESPONSIBLE_DEPT = {
    ("D-1", "新客"): "CX",
    ("D-1", "老客"): "FI",
    ("D0", "新客"): "CX",
    ("D0", "老客"): "FI",
    ("D1", None): "CX",
    ("S1", None): "FI",
}


def draw_table_image(report):
    """
    绘制日报图片 — 1448px Excel 驾驶舱风格，12 列含 AI 接管案件。

    布局（从上到下）：
      ① 顶栏：阿根廷每日案件及人力预估日报 · 日期 · 更新时间
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
    target_date = meta.get("business_day", meta.get("forecast_date_ar",
                          date.today().strftime("%Y-%m-%d")))
    run_time_cn = meta.get("run_time_cn", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    # 表格内日期用中文格式 "7月3日"
    try:
        _parts = target_date.split("-")
        table_date = f"{int(_parts[1])}月{int(_parts[2])}日"
    except (ValueError, IndexError):
        table_date = target_date

    DISPLAY_STAGES = ENABLED_STAGES
    hr_rows = [f for f in forecast if f.get("stage") in DISPLAY_STAGES]

    stage_order = {s: i for i, s in enumerate(DISPLAY_STAGES)}
    hr_rows.sort(key=lambda f: (
        stage_order.get(f.get("stage", ""), 99),
        f.get("customer") or "",
        f.get("order_type") or "",
    ))

    # ---- 展平 ----
    # AI 扣减查找表（阿根廷: 仅 D-1 老客有 AI 扣减）
    ai_corr_lookup = report.get("ai_correction", {})

    ai_deduct_map = {
        ("D-1", "新客"): ai_corr_lookup.get("NEW", {}).get("ai_follow", 0),
        ("D-1", "老客"): ai_corr_lookup.get("OLD", {}).get("ai_follow", 0),
    }
    table_rows = []
    for f in hr_rows:
        stage = f.get("stage", "")
        customer = f.get("customer") or ""
        customer_key = customer if customer else None  # "" → None for mapping
        order_type = f.get("order_type") or ""
        forecast_case = f.get("forecast_case", 0)
        target_load = f.get("target_load")
        target_str = str(target_load) if target_load is not None else "-"
        # D1 合并客群后标准固定为 50（仅图片展示，不影响 Business Engine）
        if stage == "D1" and customer_key is None and target_load is None:
            target_str = "50"
        overall_load = f.get("forecast_load")
        overall_warning = f.get("warning_level", "GREEN")
        overall_exceed = f.get("exceed_amount", "-")
        teams = f.get("teams", [])

        # AI 扣减（仅 D-1 老客有值）
        _ai_ded = ai_deduct_map.get((stage, customer), 0)
        ai_deduct_str = str(_ai_ded) if _ai_ded > 0 else ""

        # 负责机构（fallback：当无实际排班数据时显示）
        responsible_dept = _RESPONSIBLE_DEPT.get((stage, customer_key), "-")
        responsible_short = _DEPT_SHORT.get(responsible_dept, responsible_dept)

        # 超标数量：无超标时显示 0（与 Mexico 的 "-" 不同）
        if overall_warning == "RED" and overall_exceed != "-":
            exc = str(overall_exceed)
        else:
            exc = "0"

        if teams:
            has_any = False
            for t in teams:
                dept_att = t.get("attendance", 0)
                if dept_att <= 0:
                    continue
                has_any = True
                dept_full = t.get("team", "")
                dept_short = _DEPT_SHORT.get(dept_full, dept_full)
                # 人均持单 = 案件 ÷ 人数（按 Mexico 公式）
                row_load = str(int(round(forecast_case / dept_att))) if dept_att > 0 else ""
                table_rows.append({
                    "date": table_date,
                    "stage": stage,
                    "customer": customer if customer else "-",
                    "order_type": order_type if order_type else "-",
                    "forecast_case": forecast_case,
                    "ai_deduct": ai_deduct_str,
                    "dept_name": dept_short,
                    "dept_att": dept_att,
                    "load_str": row_load,
                    "target_str": target_str,
                    "warning": overall_warning,
                    "exceed_str": exc,
                })
            if not has_any:
                # 所有机构人数为 0 → 显示负责机构，预警强制 GREEN
                table_rows.append({
                    "date": table_date,
                    "stage": stage,
                    "customer": customer if customer else "-",
                    "order_type": order_type if order_type else "-",
                    "forecast_case": forecast_case,
                    "ai_deduct": ai_deduct_str,
                    "dept_name": responsible_short,
                    "dept_att": 0,
                    "load_str": "",
                    "target_str": target_str,
                    "warning": "GREEN",
                    "exceed_str": exc,
                })
        else:
            # 无机构数据 → 显示负责机构，预警强制 GREEN
            table_rows.append({
                "date": table_date,
                "stage": stage,
                "customer": customer if customer else "-",
                "order_type": order_type if order_type else "-",
                "forecast_case": forecast_case,
                "ai_deduct": ai_deduct_str,
                "dept_name": responsible_short,
                "dept_att": 0,
                "load_str": "",
                "target_str": target_str,
                "warning": "GREEN",
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
        f"阿根廷每日案件及人力预估日报    "
        f"{target_date}    "
        f"更新时间：{run_time_cn}"
    )
    _ctext(PAD, y, TOTAL_W - PAD * 2, TOP_H, top_line, _ftop, C_TOP_TEXT)
    y += TOP_H

    # ================================================================
    #  ② 金色大标题：喜 象 盘
    # ================================================================
    _fill(0, y, TOTAL_W, GOLD_H, (255, 255, 255))
    _ctext(PAD, y, TOTAL_W - PAD * 2, GOLD_H, "阿  根  廷", _fgold, C_GOLD)
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

            # --- 合并区域（列 0-5）：统一白底背景 + 文字 ---
            _fill(PAD, merge_top, MERGE_R - PAD, merge_h, C_BODY_BG)
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

                # 统一白底背景（预警仅通过圆点颜色表示）
                _fill(DEPT_L, cy, TABLE_R - DEPT_L, ROW_H, C_BODY_BG)

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
        f"AI接管案件：D-1新客×100% + D-1老客×{AI_RATIO:.0%}",
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
    dept_list_ordered = [dn for dn in ["AR_IN", "WB-FI-AR", "WB-CX-AR"]
                         if dn in staff_totals and staff_totals[dn] > 0]
    total_staff = sum(staff_totals.values())

    # AI 继续跟进总数（D-1新客×100% + D-1老客×30%）
    ai_corr_img = report.get("ai_correction", {})
    ai_follow_img = (ai_corr_img.get("NEW", {}).get("ai_follow", 0) +
                     ai_corr_img.get("OLD", {}).get("ai_follow", 0))

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
#  11. 飞书发送
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
                "content": f"阿根廷分案预估 - {target_date}",
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
    target_date = meta.get("forecast_date_ar", date.today().strftime("%Y-%m-%d"))

    DISPLAY_STAGES = ENABLED_STAGES
    hr_rows = [f for f in forecast if f.get("stage") in DISPLAY_STAGES]

    lines = [f"阿根廷分案预估 - {target_date}", ""]

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
    for dn in ["AR_IN", "WB-FI-AR", "WB-CX-AR"]:
        if dn in staff_totals and staff_totals[dn] > 0:
            dept_parts.append(f"{dn}：{staff_totals[dn]}")
    total_staff = sum(staff_totals.values())
    staff_line = f"{total_staff}（{'｜'.join(dept_parts)}）" if dept_parts else str(total_staff)

    # AI 继续跟进（D-1新客×100% + D-1老客×30%）
    ai_corr_text = report.get("ai_correction", {})
    ai_follow_text = (ai_corr_text.get("NEW", {}).get("ai_follow", 0) +
                      ai_corr_text.get("OLD", {}).get("ai_follow", 0))
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
#  11.5 Debug — queryConfig 映射核对
# ============================================================

def debug_query_config(raw_data):
    """
    调试输出: queryConfig 原始字段映射表。

    逐行打印每个 Stage/机构/platformNew 的:
      overdueLevelId → Stage (via _STAGE_MAP)
      deptName       → 机构
      platformNew    → NEW / OLD / ALL
      tomorrowStaffAgentCount
      caseCapacity
      floatCaseNum

    不修改任何数据，仅用于与 queryConfig 页面逐行核对。
    """
    if raw_data is None:
        print("[DEBUG] queryConfig: 无数据")
        return

    inner = raw_data.get("data", {})
    dept_list = inner.get("deptDetails", [])
    if not dept_list:
        print("[DEBUG] queryConfig: deptDetails 为空")
        return

    PF_MAP = {0: "ALL", 1: "NEW", 2: "OLD"}

    # 收集行
    rows = []
    for rec in dept_list:
        if not isinstance(rec, dict):
            continue
        oid = rec.get("overdueLevelId")
        stage = _STAGE_MAP.get(oid, f"UNKNOWN({oid})")
        dept_name = rec.get("deptName", "")
        pn_raw = rec.get("platformNew")
        pn_display = PF_MAP.get(pn_raw, str(pn_raw))
        tsac = rec.get("tomorrowStaffAgentCount", "")
        cc = rec.get("caseCapacity", "")
        fcn = rec.get("floatCaseNum", "")
        rows.append((stage, dept_name, pn_display, tsac, cc, fcn))

    # 排序: stage → dept → platformNew
    _stage_order = {s: i for i, s in enumerate(["D-1", "D0", "D1", "S1", "S2", "S3"])}
    _dept_order = DEPT_ORDER
    _pf_order = {"NEW": 0, "OLD": 1, "ALL": 2}
    rows.sort(key=lambda r: (
        _stage_order.get(r[0], 99),
        _dept_order.get(r[1], 99),
        _pf_order.get(r[2], 99),
    ))

    print()
    print("=" * 80)
    print("queryConfig Debug Table  (与 queryConfig 页面逐行核对)")
    print("=" * 80)
    print(f"{'Stage':<6} {'机构':<6} {'platformNew':>5}  "
          f"{'tomorrowStaffAgentCount':>25}  {'caseCapacity':>14}  {'floatCaseNum':>14}")
    print("-" * 80)
    for stage, dept_name, pn_display, tsac, cc, fcn in rows:
        print(f"{stage:<6} {dept_name:<6} {pn_display:>5}  "
              f"{str(tsac):>25}  {str(cc):>14}  {str(fcn):>14}")
    print("=" * 80)
    print()


# ============================================================
#  11.6 DEBUG — 全链路对账
# ============================================================

def debug_reconciliation(entry_data_list, cases_pre_ai_totals, cases,
                         ai_correction, staff_data, attendance,
                         forecasts, report):
    """
    DEBUG=True 时自动输出全链路对账信息。

    7 段输出，覆盖从 Quick BI 取数到最终图片数据的完整链路。
    """
    QBI_STAGES = ["D-1", "D0", "D1", "S1", "S2"]  # Quick BI 全量
    FC_STAGES = ENABLED_STAGES                     # Forecast / 图片

    print()
    print("=" * 70)
    print("  DEBUG — 全链路对账")
    print("=" * 70)

    # ================================================================
    #  1. Quick BI 各 Stage 原始案件数
    # ================================================================
    print()
    print("━" * 70)
    print("  1. Quick BI 原始案件数")
    print("━" * 70)
    qbi_totals = {s: 0 for s in FC_STAGES}
    print(f"  {'入口':<12}", end="")
    for s in FC_STAGES:
        print(f"{s:>6}", end="")
    print(f"  {'合计':>6}")
    print(f"  {'-' * 12}{'' :->54}")
    for e in entry_data_list:
        name = e.get("name", "")
        sm = e.get("summary", {})
        row_sum = sum(sm.get(s, 0) for s in FC_STAGES)
        print(f"  {name:<12}", end="")
        for s in FC_STAGES:
            v = sm.get(s, 0)
            qbi_totals[s] += v
            print(f"{v:>6}", end="")
        print(f"  {row_sum:>6}")
    print(f"  {'-' * 12}{'':->54}")
    qbi_all = sum(qbi_totals.values())
    print(f"  {'合计':<12}", end="")
    for s in FC_STAGES:
        print(f"{qbi_totals[s]:>6}", end="")
    print(f"  {qbi_all:>6}")

    # ================================================================
    #  2. AI 扣减前（cases 快照）
    # ================================================================
    print()
    print("━" * 70)
    print("  2. AI 扣减前（cases 快照）")
    print("━" * 70)
    pre_total = sum(cases_pre_ai_totals.values())
    print(f"  ", end="")
    for s in FC_STAGES:
        print(f"{s:>6}", end="")
    print(f"  {'合计':>6}")
    print(f"  {'':->54}")
    print(f"  ", end="")
    for s in FC_STAGES:
        print(f"{cases_pre_ai_totals[s]:>6}", end="")
    print(f"  {pre_total:>6}")

    # ================================================================
    #  3. AI 扣减后（cases 当前值）
    # ================================================================
    print()
    print("━" * 70)
    print("  3. AI 扣减后（D-1老客 × AI_RATIO）")
    print("━" * 70)
    post_totals = {s: 0 for s in FC_STAGES}
    for c in cases:
        stages = c.get("stages", {})
        for s in FC_STAGES:
            post_totals[s] += stages.get(s, 0)
    post_total = sum(post_totals.values())

    old_corr = ai_correction.get("OLD", {})
    new_corr = ai_correction.get("NEW", {})
    qbi_d1_new = new_corr.get("quickbi_d_1", 0)
    qbi_d1_old = old_corr.get("quickbi_d_1", 0)
    ai_ded = old_corr.get("ai_follow", 0)
    manual_old = old_corr.get("manual_d_1", 0)

    print(f"  D-1 新客: {qbi_d1_new} → 不变（不扣减）")
    print(f"  D-1 老客: {qbi_d1_old} → AI接管={ai_ded}  人工={manual_old}")
    print(f"  ", end="")
    for s in FC_STAGES:
        print(f"{s:>6}", end="")
    print(f"  {'合计':>6}")
    print(f"  {'':->54}")
    print(f"  ", end="")
    for s in FC_STAGES:
        print(f"{post_totals[s]:>6}", end="")
    print(f"  {post_total:>6}")

    # ================================================================
    #  4. queryConfig 原始配置
    # ================================================================
    print()
    print("━" * 70)
    print("  4. queryConfig 原始配置（仅 ENABLED_STAGES）")
    print("━" * 70)
    if attendance:
        # 按 (stage, dept) 汇总
        att_summary = {}
        for a in attendance:
            stage = a.get("stage", "")
            if stage not in ENABLED_STAGES:
                continue
            dept = a.get("deptName", "")
            cnt = a.get("attendance", 0)
            cap = a.get("_case_capacity", "")
            flt = a.get("_float_case_num", "")
            key = (stage, dept)
            if key not in att_summary:
                att_summary[key] = {"att": 0, "cap": cap, "flt": flt}
            att_summary[key]["att"] += cnt

        print(f"  {'Stage':<6} {'机构':<10} {'人数':>4}  {'caseCapacity':>14}  {'floatCaseNum':>14}")
        print(f"  {'':->66}")
        stage_order = {s: i for i, s in enumerate(ENABLED_STAGES)}
        for (stage, dept), info in sorted(att_summary.items(),
                                           key=lambda x: (stage_order.get(x[0][0], 99),
                                                          DEPT_ORDER.get(x[0][1], 99))):
            print(f"  {stage:<6} {dept:<10} {info['att']:>4}  "
                  f"{str(info['cap']):>14}  {str(info['flt']):>14}")
        total_att = sum(v["att"] for v in att_summary.values())
        print(f"  {'':->66}")
        print(f"  {'合计':<6} {'':10} {total_att:>4}")
    else:
        print(f"  (无 attendance 数据)")

    # ================================================================
    #  5. Forecast
    # ================================================================
    print()
    print("━" * 70)
    print("  5. Forecast")
    print("━" * 70)
    fc_stage_totals = {s: 0 for s in ENABLED_STAGES}
    for f in forecasts:
        s = f.get("stage", "")
        if s in fc_stage_totals:
            fc_stage_totals[s] += f.get("forecast_case", 0)
    print(f"  ", end="")
    for s in ENABLED_STAGES:
        print(f"{s:>6}", end="")
    print(f"  {'合计':>6}")
    print(f"  {'':->54}")
    print(f"  ", end="")
    for s in ENABLED_STAGES:
        print(f"{fc_stage_totals[s]:>6}", end="")
    fc_all = sum(fc_stage_totals.values())
    print(f"  {fc_all:>6}")

    # ================================================================
    #  6. Business Engine
    # ================================================================
    print()
    print("━" * 70)
    print("  6. Business Engine")
    print("━" * 70)
    hr_rows = [f for f in forecasts if f.get("stage") in ENABLED_STAGES]
    stage_order_be = {s: i for i, s in enumerate(ENABLED_STAGES)}
    hr_rows.sort(key=lambda f: (
        stage_order_be.get(f.get("stage", ""), 99),
        f.get("customer") or "",
    ))
    print(f"  {'Stage':<6} {'客群':<5} {'案件':>5} {'人数':>4} "
          f"{'load':>5} {'target':>6} {'need':>4} {'+人':>4} {'预警':<6}")
    print(f"  {'':->62}")
    for f in hr_rows:
        stage = f.get("stage", "")
        cust = f.get("customer") or "ALL"
        fc = f.get("forecast_case", 0)
        att = f.get("attendance", 0)
        fl = f.get("forecast_load")
        fl_str = str(fl) if fl is not None else "-"
        target = f.get("target_load")
        target_str = str(target) if target is not None else "-"
        nst = f.get("need_staff_total", 0)
        nas = f.get("need_add_staff", 0)
        wl = f.get("warning_level", "GREEN")
        print(f"  {stage:<6} {cust:<5} {fc:>5} {att:>4} "
              f"{fl_str:>5} {target_str:>6} {nst:>4} {nas:>4} {wl:<6}")
    print(f"  {'':->62}")
    red_n = sum(1 for f in hr_rows if f.get("warning_level") == "RED")
    yel_n = sum(1 for f in hr_rows if f.get("warning_level") == "YELLOW")
    grn_n = sum(1 for f in hr_rows if f.get("warning_level") == "GREEN")
    print(f"  GREEN={grn_n}  YELLOW={yel_n}  RED={red_n}")

    # ================================================================
    #  7. 最终图片数据
    # ================================================================
    print()
    print("━" * 70)
    print("  7. 最终图片数据（今日总览）")
    print("━" * 70)
    ai_corr_rpt = report.get("ai_correction", {})
    ai_follow_rpt = (ai_corr_rpt.get("NEW", {}).get("ai_follow", 0) +
                     ai_corr_rpt.get("OLD", {}).get("ai_follow", 0))
    quickbi_total = fc_all + ai_follow_rpt
    ai_pct = round(ai_follow_rpt / quickbi_total * 100, 1) if quickbi_total > 0 else 0.0

    staff_totals_rpt = report.get("staff_totals", {})
    total_staff_rpt = sum(staff_totals_rpt.values())
    dept_parts = [f"{dn}={staff_totals_rpt[dn]}" for dn in DEPT_ORDER
                  if dn in staff_totals_rpt and staff_totals_rpt[dn] > 0]

    print(f"  Quick BI 预计案件 : {quickbi_total}")
    print(f"  AI 接管案件       : {ai_follow_rpt}  ({ai_pct}%)")
    print(f"  人工催收案件      : {fc_all}")
    print(f"  排班账号          : {total_staff_rpt}  ({' | '.join(dept_parts) if dept_parts else '-'})")

    print()
    print("=" * 70)
    print("  DEBUG 对账结束")
    print("=" * 70)
    print()


# ============================================================
#  12. main()
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="阿根廷每日案件及人力预估 V1")
    parser.add_argument("--dry-run", action="store_true", help="仅计算不发送")
    parser.add_argument("--text-only", action="store_true", help="纯文本降级")
    parser.add_argument("--list-chats", action="store_true", help="列出可用群聊")
    parser.add_argument("--chat-id", type=str, default=None, help="指定接收者")
    parser.add_argument("--weekend", action="store_true", help="周末离线模式，读取 weekend_attendance.json")
    args = parser.parse_args()

    print("=" * 50)
    print("  阿根廷每日案件及人力预估 V1")
    print("=" * 50)

    # ---- Date Debug ----
    cn_tz = timezone(timedelta(hours=8))
    ar_tz = timezone(timedelta(hours=-3))

    today = date.today()
    business_day = today
    forecast_date_ar = datetime.now(ar_tz).strftime("%Y-%m-%d")
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
    print(f"forecast_date (AR)  : {forecast_date_ar}")
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

    # ---- Step 2: 获取催收系统排班人力 ----
    if args.weekend:
        # 周末离线模式: 读取本地模板，跳过 API 请求
        print("[*] 周末离线模式: 跳过催收系统 API")
        staff_data = None  # 不需要 raw data
        attendance = load_weekend_attendance()
        if attendance is None:
            print("[!] 周末模板解析失败，退出")
            sys.exit(1)
        if not attendance:
            print("[!] 周末模板全为 0，将以 0 排班继续计算")
    else:
        dept_list = fetch_organ_page()
        staff_data = get_query_config(dept_list)
        attendance = normalize_attendance(staff_data)
        # AR_IN 从 2026-07-10 起负责 D-1 和 D0 阶段
        if attendance:
            print(f"  [过滤] AR_IN 已参与 D-1/D0 阶段，共 {len(attendance)} 条 attendance")

    # ---- Step 3: 数据标准化 ----
    cases = normalize_case(entry_data_list)

    # ---- Step 3.5: AI 持续跟进修正（阿根廷: D-1 固定公式） ----
    cases_pre_ai_totals = _snapshot_case_totals(cases)  # 快照：AI 修正前
    ai_data = fetch_ai_follow_cases()
    ai_correction = apply_ai_follow_correction(cases, ai_data)

    # ---- Step 4: 业务计算 ----
    forecasts, warnings_list = compute_forecast(cases, attendance)

    # 注入 forecast_case_raw（Quick BI 原始值，AI 修正前）
    # 阿根廷: AI 修正针对 D-1 新客 + D-1 老客
    for f in forecasts:
        f["forecast_case_raw"] = f["forecast_case"]
    for cust_cn, cust_en in [("新客", "NEW"), ("老客", "OLD")]:
        raw = ai_correction.get(cust_en, {}).get("quickbi_d_1", 0)
        for f in forecasts:
            if f["stage"] == "D-1" and f["customer"] == cust_cn:
                f["forecast_case_raw"] = raw

    # ---- Step 5: 组装 Report ----
    report = build_report(forecasts, warnings_list)
    report["ai_correction"] = ai_correction  # AI 跟进修正结果
    report["meta"]["business_day"] = str(business_day)  # 统一日报展示日期

    # 今日总览排班账号统计口径：
    #   统计日报展示阶段（D-1/D0/D1/S1/S2），排除 S3
    staff_totals = {}
    if attendance:
        for a in attendance:
            if a.get("stage") not in ENABLED_STAGES:
                continue
            dn = a.get("deptName", "")
            cnt = a.get("attendance", 0)
            if dn and cnt > 0:
                staff_totals[dn] = staff_totals.get(dn, 0) + cnt
    report["staff_totals"] = staff_totals

    # ---- Data Validation ----
    validate_data(entry_data_list, cases_pre_ai_totals, ai_correction,
                  forecasts, attendance, report)

    # ---- DEBUG: Forecast Check ----
    if DEBUG:
        ar_now = datetime.now(ar_tz).strftime("%Y-%m-%d %H:%M:%S")
        cn_now = datetime.now(cn_tz).strftime("%Y-%m-%d %H:%M:%S")
        print()
        print("=" * 40)
        print("Forecast Check")
        print("=" * 40)
        print(f"  中国当前时间        : {cn_now}")
        print(f"  阿根廷当前时间      : {ar_now}")
        print(f"  Forecast Date       : {forecast_date_ar}")
        print(f"  Quick BI due_date   : {quickbi_due_date}")
        print(f"  AI Follow Date      : {ai_call_day}")
        print("=" * 40)

    # ---- DEBUG 全链路对账 ----
    if DEBUG:
        debug_query_config(staff_data)
        debug_reconciliation(entry_data_list, cases_pre_ai_totals, cases,
                             ai_correction, staff_data, attendance,
                             forecasts, report)

    # ---- Step 6: 飞书发送 ----
    feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)
    chat_id = args.chat_id or FEISHU_CHAT_ID

    if args.list_chats:
        print("\n[*] 群聊列表:")
        for c in feishu.list_chats():
            print(f"  {c.get('name', '?')} — {c.get('chat_id', '?')}")

    if args.dry_run:
        # 阿根廷展示阶段: D-1/D0/D1/S1/S2
        hr_rows = [f for f in report["forecast"] if f.get("stage") in ENABLED_STAGES]
        stage_order = {s: i for i, s in enumerate(ENABLED_STAGES)}
        hr_rows.sort(key=lambda f: (
            stage_order.get(f.get("stage", ""), 99),
            f.get("customer") or "",
            f.get("order_type") or "",
        ))

        # ---- AI 持续跟进修正 ----
        ai_corr = report.get("ai_correction", {})
        if ai_corr:
            print("\n" + "=" * 24)
            print(f"AI持续跟进修正 (D-1老客×{AI_RATIO:.0%})")
            print("=" * 24)
            for idx, cust_label in enumerate(["NEW", "OLD"]):
                c = ai_corr.get(cust_label, {})
                if not c:
                    continue
                if idx > 0:
                    print("-" * 24)
                print(f"\n{cust_label}")
                print(f"  Quick BI D-1：{c.get('quickbi_d_1', 0)}")
                print(f"  AI继续跟进：{c.get('ai_follow', 0)}")
                print(f"  人工D-1：{c.get('manual_d_1', 0)}")
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
        for dn in ["AR_IN", "WB-FI-AR", "WB-CX-AR"]:
            if dn in staff_totals and staff_totals[dn] > 0:
                dept_parts.append(f"{dn}：{staff_totals[dn]}")
        total_staff = sum(staff_totals.values())
        staff_line = f"{total_staff}（{'｜'.join(dept_parts)}）" if dept_parts else str(total_staff)
        # D-1新客×100% + D-1老客×30%
        ai_follow_total = (ai_corr.get("NEW", {}).get("ai_follow", 0) +
                           ai_corr.get("OLD", {}).get("ai_follow", 0))
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
