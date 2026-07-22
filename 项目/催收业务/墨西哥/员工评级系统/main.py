"""
员工评级系统 V2.1 — 主入口。

流程：自动/指定周期 → 拉业绩(三期) → 拉出勤 → 评分 → 导出 Summary + 中文/Mexico Excel。
数据全部来自 Quick BI OpenAPI，无 Excel 输入、无中间 Excel。

用法：
    python -m employee_rating_v2.main                 # 用系统当天日期
    python -m employee_rating_v2.main --date 2026-07-15  # 指定评估日期（补跑历史）
    python -m employee_rating_v2.main --refresh          # 忽略缓存，强制重新取数
"""

import argparse
import time
from datetime import datetime

from . import config, fetch, exporter, rating_engine
from .period import compute_periods
from .utils import setup_logging, log, ok, error


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description="员工评级系统 V2.1（Quick BI 直连）")
    ap.add_argument("--date", help="指定评估日期 YYYY-MM-DD（默认系统当天），用于补跑历史")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，强制重新请求 Quick BI")
    return ap.parse_args(argv)


def run(eval_date, refresh=False):
    t0 = time.time()
    run_date_str = eval_date.strftime("%Y%m%d")

    setup_logging(config.LOG_DIR, run_date_str)

    log("=" * 48)
    log(f"开始运行  员工评级 {config.VERSION}  (发布日期 {config.RELEASE_DATE})")
    log(f"国家/部门：{config.COUNTRY}/{config.DEPARTMENT}")
    log(f"评估日期：{eval_date.strftime('%Y-%m-%d')}   强制刷新：{refresh}")

    periods = compute_periods(eval_date)
    for p in periods:
        log(f"  {p['name']}：{p['start']} ~ {p['end']}（{p['label']}）")

    # 业绩
    log("\n获取业绩数据...")
    perf_dfs = fetch.fetch_performance(periods, run_date_str, refresh=refresh)

    # 出勤
    log("\n获取出勤数据...")
    att_df = fetch.fetch_attendance(periods[0], run_date_str, refresh=refresh)

    # 评分
    log("\n评分开始...")
    df_final = rating_engine.run(perf_dfs, att_df, eval_date)
    n_active = int((df_final["是否在职"] == config.ON_JOB_VALUE).sum())
    ok(f"评分完成（在职 {n_active} 人 / 共 {len(df_final)} 人）")

    # 导出
    log("\n导出Excel...")
    fetch_time = fetch.data_fetch_time(run_date_str)   # 实际 Quick BI 取数时间（缓存命中=首次取数）
    gen_time = datetime.now()                            # 数据生成时间（本次报表导出时刻）
    zh_path, ex_path = exporter.export(
        df_final, eval_date, periods[0]["label"], gen_time=gen_time, fetch_time=fetch_time
    )
    ok("导出成功（含 Summary 首页）")
    log(f"  中文版 : {zh_path}")
    log(f"  {config.EXPORT_LANG_LABELS.get(config.EXPORT_LANGUAGE, config.EXPORT_LANGUAGE)} : {ex_path}")

    elapsed = time.time() - t0
    ok(f"完成  总耗时 {elapsed:.1f} 秒")
    log("=" * 48)
    return df_final


def main(argv=None):
    args = _parse_args(argv)
    if args.date:
        try:
            eval_date = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"--date 格式错误：{args.date}，应为 YYYY-MM-DD")
    else:
        eval_date = datetime.now()

    try:
        return run(eval_date, refresh=args.refresh)
    except Exception as e:
        error(f"运行失败：{e}")
        raise


if __name__ == "__main__":
    main()
