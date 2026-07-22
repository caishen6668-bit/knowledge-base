"""
导出层 — Summary 首页 + 评级明细。中文版 + 附加语言版（默认 Mexico 西语）。
文件名：最终评级结果_YYYY.MM.DD_{语言标签}.xlsx
字段与列顺序沿用 V1，末尾追加 V2.1 评级组成列；不改变既有字段与结果。
"""

import os

import pandas as pd

from . import config

# 等级展示顺序
_GRADE_ORDER = ["SSS", "SS", "S", "AA", "A", "B", "C", "D"]

# Summary 指标标签（zh / 附加语言）
_SUMMARY_LABELS = {
    "zh": {
        "period": "本周期", "total": "总人数", "active": "在职人数", "resigned": "离职人数",
        "grade_cnt": "{g}人数", "grade_pct": "{g}占比",
        "avg_total": "平均综合评分", "avg_perf": "平均业绩分（三期加权）",
        "avg_att": "平均出勤分", "avg_entry": "平均入职分",
        "avg_ach": "平均达成率（本期）", "max_total": "最高综合分", "min_total": "最低综合分",
        "gen_time": "数据生成时间", "fetch_time": "Quick BI 取数时间",
        "metric": "指标", "value": "数值",
    },
    "mx": {
        "period": "Ciclo", "total": "Total de personas", "active": "Activos", "resigned": "Retirados",
        "grade_cnt": "Cantidad {g}", "grade_pct": "Proporción {g}",
        "avg_total": "Puntaje total promedio", "avg_perf": "Puntaje de desempeño promedio (ponderado)",
        "avg_att": "Puntaje de asistencia promedio", "avg_entry": "Puntaje de antigüedad promedio",
        "avg_ach": "Tasa de logro promedio (ciclo actual)", "max_total": "Puntaje total máximo",
        "min_total": "Puntaje total mínimo",
        "gen_time": "Hora de generación", "fetch_time": "Hora de extracción de Quick BI",
        "metric": "Métrica", "value": "Valor",
    },
}


def _summary_labels(lang):
    return _SUMMARY_LABELS.get(lang, _SUMMARY_LABELS["zh"])


def build_summary(df, period_label, lang="zh", gen_time=None, fetch_time=None):
    """构建 Summary DataFrame（两列：指标 / 数值）。仅统计，不影响评分。"""
    L = _summary_labels(lang)
    active = df[df["是否在职"] == config.ON_JOB_VALUE]
    n_total = len(df)
    n_active = len(active)
    n_resigned = n_total - n_active

    # 本期达成率（字符串 "133.2%" → 数值）
    ach = pd.to_numeric(
        active["达成率_本期"].astype(str).str.rstrip("%"), errors="coerce"
    ) if "达成率_本期" in active.columns else pd.Series(dtype=float)

    def _fmt_time(t):
        return t.strftime("%Y-%m-%d %H:%M:%S") if t is not None else "-"

    rows = [
        (L["period"], period_label),
        (L["gen_time"], _fmt_time(gen_time)),
        (L["fetch_time"], _fmt_time(fetch_time)),
        (L["total"], n_total),
        (L["active"], n_active),
        (L["resigned"], n_resigned),
    ]
    # 各等级人数
    for g in _GRADE_ORDER:
        rows.append((L["grade_cnt"].format(g=g), int((active["最终等级"] == g).sum())))
    # 平均分
    rows += [
        (L["avg_total"], round(active["综合评分"].mean(), 2) if n_active else 0),
        (L["avg_perf"], round(active["业绩分_三期加权"].mean(), 2) if n_active else 0),
        (L["avg_att"], round(active["出勤分"].mean(), 2) if n_active else 0),
        (L["avg_entry"], round(active["入职分"].mean(), 2) if n_active else 0),
        (L["avg_ach"], f"{ach.mean():.2f}%" if len(ach.dropna()) else "-"),
        (L["max_total"], round(active["综合评分"].max(), 2) if n_active else 0),
        (L["min_total"], round(active["综合评分"].min(), 2) if n_active else 0),
    ]
    # 各等级占比
    for g in _GRADE_ORDER:
        c = int((active["最终等级"] == g).sum())
        pct = f"{c / n_active * 100:.1f}%" if n_active else "0.0%"
        rows.append((L["grade_pct"].format(g=g), pct))

    return pd.DataFrame(rows, columns=[L["metric"], L["value"]])


def _write_book(path, summary_df, detail_df):
    """Summary 作为第一个 sheet，评级明细随后。"""
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        summary_df.to_excel(w, sheet_name="Summary", index=False)
        detail_df.to_excel(w, sheet_name="评级明细", index=False)


def export(df_final, run_date, period_label, gen_time=None, fetch_time=None):
    """
    Args:
        gen_time:   数据生成时间（报表导出时刻）。
        fetch_time: Quick BI 实际取数时间（缓存命中则为首次取数时间）。

    Returns:
        (zh_path, export_lang_path)
    """
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    stamp = run_date.strftime("%Y.%m.%d")

    cols_zh = [c for c in config.COLS_ZH if c in df_final.columns]

    # ---- 中文版 ----
    zh_label = config.EXPORT_LANG_LABELS.get(config.LANGUAGE, "中文版")
    zh_path = os.path.join(config.OUTPUT_DIR, f"最终评级结果_{stamp}_{zh_label}.xlsx")
    zh_summary = build_summary(df_final, period_label, config.LANGUAGE, gen_time, fetch_time)
    _write_book(zh_path, zh_summary, df_final[cols_zh])

    # ---- 附加语言版（默认 Mexico 西语）----
    ex_lang = config.EXPORT_LANGUAGE
    ex_label = config.EXPORT_LANG_LABELS.get(ex_lang, ex_lang)
    ex_map = config.EXPORT_MAPS.get(ex_lang, {})
    ex_path = os.path.join(config.OUTPUT_DIR, f"最终评级结果_{stamp}_{ex_label}.xlsx")

    detail_ex = df_final[cols_zh].copy()
    detail_ex.columns = [ex_map.get(c, c) for c in cols_zh]
    ex_summary = build_summary(df_final, period_label, ex_lang, gen_time, fetch_time)
    _write_book(ex_path, ex_summary, detail_ex)

    return zh_path, ex_path
