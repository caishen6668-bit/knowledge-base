# -*- coding: utf-8 -*-
"""
WW 智能绩效与人员调配系统 — 排班 API

从催收系统「机构管理 → 排班管理」获取真实排班数据。

接口: POST vitech-collect-gateway/collect/staff/staffSchedulePage
认证: X-Fixed-Token (复用已有 Token)

特性:
  - 整月加载 + 内存缓存 (load_month_schedule)
  - 按日查询 (get_day_schedule) — 不重复请求
  - 本地文件缓存 (接口失败时降级)
  - Token 失效 / 网络异常 / 空数据 处理
  - 纯数据层，不参与任何业务计算
"""

import json
import os
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# ============================================================
# 配置
# ============================================================

# 催收系统 API 地址（墨西哥 — maxicredito.loan）
_BASE_URL = os.environ.get(
    "COLLECT_BASE_URL",
    "https://loan-collect.maxicredito.loan/vitech-collect-gateway",
)
_SCHEDULE_URL = f"{_BASE_URL}/collect/staff/staffSchedulePage"

# 认证 Token
_TOKEN = os.environ.get(
    "MEX_COLLECT_TOKEN",
    "",
)

# 请求超时（秒）
_REQUEST_TIMEOUT = 30

# 缓存目录
_CACHE_DIR = Path(__file__).parent / ".schedule_cache"

# API 时区 (UTC-6, 墨西哥中部时间)
_API_TZ = timezone(timedelta(hours=-6))

# scheduleStatus → on_duty 映射
#   1  = 上班 (WORK)
#   0  = 休息
#  -1  = 请假
#   2  = 其他
#   3  = 其他
_WORK_STATUS = {1}  # 只有 1 表示上班

# ============================================================
# 内部缓存
# ============================================================

# 内存缓存: { "2026-07": [schedule_entries] }
_cache = {}

# ============================================================
# API 请求
# ============================================================

def _build_headers():
    """构建认证请求头。"""
    return {
        "Content-Type": "application/json",
        "X-CHOICE-TAG": "ms",
        "X-END-LANGUAGE": "zh_cn",
        "X-Fixed-Token": _TOKEN,
    }


def _fetch_raw_schedule(year: int, month: int) -> Optional[list]:
    """从 API 获取整月排班原始数据。

    返回:
        list[dict]: API 原始 rows，失败返回 None
    """
    # 计算时间范围（整月，API 时区）
    start_dt = datetime(year, month, 1, tzinfo=_API_TZ)
    # 下个月第一天
    if month == 12:
        end_dt = datetime(year + 1, 1, 1, tzinfo=_API_TZ)
    else:
        end_dt = datetime(year, month + 1, 1, tzinfo=_API_TZ)

    now = datetime.now(_API_TZ)
    date_iso = now.strftime("%Y-%m-%dT%H:%M:%S")

    body = {
        "region": "",
        "date": date_iso,
        "scheduleStart": int(start_dt.timestamp() * 1000),
        "scheduleEnd": int(end_dt.timestamp() * 1000),
        "page": 1,
        "rows": 500,                    # 一次拉取全部
        "staffStatus": "1",             # 只取在职
        "secondLevelDeptId": "",
        "thirdLevelDeptId": "",
        "fourthLevelDeptId": "",
        "staffName": "",
        "overdueLevelIdList": [],
        "scheduleStatusList": [],
    }

    headers = _build_headers()

    try:
        resp = requests.post(
            _SCHEDULE_URL,
            headers=headers,
            json=body,
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        print(f"  [!] 排班 API: 请求超时 ({_REQUEST_TIMEOUT}s)")
        return None
    except requests.exceptions.ConnectionError as e:
        print(f"  [!] 排班 API: 连接失败 — {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"  [!] 排班 API: 请求异常 — {e}")
        return None

    # HTTP 状态码检查
    if resp.status_code != 200:
        print(f"  [!] 排班 API: HTTP {resp.status_code}")
        if resp.status_code in (401, 403):
            print(f"  [!] Token 可能已失效，请检查 MEX_COLLECT_TOKEN")
        return None

    # JSON 解析
    try:
        data = resp.json()
    except (ValueError, AttributeError):
        print(f"  [!] 排班 API: JSON 解析失败")
        return None

    # 业务状态检查
    if not isinstance(data, dict):
        print(f"  [!] 排班 API: 返回格式异常")
        return None

    result_code = data.get("resultCode")
    if result_code != 1:
        print(f"  [!] 排班 API: resultCode={result_code}, message={data.get('message')}")
        return None

    rows = data.get("data", {}).get("rows", [])
    if not rows:
        print(f"  [!] 排班 API: 返回空数据")
        return None

    # 多页处理
    total_page = data.get("data", {}).get("totalPage", 1)
    if total_page > 1:
        all_rows = list(rows)
        for p in range(2, total_page + 1):
            body["page"] = p
            try:
                resp = requests.post(
                    _SCHEDULE_URL,
                    headers=headers,
                    json=body,
                    timeout=_REQUEST_TIMEOUT,
                )
                page_data = resp.json()
                all_rows.extend(page_data.get("data", {}).get("rows", []))
            except Exception:
                print(f"  [!] 排班 API: 第 {p} 页获取失败，使用已获取数据")
                break
        rows = all_rows

    return rows


# ============================================================
# 缓存管理
# ============================================================

def _cache_file_path(year: int, month: int) -> Path:
    """获取缓存文件路径。"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"schedule_{year}_{month:02d}.json"


def _load_cache(year: int, month: int) -> Optional[list]:
    """从本地文件加载缓存。"""
    path = _cache_file_path(year, month)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 检查缓存年龄（不超过 7 天）
        cached_at = data.get("_cached_at", 0)
        if time.time() - cached_at > 7 * 24 * 3600:
            return None
        return data.get("rows", [])
    except (json.JSONDecodeError, IOError):
        return None


def _save_cache(year: int, month: int, rows: list):
    """保存缓存到本地文件。"""
    path = _cache_file_path(year, month)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {"_cached_at": time.time(), "rows": rows},
                f,
                ensure_ascii=False,
                default=str,
            )
    except IOError as e:
        print(f"  [!] 缓存写入失败: {e}")


# ============================================================
# 数据解析
# ============================================================

def _parse_schedule_rows(raw_rows: list, year: int, month: int) -> list:
    """将 API 原始行解析为统一排班条目。

    每条原始记录的 scheduleRespDtos 是一个月的排班数组，
    展开为每天的独立条目。

    返回:
        list[dict]: 每条包含 staff_id, name, stage, schedule_date, on_duty, status
    """
    entries = []
    for row in raw_rows:
        staff_no = str(row.get("staffNo", "")).strip()
        staff_name = str(row.get("staffName", "")).strip()
        staff_id_api = str(row.get("staffId", ""))
        overdue_level = row.get("overdueLevelId", 0)

        schedules = row.get("scheduleRespDtos", [])
        if not schedules:
            continue

        for sched in schedules:
            ts_ms = int(sched.get("scheduleTime", 0))
            if ts_ms == 0:
                continue
            ts = ts_ms / 1000
            try:
                sched_dt = datetime.fromtimestamp(ts, tz=_API_TZ)
            except (ValueError, OSError):
                continue

            # 只保留指定月份的排班
            if sched_dt.year != year or sched_dt.month != month:
                continue

            status_code = sched.get("scheduleStatus", 0)
            on_duty = status_code in _WORK_STATUS

            entries.append({
                "staff_id": staff_no,       # API 工号 (如 A_Antonio0057)
                "name": staff_name,         # 姓名
                "stage": "",                # 排班 API 不含阶段信息，由 optimizer 填充
                "schedule_date": sched_dt.strftime("%Y-%m-%d"),
                "on_duty": on_duty,
                "status": "WORK" if on_duty else "REST",
                "_api_staff_id": staff_id_api,
                "_overdue_level": overdue_level,
            })

    return entries


# ============================================================
# 公开接口
# ============================================================

def load_month_schedule(year: int, month: int, force_refresh: bool = False) -> list:
    """加载整月排班数据。

    优先使用内存缓存，其次本地文件缓存，最后请求 API。
    一次请求覆盖整月，后续 get_day_schedule 不重复请求。

    参数:
        year:  年 (如 2026)
        month: 月 (1-12)
        force_refresh: 强制刷新，忽略缓存

    返回:
        list[dict]: 整月排班条目，每条包含:
            staff_id, name, stage, schedule_date, on_duty, status
    """
    cache_key = f"{year}-{month:02d}"

    # 1. 内存缓存
    if not force_refresh and cache_key in _cache:
        return _cache[cache_key]

    # 2. 本地文件缓存
    if not force_refresh:
        cached = _load_cache(year, month)
        if cached is not None:
            entries = _parse_schedule_rows(cached, year, month)
            _cache[cache_key] = entries
            return entries

    # 3. 请求 API
    raw = _fetch_raw_schedule(year, month)
    if raw is None:
        # API 失败 → 尝试降级到本地缓存（即使过期）
        cached = _load_cache(year, month)
        if cached is not None:
            print(f"  [i] 使用过期缓存")
            entries = _parse_schedule_rows(cached, year, month)
            _cache[cache_key] = entries
            return entries
        # 彻底失败
        print(f"  [XX] 排班 API: 无法获取数据，且无缓存可用")
        return []

    # 4. 保存缓存 + 解析
    _save_cache(year, month, raw)
    entries = _parse_schedule_rows(raw, year, month)
    _cache[cache_key] = entries
    return entries


def get_day_schedule(schedule_date, year: int = None, month: int = None) -> list:
    """获取指定日期的排班数据。

    参数:
        schedule_date: date 或 str ("2026-07-20") — 查询日期
        year:  可选，手动指定年月（默认从 schedule_date 推断）
        month: 可选

    返回:
        list[dict]: 当日排班条目（on_duty=True 为在岗，False 为休息）
    """
    if isinstance(schedule_date, str):
        schedule_date = date.fromisoformat(schedule_date)

    if year is None:
        year = schedule_date.year
    if month is None:
        month = schedule_date.month

    month_entries = load_month_schedule(year, month)
    if not month_entries:
        return []

    target_str = schedule_date.strftime("%Y-%m-%d")
    return [e for e in month_entries if e["schedule_date"] == target_str]


def get_staff_list(year: int = None, month: int = None) -> list:
    """获取排班系统中的所有员工信息。

    返回:
        list[dict]: 去重后的员工列表，每条包含 name, staff_id, _overdue_level
    """
    if year is None:
        now = datetime.now(_API_TZ)
        year, month = now.year, now.month

    entries = load_month_schedule(year, month)
    seen = set()
    staff = []
    for e in entries:
        if e["staff_id"] not in seen:
            seen.add(e["staff_id"])
            staff.append({
                "staff_id": e["staff_id"],
                "name": e["name"],
                "_overdue_level": e["_overdue_level"],
            })
    return staff


def clear_cache():
    """清除内存缓存（用于测试或强制刷新）。"""
    _cache.clear()


# ============================================================
# 姓名匹配
# ============================================================

def normalize_name(name: str) -> str:
    """姓名归一化：去重音符号、去空格、转小写。

    用于跨系统姓名匹配（排班 API vs 日报）。
    'Víctor Lerma' → 'victorlerma'
    """
    # NFKD 分解重音符号（如 í → i + ́ ）
    normalized = unicodedata.normalize("NFKD", name)
    # 去掉变音符号（non-spacing marks）
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    # 去空格、转小写
    return ascii_name.replace(" ", "").lower()


def match_staff_id(api_name: str, name_map: dict = None) -> str:
    """根据排班 API 姓名查找对应的日报工号。

    优先使用 name_map，其次尝试 config.SCHEDULER_NAME_TO_STAFF_ID。

    参数:
        api_name: 排班系统中的姓名 (如 'Karime Resendiz')
        name_map: 可选，{api_name: staff_id} 映射表

    返回:
        str: 日报工号 (如 'feimi006')，未找到返回空字符串
    """
    if name_map is None:
        try:
            import config
            name_map = config.SCHEDULER_NAME_TO_STAFF_ID
        except (ImportError, AttributeError):
            name_map = {}

    # 精确匹配
    if api_name in name_map:
        return name_map[api_name]

    # 归一化匹配
    norm_api = normalize_name(api_name)
    for mapped_name, staff_id in name_map.items():
        if normalize_name(mapped_name) == norm_api:
            return staff_id

    return ""
