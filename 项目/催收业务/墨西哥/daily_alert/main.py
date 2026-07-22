"""
首逾智能预警系统 — CLI 入口

用法:
  python main.py                                    # 当天，墨西哥+阿根廷
  python main.py --country MX                      # 仅墨西哥
  python main.py --country AR                      # 仅阿根廷
  python main.py --date 2026-07-07                  # 指定日期
  python main.py --dry-run                          # 仅计算，不发送飞书
  python main.py --text-only                        # 纯文本降级发送
  python main.py --list-chats                       # 列出可用群聊
  python main.py --stage D1                         # 分析 D1 阶段
  python main.py --uat                              # UAT 自动验收

也可以从上级目录运行:
  python -m daily_alert.main --dry-run
"""

# ---- Windows UTF-8: 必须放在所有 import 之前 ----
import io as _io
import sys as _sys
if _sys.platform == "win32" and hasattr(_sys.stdout, 'buffer'):
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")

import argparse
import sys
import traceback
from collections import defaultdict
from datetime import datetime, date, timedelta

from . import config
from .quickbi import (
    fetch_overdue_data, fetch_case_volumes,
    warm_cache_recovery, warm_cache_cases,
    get_cache_stats, reset_cache,
)
from .alert_engine import run_full_analysis
from .report import build_report
from .field_inspector import run_field_inspection, run_field_verification
from .uat import run_uat
from .check import run_check
from .verify_formula import run_verify_formula
from .logger import Logger
from .send_feishu import (
    FeishuClient,
    build_alert_card,
    build_text_message,
    build_combined_alert_card,
    build_combined_text_message,
    build_failure_card,
    build_failure_text,
)


def get_due_week(date_str):
    """日期 → ISO week, 如 '2026-07-07' → '2026-28'"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def fetch_historical_data(country_code, current_due_week, stage, num_weeks=7):
    """
    获取历史数据用于连续异常检测。

    Args:
        country_code: "MX" | "AR"
        current_due_week: 当前周 "2026-28"
        stage: 分析阶段
        num_weeks: 回溯周数

    Returns:
        [{"due_week": str, "overdue_rate": float, "change_abs": float}, ...]
        按时间倒序（最新在前）
    """
    if not config.ENABLE_CONTINUOUS_ALERT:
        return []

    year, week = map(int, current_due_week.split("-"))

    historical = []
    for i in range(1, num_weeks + 1):
        # 回溯 i 周
        past_monday = datetime.strptime(f"{year}-W{week:02d}-1", "%Y-W%W-%w")
        past_monday -= timedelta(weeks=i)
        past_iso = past_monday.isocalendar()
        past_week = f"{past_iso[0]}-{past_iso[1]:02d}"

        try:
            data = fetch_overdue_data(past_week, country_code)
            if data and data.get("overall", {}).get(stage):
                rate = data["overall"][stage].get("overdue_rate", 0.0)
                # 再回溯一周用于计算环比
                prev_monday = past_monday - timedelta(weeks=1)
                prev_iso = prev_monday.isocalendar()
                prev_week = f"{prev_iso[0]}-{prev_iso[1]:02d}"
                prev_data = fetch_overdue_data(prev_week, country_code)
                prev_rate = 0.0
                if prev_data and prev_data.get("overall", {}).get(stage):
                    prev_rate = prev_data["overall"][stage].get("overdue_rate", 0.0)

                historical.append({
                    "due_week": past_week,
                    "overdue_rate": rate,
                    "change_abs": rate - prev_rate,
                })
        except Exception:
            continue  # API 失败则跳过该周

    return historical


def run_country(country_code, run_date, business_date, stage="D0",
                dry_run=False, fetch_volumes=True, logger=None):
    """
    对单个国家执行完整预警流程（只分析，不发飞书）。

    Args:
        run_date: 运行日期（今天）
        business_date: 业务日期（昨天），所有 Quick BI 查询使用此日期
        logger: Logger 实例（可选）

    Returns:
        AnalysisReport 或 None（数据为空时）

    Raises:
        RuntimeError: Quick BI API 调用失败
    """
    country = config.COUNTRIES[country_code]

    def _log(msg):
        if logger:
            logger.info(f"[{country_code}] {msg}")

    print(f"\n{'─'*55}")
    print(f"  {country['name']} ({country_code})  |  业务日期: {business_date}  |  阶段: {stage}")
    print(f"{'─'*55}")

    # Quick BI 查询使用 business_date（前一天），不是 run_date
    due_week = get_due_week(business_date)
    dt = datetime.strptime(business_date, "%Y-%m-%d")
    prev_week = get_due_week((dt - timedelta(days=7)).strftime("%Y-%m-%d"))

    print(f"  This week: {due_week}  |  Baseline: {prev_week}")

    # ---- 获取数据 ----
    _log(f"Fetch overdue data (week={due_week}, day={business_date})")
    print(f"\n  [1/3] Fetch overdue data ...")
    current_data = fetch_overdue_data(due_week, country_code, business_date=business_date)
    if current_data is None:
        msg = f"Quick BI 回收率 API (524c3ccd429c) 返回空或调用失败 (due_week={due_week})"
        _log(f"FATAL: {msg}")
        raise RuntimeError(msg)
    if not current_data.get("raw_rows"):
        msg = f"Quick BI 回收率 API 返回 0 行数据 (due_week={due_week}, country={country_code}, day={business_date})"
        _log(f"FATAL: {msg}")
        raise RuntimeError(msg)

    _log(f"Got {len(current_data['raw_rows'])} rows from recovery API")

    print(f"  [2/3] Fetch baseline data ({prev_week}) ...")
    baseline_business_date = (dt - timedelta(days=7)).strftime("%Y-%m-%d")
    baseline_data = fetch_overdue_data(prev_week, country_code, business_date=baseline_business_date)
    if baseline_data is None:
        _log(f"WARN: Baseline data unavailable for {prev_week}, comparing against 0")
    else:
        _log(f"Baseline: {len(baseline_data.get('raw_rows', []))} rows")

    # 案件量（用于 MIN_CASE 过滤）
    case_volumes = None
    if fetch_volumes:
        print(f"  [3/3] Fetch case volumes ...")
        try:
            case_volumes = fetch_case_volumes(due_week, country_code)
            _log(f"Case volumes: {len(case_volumes)} buckets")
        except Exception as e:
            _log(f"WARN: Case volume fetch failed: {e}")

    # 历史数据（用于连续异常检测，仅在启用时拉取）
    historical = fetch_historical_data(country_code, due_week, stage)

    # ---- 执行分析 ----
    print(f"\n  [Analysis] Running engine (V{config.VERSION}) ...")
    analysis = run_full_analysis(
        current_data, baseline_data, country_code,
        stage=stage,
        case_volumes=case_volumes,
        historical_data=historical,
        run_date=run_date,
        business_date=business_date,
    )

    # ---- 打印摘要 ----
    if analysis.overall:
        o = analysis.overall
        print(f"\n  📊 Overall {stage} overdue rate: {o.overdue_rate:.2%}")
        for comp in o.comparisons:
            sign = "+" if comp.change_abs >= 0 else ""
            print(f"     {comp.method}: {sign}{comp.change_abs:.2%}  "
                  f"({comp.alert_icon} {comp.alert_label})")

    dim_count = sum(len(v) for v in analysis.dimension_results.values())
    print(f"  📐 Dimensions analyzed: {len(analysis.dimension_results)} ({dim_count} buckets)")
    print(f"  📈 Contributions: {len(analysis.contributions)} items")
    for c in analysis.contributions[:3]:
        print(f"     {c.dim_label}·{c.bucket}: contrib {c.contribution_pct:.1%}  "
              f"({c.change_abs:+.4f})")
    print(f"  Status: {analysis.anomaly_summary}")

    if analysis.continuous_alert and analysis.continuous_alert.is_continuous_anomaly:
        print(f"  🔥 {analysis.continuous_alert.warning_text}")

    # 统计异常节点数
    anomaly_nodes = sum(
        1 for v in analysis.dimension_results.values()
        for r in v
        if any(c.alert_level and c.alert_level != "GREEN" for c in r.comparisons)
    )
    _log(f"Anomaly nodes: {anomaly_nodes}")

    return analysis


def _get_all_chat_ids(country_code="ALL"):
    """获取所有目标飞书群 ID（去重）"""
    countries = ["MX", "AR"] if country_code == "ALL" else [country_code]
    return list(dict.fromkeys(
        config.COUNTRIES[cc]["chat_id"] for cc in countries
    ))


def _send_failure_notification(feishu, error_msg, business_date, run_date,
                                text_only=False, logger=None):
    """尝试向所有群发送失败通知。即使发送本身也失败，也要记录。"""
    chat_ids = _get_all_chat_ids()
    success = False
    for chat_id in chat_ids:
        try:
            if text_only:
                text = build_failure_text(error_msg, business_date, run_date)
                feishu.send_text(chat_id, text)
            else:
                card = build_failure_card(error_msg, business_date, run_date)
                feishu.send_card(chat_id, card)
            print(f"  ⚠️ Failure notification sent -> {chat_id}")
            success = True
        except Exception as e:
            print(f"  ❌ Failed to send failure notification to {chat_id}: {e}")
            # 降级：纯文本重试
            try:
                text = build_failure_text(error_msg, business_date, run_date)
                feishu.send_text(chat_id, text)
                success = True
            except Exception as e2:
                print(f"  ❌ Fallback failure notification also failed: {e2}")
    if logger:
        if success:
            logger.info("Failure notification sent to Feishu")
        else:
            logger.error("Failed to send failure notification to Feishu")


def main():
    parser = argparse.ArgumentParser(
        description=f"首逾智能预警系统 v{config.VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                                # All countries, today
  python main.py --country MX                   # Mexico only
  python main.py --country AR                   # Argentina only
  python main.py --date 2026-07-07 --dry-run    # Specified date, compute only
  python main.py --text-only                    # Plain text fallback
  python main.py --list-chats                   # List available chats
  python main.py --stage D1                     # Analyze D1 stage
  python main.py --uat                          # UAT auto-verification
  python -m daily_alert.main --dry-run          # Run as module
        """,
    )
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"),
                        help="Run date (default: today). Business date = run_date - 1 day")
    parser.add_argument("--country", choices=["MX", "AR", "ALL"],
                        default="ALL", help="Country (default: ALL)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute only, no Feishu send")
    parser.add_argument("--text-only", action="store_true",
                        help="Send as plain text instead of card")
    parser.add_argument("--list-chats", action="store_true",
                        help="List available Feishu chats")
    parser.add_argument("--stage", choices=["D-2", "D-1", "D0", "D1", "S1", "S2"],
                        default="D0", help="Analysis stage (default: D0)")
    parser.add_argument("--dump-fields", action="store_true",
                        help="Dump all API fields, types, and unique values (field inspection tool)")
    parser.add_argument("--verify-fields", action="store_true",
                        help="Deep-dive verify 6 unused fields (due_amt, due_case, due_day, order_grade, mult_no, is_dl_order)")
    parser.add_argument("--uat", action="store_true",
                        help="UAT auto-verification: fetch Quick BI data, compute metrics, cross-validate, generate Excel report")
    parser.add_argument("--check", action="store_true",
                        help="Manual UAT check: output raw D0/D1 calculations by dimension (no Feishu, no business analysis)")
    parser.add_argument("--verify-formula", action="store_true",
                        help="Verify overdue rate formula: enumerate all candidate formulas and compare against BI benchmark")
    args = parser.parse_args()

    # ---- 日期计算 ----
    run_date = args.date
    run_dt = datetime.strptime(run_date, "%Y-%m-%d")
    business_date = (run_dt - timedelta(days=1)).strftime("%Y-%m-%d")

    # ================================================================
    #  配置校验（启动即检查，失败立即停止）
    # ================================================================
    config_errors = config.validate_config()
    if config_errors:
        print(f"\n{'='*55}")
        print(f"  ❌ 配置校验失败 — 启动终止")
        print(f"{'='*55}")
        for err in config_errors:
            print(f"  • {err}")
        print(f"\n  请修复以上配置后重新运行。")
        sys.exit(1)

    # ================================================================
    #  日志初始化
    # ================================================================
    logger = Logger(
        run_date=run_date,
        business_date=business_date,
        stage=args.stage,
        country=args.country,
    )

    # ================================================================
    #  启动信息
    # ================================================================
    print(f"\n{'='*55}")
    print(f"  首逾智能预警系统  v{config.VERSION}  Build {config.BUILD}")
    print(f"  运行日期: {run_date}  |  业务日期: {business_date}  |  阶段: {args.stage}")
    print(f"  国家: {args.country}")
    if args.dry_run:
        print(f"  Mode: DRY-RUN")
    elif args.text_only:
        print(f"  Mode: TEXT-ONLY")
    if config.ENABLE_CONTINUOUS_ALERT:
        print(f"  🔥 Continuous alert: ENABLED")
    print(f"{'='*55}")

    logger.info("=" * 40)
    logger.info(f"START — run_date={run_date} business_date={business_date} "
                f"stage={args.stage} country={args.country} dry_run={args.dry_run}")
    logger.info(f"Config validation: PASSED ({0} errors)")

    # ---- 工具模式（不发送飞书） ----
    if args.dump_fields:
        logger.info("Mode: --dump-fields")
        try:
            run_field_inspection(run_date=run_date)
            logger.finish(success=True)
            sys.exit(0)
        except Exception as e:
            logger.fatal(f"Field inspection failed: {e}")
            traceback.print_exc()
            logger.finish(success=False)
            sys.exit(1)

    if args.verify_fields:
        logger.info("Mode: --verify-fields")
        try:
            run_field_verification(run_date=run_date)
            logger.finish(success=True)
            sys.exit(0)
        except Exception as e:
            logger.fatal(f"Field verification failed: {e}")
            traceback.print_exc()
            logger.finish(success=False)
            sys.exit(1)

    if args.uat:
        logger.info("Mode: --uat")
        try:
            run_uat(run_date=run_date, country_code=args.country)
            logger.finish(success=True)
            sys.exit(0)
        except Exception as e:
            logger.fatal(f"UAT failed: {e}")
            traceback.print_exc()
            logger.finish(success=False)
            sys.exit(1)

    if args.check:
        logger.info(f"Mode: --check country={args.country} stage={args.stage}")
        try:
            # --check 模式下，仅运行指定国家（默认 MX）
            cc = args.country if args.country != "ALL" else "MX"
            run_check(run_date=run_date, country_code=cc, stage=args.stage)
            logger.finish(success=True)
            sys.exit(0)
        except Exception as e:
            logger.fatal(f"Check failed: {e}")
            traceback.print_exc()
            logger.finish(success=False)
            sys.exit(1)

    if args.verify_formula:
        logger.info(f"Mode: --verify-formula country={args.country}")
        try:
            cc = args.country if args.country != "ALL" else "MX"
            run_verify_formula(run_date=run_date, country_code=cc)
            logger.finish(success=True)
            sys.exit(0)
        except Exception as e:
            logger.fatal(f"Formula verification failed: {e}")
            traceback.print_exc()
            logger.finish(success=False)
            sys.exit(1)

    # ================================================================
    #  正常预警模式
    # ================================================================
    feishu = None
    exit_code = 0
    total_anomaly_count = 0
    analyses = {}
    reset_cache()  # v1.0.1: 每次运行独立统计

    try:
        # ---- 飞书初始化 ----
        if not args.dry_run:
            try:
                feishu = FeishuClient()
                _ = feishu._get_token()
                print(f"[OK] Feishu connected")
                logger.info("Feishu connected")
            except Exception as e:
                msg = f"Feishu connection failed: {e}"
                print(f"[WARN] {msg}")
                logger.warn(msg)

        # ---- 列出群聊 ----
        if args.list_chats and feishu:
            print(f"\n[*] Available chats:")
            for c in feishu.list_chats():
                print(f"  {c.get('name', '?')}  ->  {c.get('chat_id', '?')}")
            logger.finish(success=True)
            sys.exit(0)

        # ---- Cache 预热 & API 数据拉取（v1.0.1: 统一请求一次，两国共享） ----
        countries_to_run = ["MX", "AR"] if args.country == "ALL" else [args.country]
        logger.info(f"Countries: {countries_to_run}")

        due_week = get_due_week(business_date)
        dt = datetime.strptime(business_date, "%Y-%m-%d")
        prev_week = get_due_week((dt - timedelta(days=7)).strftime("%Y-%m-%d"))

        # 案件量 API 的周一日期
        year, week = due_week.split("-")
        monday = datetime.strptime(f"{year}-W{week}-1", "%Y-W%W-%w")
        week_start = monday.strftime("%Y%m%d")

        logger.profile_start("API 数据拉取")
        logger.info(f"Cache warm-up: due_week={due_week}, prev_week={prev_week}")

        warm_cache_recovery(due_week)          # 本周回收率数据
        warm_cache_recovery(prev_week)         # 上周基线回收率数据
        warm_cache_cases(week_start)           # 案件量数据
        logger.profile_end()
        logger.info("Cache warm-up complete")

        # ---- 执行分析（v1.0.1: 各国复用缓存，不再重复请求） ----
        logger.profile_start("Python 计算")
        analyses = {}
        failed_countries = []
        for cc in countries_to_run:
            try:
                analysis = run_country(
                    cc, run_date, business_date,
                    stage=args.stage,
                    dry_run=args.dry_run,
                    logger=logger,
                )
                analyses[cc] = analysis
            except RuntimeError as e:
                # Quick BI 数据获取失败 — 致命错误
                logger.fatal(f"{config.COUNTRIES[cc]['name']}: {e}")
                print(f"\n[FATAL] {config.COUNTRIES[cc]['name']}: {e}")
                failed_countries.append(cc)
                analyses[cc] = None
            except Exception as e:
                logger.error(f"{config.COUNTRIES[cc]['name']} failed: {e}")
                print(f"\n[ERR] {config.COUNTRIES[cc]['name']} failed: {e}")
                traceback.print_exc()
                analyses[cc] = None

        # 如果所有国家都因 Quick BI 失败 — 发送失败通知后退出
        if failed_countries and len(failed_countries) == len(countries_to_run):
            error_msg = "所有国家的 Quick BI 数据获取均失败。\n\n"
            for cc in failed_countries:
                error_msg += f"• {config.COUNTRIES[cc]['name']} ({cc}): API 返回空或调用失败\n"
            error_msg += f"\n业务日期: {business_date}"

            logger.fatal(error_msg)

            if feishu and not args.dry_run:
                _send_failure_notification(
                    feishu, error_msg, business_date, run_date,
                    text_only=args.text_only, logger=logger,
                )

            logger.set_cache_stats(get_cache_stats())
            logger.finish(success=False)
            sys.exit(1)

        # 部分国家失败 — 记录但继续处理成功的
        if failed_countries:
            logger.warn(f"Partial failure: {len(failed_countries)}/{len(countries_to_run)} countries failed")

        # ---- 构建格式化报告 ----
        reports_by_chat = defaultdict(list)

        for cc, analysis in analyses.items():
            if analysis is None:
                continue
            formatted = build_report(analysis)
            chat_id = config.COUNTRIES[cc]["chat_id"]
            reports_by_chat[chat_id].append(formatted)

            # 统计异常
            anomaly_count = sum(
                1 for v in analysis.dimension_results.values()
                for r in v
                if any(c.alert_level and c.alert_level != "GREEN" for c in r.comparisons)
            )
            total_anomaly_count += anomaly_count

        logger.set_anomaly_count(total_anomaly_count)
        logger.info(f"Reports built: {len(reports_by_chat)} chats, "
                     f"total anomalies: {total_anomaly_count}")
        logger.profile_end()  # v1.0.1: end Python 计算

        # ---- Dry-run: 仅打印，不发送 ----
        if args.dry_run:
            for chat_id, reports in reports_by_chat.items():
                for r in reports:
                    print(f"\n{'─'*55}")

                    if r.executive_summary:
                        for line in r.executive_summary.split("\n"):
                            print(f"  📋 {line}")

                    if r.continuous_alert and r.continuous_alert.is_continuous_anomaly:
                        print(f"  🔥 {r.continuous_alert.warning_text}")

                    if r.tree_rows:
                        print()
                        for row in r.tree_rows:
                            print(f"  {row['text']}")

                    if r.recommendations:
                        print(f"\n  💡 建议:")
                        for rec in r.recommendations:
                            print(f"    • {rec}")
            logger.info(f"Mode: DRY-RUN — reports printed, not sent")
            logger.set_cache_stats(get_cache_stats())
            exit_code = logger.finish(success=True)
            _print_summary(analyses)
            sys.exit(exit_code)

        # ---- 发送飞书（v1.0.1: profile timing） ----
        logger.profile_start("飞书发送")
        if not feishu:
            msg = "No Feishu connection — reports not sent"
            print(f"\n[WARN] {msg}")
            logger.warn(msg)
            logger.profile_end()
            logger.set_cache_stats(get_cache_stats())
            exit_code = logger.finish(success=True)
            _print_summary(analyses)
            sys.exit(exit_code)

        send_errors = 0
        for chat_id, reports in reports_by_chat.items():
            try:
                if args.text_only:
                    if len(reports) == 1:
                        text = build_text_message(reports[0])
                    else:
                        text = build_combined_text_message(reports)
                    feishu.send_text(chat_id, text)
                    print(f"\n  ✅ Text sent -> {chat_id}")
                    logger.info(f"Text message sent to {chat_id}")
                else:
                    if len(reports) == 1:
                        card = build_alert_card(reports[0])
                    else:
                        card = build_combined_alert_card(reports)
                    feishu.send_card(chat_id, card)
                    print(f"\n  ✅ Card sent -> {chat_id}")
                    logger.info(f"Card sent to {chat_id}")
            except Exception as e:
                send_errors += 1
                print(f"\n  ❌ Send to {chat_id} failed: {e}")
                logger.error(f"Send to {chat_id} failed: {e}")
                # 降级：文本重试
                if not args.text_only:
                    try:
                        if len(reports) == 1:
                            text = build_text_message(reports[0])
                        else:
                            text = build_combined_text_message(reports)
                        feishu.send_text(chat_id, text)
                        print(f"  ✅ Fallback text sent -> {chat_id}")
                        logger.info(f"Fallback text sent to {chat_id}")
                    except Exception as e2:
                        print(f"  ❌ Fallback also failed: {e2}")
                        logger.error(f"Fallback send to {chat_id} also failed: {e2}")

        if send_errors:
            logger.warn(f"Send errors: {send_errors} chat(s)")

        logger.profile_end()  # v1.0.1: end 飞书发送
        logger.set_cache_stats(get_cache_stats())
        exit_code = logger.finish(success=True)

    except Exception as e:
        # ---- 未预期的致命错误 ----
        error_msg = f"未预期错误: {e}\n\n{traceback.format_exc()[:500]}"
        logger.fatal(error_msg)
        traceback.print_exc()

        # 尝试发送失败通知
        if feishu and not args.dry_run:
            try:
                _send_failure_notification(
                    feishu,
                    f"程序运行异常\n\n{str(e)[:200]}",
                    business_date, run_date,
                    text_only=args.text_only, logger=logger,
                )
            except Exception:
                logger.error("Failed to send failure notification")

        logger.set_cache_stats(get_cache_stats())
        exit_code = logger.finish(success=False)

    _print_summary(analyses)
    sys.exit(exit_code)


def _print_summary(analyses):
    """打印最终汇总"""
    print(f"\n{'='*55}")
    print(f"  Done")
    for cc, analysis in analyses.items():
        if analysis and analysis.overall:
            o = analysis.overall
            print(f"  {analysis.country_name}: "
                  f"{o.overdue_rate:.2%} ({o.stage}) | "
                  f"{analysis.anomaly_summary}")
        else:
            print(f"  {config.COUNTRIES[cc]['name']}: FAILED")
    print(f"{'='*55}")


if __name__ == "__main__":
    sys.exit(main())
