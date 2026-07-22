"""
评分引擎 — 完全复刻 V1 评分规则，仅数据来源改变。

保持不变：达成率封顶、去最低一天、三期加权、出勤分、入职分、≤6天封顶、等级、排名。
唯一差异来自数据源：
  · 达成率 = (今日回款 / 今日入催) / target（V1 中 达成率 为 Excel 现成列，语义等同）
  · 工龄、是否在职 来自出勤 API（join_date / is_resigned），不再依赖花名册
  · 日订单催回率<1 的异常数据过滤保留（非评分规则，见 compute_period_perf 注释），
    字段由 API 的 total_repay_num / today_enter_collect_num 重建
"""

import pandas as pd

from . import config
from .utils import norm_name, to_num, work_days_since


# ============================================================
#  1. 业绩分规则（V1 原样）
# ============================================================
def perf_score(rate):
    pct = rate * 100
    if pct <= 60:
        return 0
    elif pct < 70:
        return (pct - 60) * 2
    elif pct < 80:
        return 20 + (pct - 70) * 1
    elif pct < 85:
        return 30 + (pct - 80) * 1
    elif pct < 90:
        return 35 + (pct - 85) * 1
    elif pct < 95:
        return 40 + (pct - 90) * 1
    elif pct < 100:
        return 45 + (pct - 95) * 1
    elif pct < 110:
        return 50 + (pct - 100) * 1
    elif pct < 120:
        return 60 + (pct - 110) * 1
    elif pct < 130:
        return 70 + (pct - 120) * 1
    else:
        return 80


# ============================================================
#  2. 单周期业绩聚合
# ============================================================
def _row_ach_rate(row):
    """达成率 = (回款 / 入催) / target"""
    enter = to_num(row.get(config.PERF_FIELD_ENTER))
    repay = to_num(row.get(config.PERF_FIELD_REPAY))
    target = to_num(row.get(config.PERF_FIELD_TARGET))
    if enter <= 0 or target <= 0:
        return float("nan")
    return (repay / enter) / target


def compute_period_perf(df):
    """
    单周期业绩 → 每人一行。

    Returns:
        DataFrame[agent, agent_id, agent_display, 达成率, 业绩分, 当前阶段]
    """
    empty_cols = ["agent", "agent_id", "agent_display", "达成率", "业绩分", "当前阶段"]
    if df is None or df.empty:
        return pd.DataFrame(columns=empty_cols)

    df = df.copy()
    df["ach_rate"] = df.apply(_row_ach_rate, axis=1)

    # 达成率封顶：D0/D1 → 1.5，其余 → 2.0
    stage = df[config.PERF_FIELD_STAGE].astype(str).str.strip().str.upper()
    mask_150 = stage.isin({s.upper() for s in config.STAGE_CAP_150})
    df.loc[mask_150, "ach_rate"] = df.loc[mask_150, "ach_rate"].clip(upper=config.CAP_150)
    df.loc[~mask_150, "ach_rate"] = df.loc[~mask_150, "ach_rate"].clip(upper=config.CAP_200)

    # 异常数据过滤（非评分规则）
    # 考核指标是「金额达成率」；此处过滤与评分无关，仅剔除异常工作日：
    # 员工缺勤时，其历史已分配案件仍可能自然还款，而订单无法重新分配，
    # 导致「日订单催回率」>= 100% 且回款金额仍计入该员工。这类数据不代表
    # 员工当天真实催收表现，故予以剔除，不参与绩效计算。
    enter_num = pd.to_numeric(df[config.PERF_FIELD_ENTER_NUM], errors="coerce")
    repay_num = pd.to_numeric(df[config.PERF_FIELD_REPAY_NUM], errors="coerce")
    df["daily_order_recovery_rate"] = repay_num / enter_num
    df = df[
        df["daily_order_recovery_rate"].isna()
        | (df["daily_order_recovery_rate"] < config.ORDER_RECOVERY_ANOMALY)
    ]

    df["agent_display"] = df[config.PERF_FIELD_NAME].astype(str).str.strip()
    df["agent"] = df["agent_display"].map(norm_name)
    df["agent_id"] = df[config.PERF_FIELD_ACCT]

    df = df.dropna(subset=["ach_rate"])
    df = df[df["agent"] != ""]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    # 去最低一天，其余取平均（V1 remove_min_and_mean）
    def remove_min_and_mean(s):
        if s.shape[0] == 1:
            return s.iloc[0]
        return s.drop(s.idxmin()).mean()

    agg = (df.groupby(["agent", "agent_id"])["ach_rate"]
             .apply(remove_min_and_mean)
             .reset_index()
             .rename(columns={"ach_rate": "达成率"}))
    agg["业绩分"] = agg["达成率"].apply(perf_score)

    # 当前阶段 = 最新一天的 逾期阶段 - 客群
    df_sorted = df.sort_values([config.PERF_FIELD_DATE], ascending=False)
    latest = df_sorted.groupby("agent").agg(
        stage=(config.PERF_FIELD_STAGE, "first"),
        cust=(config.PERF_FIELD_CUST, "first"),
        agent_display=("agent_display", "first"),
    ).reset_index()
    latest["当前阶段"] = latest["stage"].astype(str) + "-" + latest["cust"].astype(str)

    agg = agg.merge(latest[["agent", "当前阶段", "agent_display"]], on="agent", how="left")
    return agg[["agent", "agent_id", "agent_display", "达成率", "业绩分", "当前阶段"]]


# ============================================================
#  3. 三周期业绩合并
# ============================================================
def build_performance(perf_dfs):
    curr = compute_period_perf(perf_dfs.get("current"))
    prev1 = compute_period_perf(perf_dfs.get("prev1"))
    prev2 = compute_period_perf(perf_dfs.get("prev2"))

    curr = curr.rename(columns={"达成率": "达成率_本期", "业绩分": "业绩分_本期"})
    prev1 = prev1.rename(columns={"达成率": "达成率_近1期", "业绩分": "业绩分_近1期"})[
        ["agent", "达成率_近1期", "业绩分_近1期"]]
    prev2 = prev2.rename(columns={"达成率": "达成率_近2期", "业绩分": "业绩分_近2期"})[
        ["agent", "达成率_近2期", "业绩分_近2期"]]

    perf_all = curr.merge(prev1, on="agent", how="left").merge(prev2, on="agent", how="left")

    # 达成率显示为百分比字符串
    for col in ["达成率_本期", "达成率_近1期", "达成率_近2期"]:
        if col in perf_all.columns:
            perf_all[col] = (pd.to_numeric(perf_all[col], errors="coerce") * 100).round(2).astype(str) + "%"

    return perf_all


# ============================================================
#  4. 出勤聚合（含工龄 & 在职状态）
# ============================================================
def _att_score(absent, leave):
    score = config.ATT_BASE_SCORE - absent * config.ATT_ABSENT_PENALTY - leave * config.ATT_LEAVE_PENALTY
    return min(score, config.ATT_BASE_SCORE)


def build_attendance(att_df, today):
    cols = ["agent", "att_name", "缺勤次数", "出勤天数", "请假次数", "出勤分",
            "work_days", "入职分", "是否在职"]
    if att_df is None or att_df.empty:
        return pd.DataFrame(columns=cols)

    df = att_df.copy()
    status = df[config.ATT_FIELD_STATUS].astype(str).str.strip()
    df["is_absent"] = status.isin(config.ATT_ABSENT).astype(int)
    df["is_workday"] = status.isin(config.ATT_WORKDAY).astype(int)
    df["is_leave"] = status.isin(config.ATT_LEAVE).astype(int)
    df["att_name"] = df[config.ATT_FIELD_NAME].astype(str).str.strip()
    df["agent"] = df["att_name"].map(norm_name)

    # S3 等排除阶段：不计入出勤天数（缺勤/请假仍正常计）
    att_stage = df[config.ATT_FIELD_STAGE].astype(str).str.strip().str.upper()
    mask_excluded = att_stage.apply(
        lambda s: any(s.startswith(e.upper()) for e in config.EXCLUDED_STAGES)
    )
    df.loc[mask_excluded, "is_workday"] = 0

    agg = df.groupby("agent").agg(
        att_name=("att_name", "first"),
        缺勤次数=("is_absent", "sum"),
        出勤天数=("is_workday", "sum"),
        请假次数=("is_leave", "sum"),
        join_date=(config.ATT_FIELD_JOIN, "first"),
        is_resigned=(config.ATT_FIELD_RESIGNED, "first"),
    ).reset_index()

    agg["出勤分"] = agg.apply(lambda r: _att_score(r["缺勤次数"], r["请假次数"]), axis=1)

    # 工龄 & 入职分（不依赖花名册）
    agg["work_days"] = agg["join_date"].apply(lambda j: work_days_since(j, today))
    months = (agg["work_days"] // 30).astype(int)
    agg["入职分"] = 1 + (months - 1).clip(lower=0) + (months - 6).clip(lower=0) * 0.5

    agg["是否在职"] = agg["is_resigned"].astype(str).str.strip()
    return agg[cols]


# ============================================================
#  5. 合并 + 加权 + 综合评分
# ============================================================
def _weighted_score(row):
    w = config.WEIGHTS
    pairs = [
        (row.get("业绩分_本期"), w["current"]),
        (row.get("业绩分_近1期"), w["prev1"]),
        (row.get("业绩分_近2期"), w["prev2"]),
    ]
    scores = [(s, wt) for s, wt in pairs if pd.notna(s)]
    if not scores:
        return 0.0
    w_sum = sum(wt for _, wt in scores)
    return sum(s * wt for s, wt in scores) / w_sum


def _grade_and_coef(score):
    for threshold, grade, coef in config.GRADE_TABLE:
        if score >= threshold:
            return grade, coef
    return "D", 1.0


def _distance_to_next_grade(score):
    """距下一（更高）等级还差多少分。已是最高则提示。仅展示，不参与评分。"""
    higher = [(t, g) for t, g, _ in config.GRADE_TABLE if t > score]
    if not higher:
        return "已达最高等级"
    t, g = min(higher, key=lambda x: x[0])
    return f"距{g}还差{round(t - score, 2)}分"


def combine(perf_all, att_agg):
    df = perf_all.merge(att_agg, on="agent", how="left")

    for col in ["缺勤次数", "请假次数", "出勤天数"]:
        df[col] = df.get(col, 0)
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["出勤分"] = pd.to_numeric(df.get("出勤分"), errors="coerce").fillna(0)
    df["入职分"] = pd.to_numeric(df.get("入职分"), errors="coerce").fillna(0)
    df["是否在职"] = df.get("是否在职").fillna("离职/未知")

    # 催员姓名：优先业绩原名，缺失回落出勤名/key
    df["催员姓名"] = df["agent_display"].fillna(df.get("att_name")).fillna(df["agent"])
    df["组员账号"] = df.get("agent_id")

    # 出勤 ≤6 天 → 业绩分封顶 45
    mask_le6 = df["出勤天数"] <= config.LOW_ATTENDANCE_DAYS
    cap = config.LOW_ATTENDANCE_PERF_CAP
    for col in ["业绩分_本期", "业绩分_近1期", "业绩分_近2期"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df.loc[mask_le6 & df[col].notna(), col] = df.loc[mask_le6 & df[col].notna(), col].clip(upper=cap)
            df.loc[mask_le6 & df[col].isna(), col] = cap

    df["业绩分_三期加权"] = df.apply(_weighted_score, axis=1)
    df["综合评分"] = df["业绩分_三期加权"] + df["出勤分"] + df["入职分"]

    # 等级 & 奖金系数（仅在职）
    def lv(row):
        if str(row["是否在职"]) != config.ON_JOB_VALUE:
            return "", 0.0
        return _grade_and_coef(row["综合评分"])

    lvs = df.apply(lv, axis=1)
    df["最终等级"] = lvs.apply(lambda x: x[0])
    df["奖金系数"] = lvs.apply(lambda x: x[1])

    # V2.1 评级组成（纯展示派生列，不改变任何评分结果）
    df["业绩贡献"] = df["业绩分_三期加权"]
    df["出勤贡献"] = df["出勤分"]
    df["工龄贡献"] = df["入职分"]
    df["距上一等级"] = df.apply(
        lambda r: _distance_to_next_grade(r["综合评分"]) if r["最终等级"] != "" else "",
        axis=1,
    )

    # 排名（仅在职）
    active = df[df["是否在职"] == config.ON_JOB_VALUE].copy()
    active = active.sort_values("综合评分", ascending=False).reset_index(drop=True)
    active["最终名次"] = active.index + 1
    df = df.merge(active[["agent", "最终名次"]], on="agent", how="left")

    df["在职标记"] = (df["是否在职"] == config.ON_JOB_VALUE).astype(int)
    df = df.sort_values(by=["在职标记", "最终名次"], ascending=[False, True]).reset_index(drop=True)
    return df


def run(perf_dfs, att_df, today):
    """完整评分流程：业绩 → 出勤 → 合并综合。返回 df_final。"""
    perf_all = build_performance(perf_dfs)
    att_agg = build_attendance(att_df, today)
    return combine(perf_all, att_agg)
