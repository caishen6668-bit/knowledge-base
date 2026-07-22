# -*- coding: utf-8 -*-
"""
WW 智能绩效与人员调配系统 — 调岗历史

记录和查询人员队列调配历史，用于推荐时避免短期内重复调岗。

TODO: 后续设计存储方案（Excel/CSV/SQLite）。
"""


def record_transfer(staff_id, from_queue, to_queue, date, reason):
    """记录一次调岗。

    TODO: 实现持久化存储。
    """
    raise NotImplementedError("调岗历史模块尚未实现")


def get_history(staff_id=None, days=30):
    """查询调岗历史。

    参数：
        staff_id: 员工工号，None = 全部
        days: 回溯天数

    返回：
        list[dict]

    TODO: 实现查询逻辑。
    """
    raise NotImplementedError("调岗历史模块尚未实现")
