# -*- coding: utf-8 -*-
"""
WW 智能绩效与人员调配系统 — 绩效计算

方案选择 → 档位匹配 → 日绩效计算。
"""

import config
from models import PerfResult


# ============================================================
# 方案选择
# ============================================================

def get_scheme(data_date):
    """根据数据日期自动选择生效日期 <= data_date 的最新一版方案。

    新增方案时只需在 config.SCHEME_VERSIONS 追加一行，本函数无需修改。

    返回：
        (scheme_dict, scheme_name)
    """
    best = None
    for eff_date, scheme_dict, scheme_name in config.SCHEME_VERSIONS:
        if eff_date <= data_date:
            best = (scheme_dict, scheme_name)
        else:
            break
    if best is None:
        raise ValueError(
            f"{data_date} 没有可用绩效方案（所有方案的生效日期均晚于该日期）。\n"
            f"请先在 SCHEME_VERSIONS 中新增对应版本的绩效方案。"
        )
    return best


# ============================================================
# 档位匹配
# ============================================================

def lookup_tier(rate, tiers):
    """根据催回率查找匹配的档位。

    匹配规则：min <= rate < max（最后一档：min <= rate）
    返回：(coefficient, amount)
    """
    for i, (lo, hi, coeff, amount) in enumerate(tiers):
        if i == len(tiers) - 1:
            if rate >= lo:
                return coeff, amount
        else:
            if lo <= rate < hi:
                return coeff, amount
    return 0, 0


# ============================================================
# 每日绩效计算
# ============================================================

# 催回率计算公式注册表
def _calc_amount_rate(rec):
    """直接使用债务汇总维度·金额催回率（D0 等默认公式）"""
    return rec.amount_rate


def _calc_cross_hybrid(rec):
    """回款总额（债务汇总）÷ 应催总额（队列新案）（S1 等）"""
    if rec.due_amount > 0:
        return rec.repaid_amount / rec.due_amount
    return 0.0


RATE_FORMULAS = {
    "amount_rate": _calc_amount_rate,
    "cross_hybrid": _calc_cross_hybrid,
}


def calculate_day(data_date, records):
    """计算一天的绩效。

    参数：
        data_date: date
        records: list[StaffRecord]

    返回：
        (results, scheme_name)
        results: list[PerfResult]
    """
    scheme, scheme_name = get_scheme(data_date)
    results = []

    for rec in records:
        # 休息
        if rec.is_rest:
            results.append(PerfResult(
                name=rec.name,
                staff_id=rec.staff_id,
                queue=rec.queue,
                stage="",
                rate=0,
                coefficient=0,
                amount="休",
                is_rest=True,
            ))
            continue

        # 队列 → 阶段映射
        stage = config.QUEUE_STAGE_MAP.get(rec.queue, rec.queue)

        # 选择催回率公式
        formula_name = config.RATE_RULES.get(stage, "amount_rate")
        rate = RATE_FORMULAS[formula_name](rec)

        # 查找档位
        tiers = scheme.get(stage)
        if tiers is None:
            coeff, amount = 0, 0
        else:
            coeff, amount = lookup_tier(rate, tiers)

        results.append(PerfResult(
            name=rec.name,
            staff_id=rec.staff_id,
            queue=rec.queue,
            stage=stage,
            rate=rate,
            coefficient=coeff,
            amount=amount,
            is_rest=False,
        ))

    return results, scheme_name
