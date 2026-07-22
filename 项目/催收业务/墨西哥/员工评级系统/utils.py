"""通用工具：日志、安全转换、日期、姓名归一。"""

import logging
import os
from datetime import datetime


# ============================================================
#  日志（控制台 + 文件）
# ============================================================
_logger = logging.getLogger("employee_rating")


def setup_logging(log_dir, run_date_str):
    """初始化日志：同时输出控制台与 logs/employee_rating_YYYYMMDD.log。"""
    os.makedirs(log_dir, exist_ok=True)
    _logger.setLevel(logging.INFO)
    _logger.handlers.clear()

    fh = logging.FileHandler(
        os.path.join(log_dir, f"employee_rating_{run_date_str}.log"), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))

    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(message)s"))

    _logger.addHandler(fh)
    _logger.addHandler(sh)
    return _logger


def _ensure_handler():
    """未显式 setup 时（如单模块自测）也能打印。"""
    if not _logger.handlers:
        _logger.setLevel(logging.INFO)
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(sh)


def log(msg):
    """普通信息"""
    _ensure_handler()
    _logger.info(msg)


def ok(msg):
    """成功标记 √"""
    _ensure_handler()
    _logger.info(f"√ {msg}")


def warn(msg):
    """警告标记 !"""
    _ensure_handler()
    _logger.warning(f"! {msg}")


def error(msg):
    """错误标记 ×"""
    _ensure_handler()
    _logger.error(f"× {msg}")


# ============================================================
#  安全数值转换
# ============================================================
def to_num(val):
    """安全转 float；'-'/''/None → 0.0"""
    if val is None or val == "-" or val == "":
        return 0.0
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


# ============================================================
#  日期
# ============================================================
def parse_ymd(s):
    """'20260429' → datetime；失败返回 None"""
    try:
        return datetime.strptime(str(s).strip(), "%Y%m%d")
    except (ValueError, TypeError):
        return None


def work_days_since(join_date, today):
    """工龄天数 = today - join_date；无法解析返回 0"""
    d = parse_ymd(join_date)
    if d is None:
        return 0
    return max((today - d).days, 0)


# ============================================================
#  姓名归一（与 V1 一致：strip + lower）
# ============================================================
def norm_name(s):
    return str(s).strip().lower()
