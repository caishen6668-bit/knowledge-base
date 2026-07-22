# -*- coding: utf-8 -*-
"""
WW 智能绩效与人员调配系统 — 数据模型

所有模块间数据交换使用这些结构，保证接口稳定。
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class StaffRecord:
    """单条日报员工记录（从日报读取后传递给绩效计算）"""
    name: str
    staff_id: str
    dept: str
    queue: str
    coverage: float
    amount_rate: float       # 债务汇总维度·金额催回率
    repaid_amount: float     # 债务汇总维度·回款总额
    due_amount: float        # 队列新案维度·应催总额
    is_rest: bool = False    # O列分案户数=0 → 休息


@dataclass
class PerfResult:
    """单人单日绩效计算结果"""
    name: str
    staff_id: str
    queue: str
    stage: str
    rate: float
    coefficient: float
    amount: Any              # float 或 "休"
    is_rest: bool = False


@dataclass
class DayResult:
    """单日处理结果"""
    data_date: date
    results: list          # list[PerfResult]
    scheme_name: str
