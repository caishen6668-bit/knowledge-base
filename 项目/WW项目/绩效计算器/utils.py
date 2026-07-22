# -*- coding: utf-8 -*-
"""
WW 智能绩效与人员调配系统 — 工具函数

纯函数，无业务依赖。供 excel_reader、performance、main 等模块使用。
"""

import re
import os
from datetime import date, datetime
from pathlib import Path

import config


# ============================================================
# 推荐等级
# ============================================================

def get_recommendation_level(overall_score: float) -> str:
    """根据 OverallScore 返回推荐等级。

    S: >=90  (卓越)
    A: 80-89 (优秀)
    B: 70-79 (良好)
    C: 60-69 (合格)
    D: <60   (待提升)
    """
    if overall_score >= 90:
        return "S"
    elif overall_score >= 80:
        return "A"
    elif overall_score >= 70:
        return "B"
    elif overall_score >= 60:
        return "C"
    else:
        return "D"


RECOMMENDATION_LEVELS = {
    "S": "卓越",
    "A": "优秀",
    "B": "良好",
    "C": "合格",
    "D": "待提升",
}


# ============================================================
# 姓名与数值处理
# ============================================================

def clean_name(raw_name):
    """清洗姓名：去前后空格、去前缀"""
    if raw_name is None:
        return ""
    name = str(raw_name).strip()
    for prefix in config.NAME_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name


def parse_value(raw):
    """将日报文本单元格转为数值。

    '37.25%' -> 0.3725
    '20400.00' -> 20400.0
    '51' -> 51
    """
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if not s or s == "-":
        return 0.0
    if s.endswith("%"):
        try:
            return float(s.rstrip("%")) / 100.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


# ============================================================
# 日期推断
# ============================================================

def infer_date(filename, sheet_name):
    """从文件名和 sheet 名推断数据日期。

    文件名如 "10号.xlsx" → day=10
    sheet 名如 "data2026-07-12" → 导出日期 2026-07-12
    规则：day ≤ 导出日 → 同月；否则上一个月
    """
    m = re.match(config.DAILY_FILE_PATTERN, filename)
    if not m:
        raise ValueError(f"无法从文件名推断日期: {filename}")
    day = int(m.group(1))

    m2 = re.search(config.SHEET_DATE_PATTERN, sheet_name)
    if not m2:
        raise ValueError(f"无法从 sheet 名推断年月: {sheet_name}")
    export_date = datetime.strptime(m2.group(1), "%Y-%m-%d").date()

    if day <= export_date.day:
        data_date = date(export_date.year, export_date.month, day)
    else:
        if export_date.month == 1:
            data_date = date(export_date.year - 1, 12, day)
        else:
            data_date = date(export_date.year, export_date.month - 1, day)

    return data_date


# ============================================================
# 文件扫描
# ============================================================

def scan_daily_files(directory):
    """递归扫描目录及子目录下的日报文件（匹配 N号.xlsx 模式）。

    返回 (相对路径, 日数, 文件完整路径) 列表，按日数排序。
    """
    if not directory.exists():
        return []
    files = []
    for root, _dirs, filenames in os.walk(directory):
        for f in filenames:
            m = re.match(config.DAILY_FILE_PATTERN, f)
            if m:
                full_path = Path(root) / f
                rel_path = str(full_path.relative_to(directory))
                files.append((rel_path, int(m.group(1)), str(full_path)))
    files.sort(key=lambda x: x[1])
    return files


# ============================================================
# 经验评分（供 ability 和 optimizer 共用）
# ============================================================

def experience_score(days: int) -> float:
    """根据历史累计工作天数计算经验评分（分段线性插值）。

    映射: 0→0, 10→20, 30→40, 60→60, 120→80, 180+→100
    """
    import config
    points = config.EXPERIENCE_SCORE_POINTS
    if days <= points[0][0]:
        return float(points[0][1])
    if days >= points[-1][0]:
        return float(points[-1][1])

    for i in range(len(points) - 1):
        d1, s1 = points[i]
        d2, s2 = points[i + 1]
        if d1 <= days <= d2:
            if d2 == d1:
                return float(s1)
            return float(s1 + (days - d1) / (d2 - d1) * (s2 - s1))

    return 0.0
