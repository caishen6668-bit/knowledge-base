"""
V2 首逾智能预警系统 — CLI 入口

用法:
  python -m daily_alert.v2.main                          # 当天，所有国家
  python -m daily_alert.v2.main --country MX --stage D0   # 墨西哥 D0
  python -m daily_alert.v2.main --country AR --stage D1   # 阿根廷 D1
  python -m daily_alert.v2.main --date 2026-07-07         # 指定日期
  python -m daily_alert.v2.main --dry-run                 # 仅计算，不发飞书（未来）

V2 Phase 1+2+3: 趋势预警 + 根因定位 + 行动建议
  - 昨天对比 (DoD) / 近3日均值 / 近7日均值 / 目标值对比
  - 自动判断: 🟢正常 🟡关注 🟠警告 🔴严重
  - 🟠/🔴 自动触发根因下钻定位
  - 自动生成行动建议（纯规则引擎）
  - 输出 TrendResult + RootCauseResult + ActionResult
"""

# ---- Windows UTF-8 ----
import io as _io
import sys as _sys
if _sys.platform == "win32" and hasattr(_sys.stdout, 'buffer'):
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")

import argparse
import sys
import traceback
from datetime import datetime, date, timedelta

from .. import config as v1_config
from . import config as v2_config
from .trend_engine import (
    compute_trends,
    print_trend_report,
    reset_cache,
    get_cache_stats,
    _get_business_date,
)
from .root_cause import analyze_root_cause, print_root_cause_report
from .action_engine import analyze_actions, print_action_report
from .alert_decision import decide_alert, print_alert_decision
from .alert_decision_v2 import (
    decide_alert_v2, print_alert_decision_v2,
    decide_alert_multi_dim, decide_alert_multi_dim_enriched,
    print_multi_dim_decision,
)
from .feishu_builder_v2 import build_alert_card_v2, build_text_message_v2, send_alert_v2
from .persistence import compute_persistence, attach_persistence_labels
from .dimension_scorer import compute_app_breakdowns
from .simulator import (
    run_simulation, print_simulation_summary, generate_excel,
    run_comparison, generate_comparison_excel,
)
from .threshold_optimizer import (
    run_optimization, print_optimization_summary,
    recommend_best, generate_optimization_excel,
)


def main():
    parser = argparse.ArgumentParser(
        description=f"V2 首逾智能预警系统 — 趋势预警引擎 Phase {v2_config.V2_PHASE}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m daily_alert.v2.main                             # All countries, today
  python -m daily_alert.v2.main --country MX --stage D0      # Mexico D0
  python -m daily_alert.v2.main --country AR --stage D1      # Argentina D1
  python -m daily_alert.v2.main --date 2026-07-07 --dry-run  # Specified date, print only
        """,
    )
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"),
                        help="运行日期（默认今天）。业务日期 = 运行日期 - 1")
    parser.add_argument("--country", choices=["MX", "AR", "ALL"],
                        default="ALL", help="国家（默认 ALL）")
    parser.add_argument("--stage", choices=["D-2", "D-1", "D0", "D1", "S1", "S2"],
                        default="D0", help="分析阶段（默认 D0）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅计算并打印，不发送飞书（Phase 2 启用飞书）")
    parser.add_argument("--simulate", action="store_true",
                        help="30 天模拟回测模式：逐天运行完整流程并生成 Simulation_Report.xlsx")
    parser.add_argument("--sim-days", type=int, default=30,
                        help="模拟回溯天数（默认 30）")
    parser.add_argument("--sim-stages", type=str, default="D0",
                        help="模拟阶段，逗号分隔（默认 D0，如 D0,D1）")
    parser.add_argument("--optimize", action="store_true",
                        help="阈值优化模式：自动搜索最佳参数使告警率落在 25%-40%")
    parser.add_argument("--compare-alert", action="store_true",
                        help="V1 vs V2 Alert Decision 对比：30天模拟比较告警率")
    args = parser.parse_args()

    # ================================================================
    #  Simulation Mode — 30 天回测
    # ================================================================
    if args.simulate:
        countries_to_run = ["MX", "AR"] if args.country == "ALL" else [args.country]
        stages_to_run = [s.strip() for s in args.sim_stages.split(",") if s.strip()]

        results, stats = run_simulation(
            run_date=args.date,
            countries=countries_to_run,
            stages=stages_to_run,
            days=args.sim_days,
            silent=True,
        )

        print_simulation_summary(stats)
        generate_excel(results, stats)
        sys.exit(0)

    # ================================================================
    #  Optimization Mode — 阈值网格搜索
    # ================================================================
    if args.optimize:
        countries_to_run = ["MX", "AR"] if args.country == "ALL" else [args.country]
        stages_to_run = [s.strip() for s in args.sim_stages.split(",") if s.strip()]

        top10, all_results = run_optimization(
            run_date=args.date,
            countries=countries_to_run,
            stages=stages_to_run,
            days=args.sim_days,
            silent=True,
        )

        best = recommend_best(top10)
        print_optimization_summary(top10, best)
        generate_optimization_excel(top10, all_results, best)
        sys.exit(0)

    # ================================================================
    #  Alert Decision V1 vs V2 对比
    # ================================================================
    if args.compare_alert:
        countries_to_run = ["MX", "AR"] if args.country == "ALL" else [args.country]
        stages_to_run = [s.strip() for s in args.sim_stages.split(",") if s.strip()]

        comparison = run_comparison(
            run_date=args.date,
            countries=countries_to_run,
            stages=stages_to_run,
            days=args.sim_days,
            silent=True,
        )
        generate_comparison_excel(comparison)
        sys.exit(0)

    # ---- 日期计算 ----
    run_date = args.date
    business_date = _get_business_date(run_date)

    # ================================================================
    #  启动信息
    # ================================================================
    print(f"\n{'='*55}")
    print(f"  V2 首逾智能预警系统  {v2_config.V2_VERSION}  Phase {v2_config.V2_PHASE}")
    print(f"  Build {v2_config.V2_BUILD}  |  基于 V1 {v1_config.VERSION} Stable")
    print(f"  运行日期: {run_date}  |  业务日期: {business_date}  |  阶段: {args.stage}")
    print(f"  国家: {args.country}")
    if args.dry_run:
        print(f"  Mode: DRY-RUN (仅打印)")
    print(f"{'='*55}")

    # ---- 配置校验 ----
    config_errors = v1_config.validate_config()
    if config_errors:
        print(f"\n  ❌ V1 配置校验失败 — 启动终止")
        for err in config_errors:
            print(f"  • {err}")
        sys.exit(1)

    # ================================================================
    #  执行趋势分析
    # ================================================================
    reset_cache()
    countries_to_run = ["MX", "AR"] if args.country == "ALL" else [args.country]
    exit_code = 0
    all_reports = {}

    try:
        for cc in countries_to_run:
            try:
                report = compute_trends(
                    country_code=cc,
                    business_date=business_date,
                    stage=args.stage,
                    run_date=run_date,
                )
                all_reports[cc] = report

                # 打印完整报告
                print_trend_report(report)

                # ---- Phase 2: 根因定位（🟠/🔴 自动触发） ----
                anomalies = report.get_anomalies()
                triggered = [a for a in anomalies
                             if a.overall_judgment in ("ORANGE", "RED")]
                root_cause = None
                if triggered:
                    try:
                        root_cause = analyze_root_cause(
                            trend_report=report,
                            country_code=cc,
                            business_date=business_date,
                            stage=args.stage,
                        )
                        if root_cause:
                            print_root_cause_report(root_cause)
                    except Exception as e:
                        print(f"\n  ⚠️ 根因定位失败: {e}")
                        traceback.print_exc()

                # ---- Phase 3: 行动建议（条件触发） ----
                action_result = None
                if root_cause and root_cause.has_root_cause:
                    try:
                        action_result = analyze_actions(
                            trend_report=report,
                            root_cause=root_cause,
                        )
                        if action_result:
                            print_action_report(action_result)
                    except Exception as e:
                        print(f"\n  ⚠️ 行动建议生成失败: {e}")
                        traceback.print_exc()

                # ---- Phase 3.5: 多维度告警决策 ----
                try:
                    if action_result:
                        # 有 Root Cause + Action → 增强模式
                        multi_decision = decide_alert_multi_dim_enriched(
                            trend_report=report,
                            root_cause=root_cause,
                            action_result=action_result,
                            country_code=cc,
                        )
                    else:
                        # 纯维度评分（Overall 正常或 Root Cause 未触发）
                        multi_decision = decide_alert_multi_dim(
                            trend_report=report,
                            country_code=cc,
                        )

                    # ---- 异常持续时间（Persistence） ----
                    try:
                        persistence_map = compute_persistence(
                            country_code=cc,
                            business_date=business_date,
                            stage=args.stage,
                        )
                        attach_persistence_labels(
                            multi_decision.all_anomalies,
                            persistence_map,
                        )
                    except Exception as e:
                        print(f"\n  ⚠️ 持久性分析失败: {e}")

                    # ---- APP 下钻（纯展示，不参与 Risk Score） ----
                    try:
                        compute_app_breakdowns(
                            multi_decision.all_anomalies,
                            cc, business_date, args.stage,
                        )
                    except Exception as e:
                        print(f"\n  ⚠️ APP 下钻分析失败: {e}")

                    # 终端打印（调试用）
                    print_multi_dim_decision(multi_decision)

                    # ---- 飞书发送 ----
                    if not args.dry_run and multi_decision.should_alert:
                        try:
                            # 发送到群聊
                            chat_id = v1_config.FEISHU_CHAT_MX if cc == "MX" else v1_config.FEISHU_CHAT_AR
                            if chat_id:
                                result = send_alert_v2(multi_decision, chat_id)
                                print(f"  📨 飞书群聊已发送: {chat_id} "
                                      f"(msg_id={result.get('data',{}).get('message_id','?')})")
                        except Exception as e:
                            print(f"\n  ⚠️ 飞书发送失败: {e}")
                            traceback.print_exc()

                except Exception as e:
                    print(f"\n  ⚠️ 多维度告警决策失败: {e}")
                    traceback.print_exc()

            except Exception as e:
                print(f"\n  ❌ {v1_config.COUNTRIES[cc]['name']} ({cc}) 趋势分析失败: {e}")
                traceback.print_exc()
                exit_code = 1
                all_reports[cc] = None

        # ---- Cache Profile ----
        stats = get_cache_stats()
        if stats["calls"] > 0:
            print(f"\n  📈 API Profile: {stats['calls']} requests, "
                  f"{stats['hits']} cache hits "
                  f"({stats['hit_rate']:.0f}% hit rate)")

        # ---- 最终汇总 ----
        print(f"\n{'='*55}")
        print(f"  V2 趋势预警完成")
        for cc, report in all_reports.items():
            if report and report.results:
                print(f"  {report.summary()}")
            elif report:
                print(f"  {v1_config.COUNTRIES[cc]['name']}: 无数据")
            else:
                print(f"  {v1_config.COUNTRIES[cc]['name']}: FAILED")
        print(f"{'='*55}\n")

    except Exception as e:
        print(f"\n  ❌ 未预期错误: {e}")
        traceback.print_exc()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    sys.exit(main())
