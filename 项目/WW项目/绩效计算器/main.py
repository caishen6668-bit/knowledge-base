# -*- coding: utf-8 -*-
"""
WW 智能绩效与人员调配系统 — 主入口

用法：
    python main.py
"""

import os
import sys
import traceback

import config
from utils import scan_daily_files
from excel_reader import read_daily_report
from performance import calculate_day
from excel_export import generate_output
import ability
import optimizer
import dashboard
import scheduler_api
import openpyxl


# 控制台 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 40)
    print("  WW 智能绩效与人员调配系统 V2.0")
    print("=" * 40)

    # 扫描日报
    daily_files = scan_daily_files(config.DAILY_DIR)
    if not daily_files:
        print(f"\n[!] 日报目录为空或不存在: {config.DAILY_DIR}")
        print(f"    请将日报文件（如 10号.xlsx）放入该目录后重试。")
        return

    print(f"\n发现日报：\n")
    for rel, day, full in daily_files:
        print(f"  {rel}")

    print(f"\n请选择：\n")
    print(f"  1、全部计算（默认，直接回车）")
    print(f"  2、选择部分日报")
    print()

    choice = input("> ").strip()

    if choice == "2":
        print()
        for i, (rel, day, full) in enumerate(daily_files, 1):
            print(f"  [{i}] {rel}")
        print(f"\n  输入编号，多个用逗号分隔（如 1,2,3）")

        choice2 = input("\n> ").strip()
        if not choice2:
            selected_files = daily_files
        else:
            selected_files = []
            try:
                choice_normalized = choice2.replace("，", ",")
                indices = [int(x.strip()) for x in choice_normalized.split(",")]
                for idx in indices:
                    if 1 <= idx <= len(daily_files):
                        selected_files.append(daily_files[idx - 1])
                    else:
                        print(f"  [!] 编号 {idx} 超出范围，已跳过")
            except ValueError:
                print("  [!] 输入格式错误")
                return
            if not selected_files:
                print("  未选择任何文件，退出。")
                return
    else:
        selected_files = daily_files

    print(f"\n共 {len(selected_files)} 个日报待处理。\n")

    # 逐个处理
    all_day_results = []
    last_data_date = None
    last_records = None

    for item in selected_files:
        rel_path, day_num, filepath = item

        print(f"正在处理：{rel_path}")
        print("-" * 30)

        try:
            data_date, records, warnings = read_daily_report(filepath)
            last_data_date = data_date
            last_records = records
            results, scheme_name = calculate_day(data_date, records)
            all_day_results.append((data_date, results, scheme_name))

            # 更新能力数据库
            try:
                ability.update(records, data_date)
            except Exception as e:
                print(f"  [!] 能力数据库更新失败: {e}")

            # 按阶段统计人数
            stage_counts = {}
            for r in results:
                if r.is_rest:
                    stage = "休息"
                elif r.stage:
                    stage = r.stage
                else:
                    stage = "未分配"
                stage_counts[stage] = stage_counts.get(stage, 0) + 1

            total_perf = sum(r.amount for r in results if not r.is_rest)

            print(f"  使用绩效方案：{scheme_name}")
            print(f"  读取员工：{len(records)}人")
            for stage in sorted(stage_counts.keys()):
                print(f"  {stage}：{stage_counts[stage]}人")
            print(f"  总绩效：{total_perf:.0f}")
            print(f"  处理完成。")

            # 打印警告
            if warnings:
                for w in warnings:
                    print(w)

            print()

        except Exception as e:
            print(f"  [XX] 处理失败: {e}")
            traceback.print_exc()
            print()
            continue

    if not all_day_results:
        print("没有成功处理任何文件，退出。")
        return

    # 生成输出
    generate_output(all_day_results, str(config.OUTPUT_PATH))

    # --- 管理驾驶舱 ---
    if last_data_date and last_records:
        print("\n生成管理驾驶舱...")
        try:
            # 1. 构建 ability_map: {staff_id: {stage: {ability_score, confidence, experience, overall_score, name}}}
            all_ab = ability.get_all_abilities()
            ability_map = {}
            for a in all_ab:
                sid = a["staff_id"]
                if sid not in ability_map:
                    ability_map[sid] = {}
                ability_map[sid][a["stage"]] = {
                    "ability_score": a["ability_score"],
                    "confidence": a["confidence"],
                    "experience": a["experience"],
                    "overall_score": a["overall_score"],
                    "name": a["name"],
                }

            # 2. 构建 yesterday_map: {staff_id: stage}（取 dashboard 日期之前最后一次非休息记录）
            detail = ability.get_detail_history()
            yesterday_map = {}
            for d in detail:
                if d["date"] is not None and d["date"] < last_data_date and not d.get("is_rest", False):
                    yesterday_map[d["staff_id"]] = d["stage"]  # 后面的覆盖前面的（取最新）

            # 3. 确定需求（使用最后一日的队列分布，归一化队列名）
            demand = {}
            for rec in last_records:
                if not rec.is_rest:
                    q = config.QUEUE_STAGE_MAP.get(rec.queue, rec.queue)
                    demand[q] = demand.get(q, 0) + 1

            # 4. 在岗人员 — 从排班 API 获取真实排班数据
            day_schedule = scheduler_api.get_day_schedule(last_data_date)
            name_map = config.SCHEDULER_NAME_TO_STAFF_ID if hasattr(config, 'SCHEDULER_NAME_TO_STAFF_ID') else {}
            staff_pool = []
            scheduled_count = 0
            for entry in day_schedule:
                if entry["on_duty"]:
                    scheduled_count += 1
                    staff_id = scheduler_api.match_staff_id(entry["name"], name_map)
                    if staff_id:
                        staff_pool.append(staff_id)

            if not staff_pool:
                # 降级：排班数据不可用或没有匹配到，使用日报数据
                print(f"  [i] 排班 API 未匹配到 WW 团队成员，使用日报数据")
                staff_pool = [r.staff_id for r in last_records if not r.is_rest]
            else:
                print(f"  排班 API: {scheduled_count} 人在岗, 匹配 WW 团队 {len(staff_pool)} 人")

            # 5. 运行优化器
            locked = config.LOCKED_STAFF if hasattr(config, 'LOCKED_STAFF') else {}
            opt_result = optimizer.optimize(staff_pool, demand, ability_map, yesterday_map, locked)

            # 6. 能力排行（按 OverallScore 降序）
            ability_ranking = sorted(all_ab, key=lambda x: x.get("overall_score", 0), reverse=True)

            # 7. 写入 Dashboard Sheet
            wb = openpyxl.load_workbook(str(config.OUTPUT_PATH))
            dashboard.add_dashboard_sheet(wb, last_data_date, last_records,
                                           ability_ranking, opt_result, demand)
            wb.save(str(config.OUTPUT_PATH))
            wb.close()

            # 简要输出
            s = opt_result.summary
            print(f"  管理驾驶舱已生成。")
            print(f"  调岗建议: {s['total_transfers']}人 | "
                  f"保持: {s['staying_count']}人 | "
                  f"稳定率: {s['stability_pct']:.0f}% | "
                  f"预警: {len(opt_result.warnings)}人")

        except Exception as e:
            print(f"  [!] 管理驾驶舱生成失败: {e}")
            import traceback
            traceback.print_exc()

    # 结束
    print("=" * 40)
    print("  全部处理完成！")
    print(f"  输出文件：{config.OUTPUT_PATH.name}")
    print("=" * 40)



if __name__ == "__main__":
    main()
