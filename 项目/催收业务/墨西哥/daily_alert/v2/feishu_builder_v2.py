"""
V2 飞书消息构建器 — 多维度独立监控

卡片结构：
  1. 今日结论 — 状态 + 风险摘要 + 管理动作
  2. 重点风险 Top3 — 三个独立风险点，含异常原因和排名上下文
  3. 整体数据 — 精简参考信息

设计原则：
  - 面向业务人员，不展示调试信息
  - 不重复展示同一风险路径（去重：整体不在 Top3 中和子维度并列）
  - 无 🔴 红点（仅标题用 🚨/🟡/🟢）
  - 无 Risk Score 展示（后台保留）
  - 币种使用 MXN / ARS，不用 ¥
  - 金额用"万"为单位

不修改: send_feishu.py / trend_engine.py / root_cause.py / action_engine.py
"""

import json
import re
from datetime import datetime
from typing import List, Optional

from .models import MultiDimAlertDecision, DimensionAnomaly


# ============================================================
#  主入口
# ============================================================

def build_alert_card_v2(decision: MultiDimAlertDecision) -> dict:
    """构建飞书卡片 JSON。

    两层结构：
      管理摘要（30秒读完） → 异常详情（APP / 原因分析）
    """
    elements = []
    cc = decision.country_code

    # ---- 颜色 & 状态 ----
    color = _card_color(decision)
    status_emoji = _status_emoji(decision)
    country_flag = _country_flag(cc)

    # ---- 标题 ----
    title = f"{status_emoji} {country_flag} {decision.country_name} {decision.stage} 风险监控日报"

    # ════════════════════════════════════════════════════════
    #  区 1: 管理摘要 — 首页，30 秒读完
    # ════════════════════════════════════════════════════════
    elements.append(_section_header("今日摘要"))
    elements.extend(_summary_fields(decision))

    # ════════════════════════════════════════════════════════
    #  区 2: 异常详情 — APP 下钻 + 原因分析
    # ════════════════════════════════════════════════════════
    top3_items = _build_top3_deduped(decision)
    if top3_items:
        elements.append({"tag": "hr"})
        elements.append(_section_header("异常详情"))
        elements.append({"tag": "hr"})

        for rank, anomaly in enumerate(top3_items, 1):
            elements.extend(_anomaly_card_fields(
                rank, anomaly, cc,
                overall_due=decision.overall_due_amount,
                overall_cases=decision.overall_case_count,
            ))

        if decision.truncated_count > 0:
            elements.append(_markdown_block("*仅展示风险最高 Top3*"))

    # ════════════════════════════════════════════════════════
    #  区 3: 统一建议
    # ════════════════════════════════════════════════════════
    suggestion = _unified_suggestion(decision)
    if suggestion:
        elements.append({"tag": "hr"})
        elements.append(_markdown_block(suggestion))

    # ---- Footer ----
    elements.append({"tag": "hr"})
    elements.append(_markdown_block(
        f"{decision.business_date}  |  "
        f"{datetime.now().strftime('%H:%M')}  |  "
        f"V2 Multi-Dim"
    ))

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": elements,
    }


# ============================================================
#  文本降级
# ============================================================

def build_text_message_v2(decision: MultiDimAlertDecision) -> str:
    """纯文本降级消息。"""
    lines = []
    cc = decision.country_code
    status_emoji = _status_emoji(decision)
    country_flag = _country_flag(cc)

    lines.append(f"{status_emoji} {country_flag} {decision.country_name} {decision.stage} 风险监控日报")
    lines.append("")

    # 管理摘要
    lines.append("━━ 今日摘要 ━━")
    # Build text summary inline
    rate = decision.overall_rate * 100
    dod = decision.overall_dod_change_pp * 100
    top3_items = _build_top3_deduped(decision)
    if decision.overall_anomaly is not None:
        lines.append(f"🚨 整体首逾率 {rate:.2f}%（较昨日 {dod:+.2f}pp），触发告警")
    elif top3_items:
        lines.append(f"🟡 整体首逾率 {rate:.2f}%（较昨日 {dod:+.2f}pp），局部风险")
    else:
        lines.append(f"🟢 整体首逾率 {rate:.2f}%（较昨日 {dod:+.2f}pp），各维度正常")
    action_guide = _management_action(decision)
    if action_guide:
        lines.append(action_guide)
    lines.append("")

    # 异常详情
    if top3_items:
        lines.append("━━ 异常详情 ━━")
        for rank, anomaly in enumerate(top3_items, 1):
            lines.extend(_anomaly_text_rows(
                rank, anomaly, cc,
                overall_due=decision.overall_due_amount,
                overall_cases=decision.overall_case_count,
            ))
        if decision.truncated_count > 0:
            lines.append("仅展示风险最高 Top3")
        lines.append("")

    # 统一建议
    suggestion = _unified_suggestion(decision)
    if suggestion:
        lines.append("━━ 今日建议 ━━")
        lines.append(suggestion)
        lines.append("")

    lines.append(f"{decision.business_date}  |  V2 Multi-Dim")

    return "\n".join(lines)


# ============================================================
#  Top3 去重逻辑
# ============================================================

def _build_top3_deduped(decision: MultiDimAlertDecision) -> List[DimensionAnomaly]:
    """构建去重后的 Top3 列表。

    规则：
      - 使用 decision.top3_segments（已通过 select_top3 做层级去重）
      - 排除 is_merged_away=True 的维度
      - 不展示 overall（已在结论和整体数据中体现）
      - 按 risk_score 降序

    这样避免父子重复（如 "非分期产品" 和 "Grade D" 同时出现）。
    """
    segments = [
        a for a in decision.top3_segments
        if not a.is_merged_away and a.dimension != "overall"
    ]
    segments.sort(key=lambda x: x.risk_score, reverse=True)
    return segments[:3]


# ============================================================
#  卡片组件
# ============================================================

def _section_header(text: str) -> dict:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**━━ {text} ━━**"},
    }


def _markdown_block(content: str) -> dict:
    return {
        "tag": "div",
        "text": {"tag": "lark_md", "content": content},
    }


def _top3_card_title(anomaly: DimensionAnomaly) -> str:
    """Top3 卡片标题：标题维度与数据口径一致。

    - Level 0（整体）: "整体"
    - Level 1（产品分类）: "非分期产品" / "分期产品"
    - Level 2（分期子类型）: "借款分期" / "展期分期" / "展期N期"
    - Level 3（风控等级）: "Grade D" / "Grade A" ...（真实维度，不映射到产品）
    """
    if anomaly.level == 0:
        return "整体"
    elif anomaly.level == 1:
        return anomaly.bucket_label
    elif anomaly.level == 2:
        return anomaly.bucket_label
    elif anomaly.level == 3:
        return f"Grade {anomaly.bucket}"
    return anomaly.bucket_label or anomaly.display_name


def _vq_detail(app) -> str:
    """构建原因分析展示：标签 + 具体变化数值。

    示例：
      质量走弱
      （单量 +3%，首逾 +8.97pp）
    """
    label = app.volume_quality_label
    if not label:
        return ""

    # 单量变化
    if app.yesterday_case_count > 0:
        case_pct = (app.case_count - app.yesterday_case_count) / app.yesterday_case_count * 100
    elif app.case_count > 0:
        case_pct = 100.0  # 昨日0单 → 今日有单
    else:
        case_pct = 0.0

    # 首逾变化
    rate_pp = app.dod_change_pp * 100

    case_str = f"单量 {case_pct:+.0f}%" if abs(case_pct) >= 0.5 else "单量持平"
    rate_str = f"首逾 {rate_pp:+.2f}pp"

    return f"{label}\n（{case_str}，{rate_str}）"


def _app_priority_tag(app, persistence_label: str = "") -> str:
    """APP 处理优先级：P1=真正高风险，P2=其余。

    P1 保持稀缺性 — 必须同时满足：
      1. 当日 DoD ≥ 5pp（明显走弱）
      2. 父维度持续走弱（连续走弱 或 再次走弱）
    不满足任一条 → P2。
    """
    is_significant = app.dod_change_pp >= 0.05
    is_persistent = "连续走弱" in persistence_label or "再次走弱" in persistence_label
    if is_significant and is_persistent:
        return "**[P1]**"
    return "P2"


def _anomaly_card_fields(
    rank: int,
    anomaly: DimensionAnomaly,
    country_code: str,
    amount_tag: str = "",
    overall_due: float = 0.0,
    overall_cases: int = 0,
) -> List[dict]:
    """单个风险项的卡片字段（产品 → Grade → APP）。

    格式：
      ① **非分期产品**
      异常Grade：**D**
      🔥 连续走弱 2 天
      首逾 **40.80%**（昨 31.83% +8.97pp）
      到期单量 **670** 单（81.9%）
      主要APP：**TruCred**、**Kredizo**
    """
    fields = []
    rank_icons = {1: "①", 2: "②", 3: "③"}
    icon = rank_icons.get(rank, f"{rank}")

    # ---- 标题行：产品优先 ----
    if anomaly.level == 3:
        # Grade 异常 → 标题显示所属产品
        product_name = anomaly.primary_product or anomaly.bucket_label
    elif anomaly.level in (1, 2):
        product_name = anomaly.bucket_label
    else:
        product_name = _top3_card_title(anomaly)
    fields.append(_markdown_block(f"{icon}  **{product_name}**"))

    # ---- Grade 异常：显示具体异常 Grade ----
    if anomaly.level == 3:
        fields.append(_markdown_block(f"异常Grade：**{anomaly.bucket}**"))

    # ---- 持续状态 ----
    if anomaly.persistence_label:
        fields.append(_markdown_block(anomaly.persistence_label))

    # ---- 首逾率 + 较昨日变化 ----
    yesterday_rate = anomaly.current_rate - anomaly.dod_change_pp
    dod_str = f"{anomaly.dod_change_pp*100:+.2f}pp"
    fields.append(_markdown_block(
        f"首逾 **{anomaly.current_rate*100:.2f}%**"
        f"（昨 {yesterday_rate*100:.2f}% {dod_str}）"
    ))

    # ---- 到期单量 ----
    case_pct = (anomaly.case_count / overall_cases * 100) if overall_cases > 0 else 0.0
    fields.append(_markdown_block(
        f"到期单量 **{anomaly.case_count}** 单（{case_pct:.1f}%）"
    ))

    # ---- 主要 APP（极简，仅名称） ----
    if anomaly.top_apps:
        app_names = "、".join([f"**{app.app_name}**" for app in anomaly.top_apps[:2]])
        fields.append(_markdown_block(f"主要APP：{app_names}"))

    fields.append({"tag": "hr"})
    return fields


def _build_card_action(anomaly: DimensionAnomaly) -> str:
    """生成单个异常卡片的建议文案（引用 Top2 APP + 主要 Grade）。"""
    apps = (anomaly.top_apps or [])[:2]
    if not apps:
        return anomaly.suggested_action or "持续监控"

    # 构建 "APP Grade" 引用，如 "Cridit Grade B"
    app_grade_refs = []
    for app in apps:
        if app.top_grades:
            top_grade = app.top_grades[0][0]  # grade name
            app_grade_refs.append(f"{app.app_name} Grade {top_grade}")
        else:
            app_grade_refs.append(app.app_name)

    refs = "、".join(app_grade_refs)
    if anomaly.level == 1:
        if anomaly.bucket_label == "分期产品":
            return f"优先查看 {refs}，再下钻借款分期/展期分期/展期N期确认风险来源"
        else:
            return f"优先查看 {refs}，再下钻对应 Grade 确认风险来源"
    elif anomaly.level == 2:
        return f"优先查看 {refs}，再下钻对应 Grade 确认风险集中点"
    elif anomaly.level == 3:
        return f"优先查看 {refs}，确认具体风险来源"
    return f"优先查看 {refs}，再下钻对应维度确认风险来源"


def _one_line_judgment(decision: MultiDimAlertDecision) -> str:
    """生成 💬 今日判断 — 管理汇报口吻。

    结构：
      1. 整体首逾率态势（一句话）
      2. 风险集中点（产品视角）
      3. 关键走弱点 + 建议
    全页面保持 产品 → APP → Grade 视角。
    """
    top3 = _build_top3_deduped(decision)
    if not top3:
        return ""

    # ---- 1. 整体态势 ----
    rate = decision.overall_rate * 100
    dod = decision.overall_dod_change_pp * 100
    if decision.overall_anomaly is not None:
        overall_text = f"整体首逾率 {rate:.2f}%（较昨日 {dod:+.2f}pp），触发告警"
    elif dod > 0.3:
        overall_text = f"整体首逾率 {rate:.2f}%（较昨日 {dod:+.2f}pp），小幅走弱"
    else:
        overall_text = f"整体首逾率 {rate:.2f}%（较昨日 {dod:+.2f}pp），基本稳定"

    # ---- 2. 收集走弱 APP（仅明显走弱 >1pp） ----
    worsening: list = []   # [(app_name, dod_pp, grade, product), ...]
    for s in top3:
        # 产品标签（Grade 维度用 primary_product）
        if s.level == 3:
            product_label = s.primary_product or s.bucket_label
        else:
            product_label = s.bucket_label
        for app in (s.top_apps or []):
            grade = app.top_grades[0][0] if app.top_grades else ""
            if app.dod_change_pp > 0.01:   # 明显走弱
                worsening.append((app.app_name, app.dod_change_pp, grade, product_label))
            elif app.dod_change_pp > 0:
                # 小幅走弱也收集，但不作为重点
                worsening.append((app.app_name, app.dod_change_pp, grade, product_label))

    # 明显走弱优先
    worsening.sort(key=lambda x: x[1], reverse=True)

    # ---- 3. 风险集中产品 ----
    products = list(dict.fromkeys(
        [s.primary_product if s.level == 3 else s.bucket_label for s in top3[:2]]
    ))

    # ---- 4. 构建汇报 ----
    parts = [f"💬 今日判断：{overall_text}。"]

    if products:
        parts.append(f"风险主要集中在{'、'.join(products)}")

    if worsening:
        w = worsening[0]
        w_grade = f" Grade {w[2]}" if w[2] else ""
        parts.append(f"，其中 {w[0]}{w_grade} 今日明显走弱（{w[1]*100:+.2f}pp）")
        parts.append(f"，建议优先排查对应策略及分案情况。")
    else:
        parts.append("，各APP暂无明显走弱信号，建议持续观察。")

    return "".join(parts)


def _overall_data_fields(decision: MultiDimAlertDecision, country_code: str) -> List[dict]:
    """监控快照：到期规模 + 异常分布（含产品 / APP / Grade）。"""
    fields = []

    # ---- 到期规模（单量视角） ----
    fields.append(_markdown_block(
        f"到期单量 **{decision.overall_case_count:,}** 单"
    ))

    # ---- 异常分布：产品分类 / 重点APP / 重点Grade ----
    dist_lines = []
    dist_lines.append("**异常分布**：")

    # 产品分类
    product_items = [
        a for a in decision.all_anomalies
        if a.dimension == "product_type" and not a.is_merged_away and not a.is_sample_small
    ]
    if product_items:
        dist_lines.append("产品分类：")
        for a in sorted(product_items, key=lambda x: x.risk_score, reverse=True):
            dist_lines.append(f"● {a.bucket_label}")

    # 重点APP（从所有异常维度的 top_apps 收集，去重，按 due_amount 降序，取 Top 5）
    all_apps = []
    seen_apps = set()
    for a in decision.all_anomalies:
        if a.is_merged_away or a.is_sample_small:
            continue
        for app in (a.top_apps or []):
            if app.app_name not in seen_apps:
                seen_apps.add(app.app_name)
                all_apps.append(app)
    all_apps.sort(key=lambda x: x.due_amount, reverse=True)
    if all_apps:
        dist_lines.append("重点APP：")
        for app in all_apps[:5]:
            dist_lines.append(f"● {app.app_name}")

    # 重点Grade（仅异常维度）
    grade_items = [
        a for a in decision.all_anomalies
        if a.dimension == "order_grade" and a.is_anomalous
        and not a.is_merged_away and not a.is_sample_small
    ]
    if grade_items:
        dist_lines.append("重点Grade：")
        for a in sorted(grade_items, key=lambda x: x.risk_score, reverse=True):
            dist_lines.append(f"● {a.bucket_label}")

    fields.append(_markdown_block("\n".join(dist_lines)))

    # ---- 目标参考 ----
    if decision.overall_target_value > 0:
        fields.append(_markdown_block(
            f"目标首逾：{decision.overall_target_value*100:.2f}%"
            f"（高于目标 {decision.overall_target_pp*100:+.2f}pp）"
        ))

    return fields


def _anomaly_text_rows(
    rank: int,
    anomaly: DimensionAnomaly,
    country_code: str,
    amount_tag: str = "",
    overall_due: float = 0.0,
    overall_cases: int = 0,
) -> List[str]:
    """纯文本降级：单个异常维度（产品 → Grade → APP）。"""
    lines = []
    rank_icons = {1: "①", 2: "②", 3: "③"}
    icon = rank_icons.get(rank, f"{rank}")

    # 产品优先标题
    if anomaly.level == 3:
        product_name = anomaly.primary_product or anomaly.bucket_label
    elif anomaly.level in (1, 2):
        product_name = anomaly.bucket_label
    else:
        product_name = _top3_card_title(anomaly)

    yesterday_rate = anomaly.current_rate - anomaly.dod_change_pp

    lines.append("")
    lines.append(f"{icon} {product_name}")

    # Grade 异常：显示具体 Grade
    if anomaly.level == 3:
        lines.append(f"   异常Grade：{anomaly.bucket}")

    # 持续状态
    if anomaly.persistence_label:
        lines.append(f"   {anomaly.persistence_label}")

    # 首逾率 + 较昨日
    lines.append(f"   首逾 {anomaly.current_rate*100:.2f}%"
                 f"（昨 {yesterday_rate*100:.2f}% {anomaly.dod_change_pp*100:+.2f}pp）")

    # 到期单量
    case_pct = (anomaly.case_count / overall_cases * 100) if overall_cases > 0 else 0.0
    lines.append(f"   到期单量 {anomaly.case_count} 单（{case_pct:.1f}%）")

    # 主要 APP（极简）
    if anomaly.top_apps:
        app_names = "、".join([app.app_name for app in anomaly.top_apps[:2]])
        lines.append(f"   主要APP：{app_names}")

    return lines


def _overall_text_rows(decision: MultiDimAlertDecision, country_code: str) -> List[str]:
    """纯文本降级：监控快照（单量视角）。"""
    lines = []
    lines.append(f"到期单量 {decision.overall_case_count:,} 单")
    if decision.overall_target_value > 0:
        lines.append(f"目标首逾：{decision.overall_target_value*100:.2f}%"
                     f"（高于目标 {decision.overall_target_pp*100:+.2f}pp）")
    return lines


# ============================================================
#  管理摘要（首页，30 秒读完）
# ============================================================

def _summary_fields(decision: MultiDimAlertDecision) -> List[dict]:
    """构建管理摘要：整体状态 + Top2 包体 + Top1 Grade + 建议。

    只回答四个问题：
      1. 今天有没有问题？
      2. 哪个包体有问题？
      3. 哪个 Grade 有问题？
      4. 今天找谁处理？
    """
    fields = []
    top3 = _build_top3_deduped(decision)

    # ---- 1. 整体状态 ----
    rate = decision.overall_rate * 100
    dod = decision.overall_dod_change_pp * 100

    if decision.overall_anomaly is not None:
        overall_line = f"🚨 整体首逾率 **{rate:.2f}%**（较昨日 {dod:+.2f}pp），触发告警"
    elif top3:
        overall_line = f"🟡 整体首逾率 **{rate:.2f}%**（较昨日 {dod:+.2f}pp），局部风险"
    else:
        overall_line = f"🟢 整体首逾率 **{rate:.2f}%**（较昨日 {dod:+.2f}pp），各维度正常"

    fields.append(_markdown_block(overall_line))

    if not top3:
        fields.append(_markdown_block("✅ 各维度首逾率均在正常范围，无异常告警。"))
        return fields

    # ---- 2. 异常包体 Top2（product_type / order_type） ----
    body_anomalies = [
        a for a in decision.all_anomalies
        if a.dimension in ("product_type", "order_type")
        and a.is_anomalous
        and not a.is_merged_away
        and not a.is_sample_small
    ]
    body_anomalies.sort(key=lambda x: x.risk_score, reverse=True)

    if body_anomalies:
        lines = ["📌 异常包体："]
        for a in body_anomalies[:2]:
            rate_str = f"{a.current_rate*100:.2f}%"
            # 取变化最显著的方向
            if a.dod_pass:
                change_str = f"较昨日 {a.dod_change_pp*100:+.2f}pp"
            elif a.avg3d_pass:
                change_str = f"较3日均 {a.avg3d_change_pp*100:+.2f}pp"
            elif a.avg7d_pass:
                change_str = f"较7日均 {a.avg7d_change_pp*100:+.2f}pp"
            else:
                change_str = ""
            lines.append(f"　• **{a.bucket_label}** — 首逾 {rate_str}" +
                        (f"  {change_str}" if change_str else ""))
        fields.append(_markdown_block("\n".join(lines)))

    # ---- 3. 异常Grade Top1 ----
    grade_anomalies = [
        a for a in decision.all_anomalies
        if a.dimension == "order_grade"
        and a.risk_score > 0
        and not a.is_merged_away
        and not a.is_sample_small
    ]
    grade_anomalies.sort(key=lambda x: x.risk_score, reverse=True)

    if grade_anomalies:
        a = grade_anomalies[0]
        rate_str = f"{a.current_rate*100:.2f}%"
        if a.dod_pass:
            change_str = f"较昨日 {a.dod_change_pp*100:+.2f}pp"
        elif a.avg3d_pass:
            change_str = f"较3日均 {a.avg3d_change_pp*100:+.2f}pp"
        elif a.avg7d_pass:
            change_str = f"较7日均 {a.avg7d_change_pp*100:+.2f}pp"
        else:
            change_str = ""

        fields.append(_markdown_block(
            f"📌 异常Grade：\n　• **Grade {a.bucket_label}** — 首逾 {rate_str}" +
            (f"  {change_str}" if change_str else "")
        ))

    return fields


# ============================================================
#  统一建议
# ============================================================

def _unified_suggestion(decision: MultiDimAlertDecision) -> str:
    """生成一条统一建议，位于日报末尾。"""
    top3 = _build_top3_deduped(decision)
    if not top3:
        return ""

    # 仅收集最需要立即排查的前 2 个 APP+Grade
    items = []
    seen = set()
    for s in top3[:1]:
        for app in (s.top_apps or [])[:2]:
            key = (app.app_name, app.top_grades[0][0] if app.top_grades else "")
            if key not in seen and key[1]:
                seen.add(key)
                items.append(key)
    # 如果第一个异常维度 APP 不够 2 个，从第二个维度补充
    if len(items) < 2 and len(top3) > 1:
        for app in (top3[1].top_apps or [])[:1]:
            key = (app.app_name, app.top_grades[0][0] if app.top_grades else "")
            if key not in seen and key[1]:
                seen.add(key)
                items.append(key)

    if not items:
        return ""

    lines = [
        "👉 今日建议：优先排查",
        "\n".join(f"　{i+1} **{name}** Grade {grade}" for i, (name, grade) in enumerate(items[:2])),
    ]

    # 判断是否有持续走弱
    has_persistent = any("连续走弱" in (s.persistence_label or "") for s in top3[:2])
    if has_persistent:
        lines.append("若明日继续走弱，升级为风险评审。")
    else:
        lines.append("若明日持续则升级处理。")

    return "\n".join(lines)


# ============================================================
#  今日结论 + 管理动作（详情页使用）
# ============================================================

def _short_conclusion(decision: MultiDimAlertDecision) -> str:
    """今日结论（单量视角：首逾率概览 + 📌 今日重点）。"""
    top3 = _build_top3_deduped(decision)
    rate = decision.overall_rate * 100
    dod = decision.overall_dod_change_pp * 100

    # ---- 首逾率概览 ----
    if decision.overall_anomaly is not None:
        header = f"🚨 今日 {decision.stage} 首逾率 **{rate:.2f}%**（较昨日 {dod:+.2f}pp），整体触发告警"
    elif top3:
        header = f"🟡 今日 {decision.stage} 首逾率 **{rate:.2f}%**（较昨日 {dod:+.2f}pp），局部风险"
    else:
        header = f"🟢 今日 {decision.stage} 首逾率 **{rate:.2f}%**（较昨日 {dod:+.2f}pp），各维度正常"

    if not top3:
        return header

    # ---- 📌 今日重点：按产品去重，同一产品下挂多个 APP（产品 → APP → Grade） ----
    # 按产品分组
    from collections import OrderedDict
    product_groups = OrderedDict()
    for s in sorted(top3, key=lambda x: x.case_count, reverse=True):
        # 产品标签
        if s.level == 3:
            product_label = s.primary_product or s.bucket_label
        else:
            product_label = s.bucket_label

        if product_label not in product_groups:
            product_groups[product_label] = {"total_cases": 0, "apps": OrderedDict()}
        product_groups[product_label]["total_cases"] += s.case_count

        # 收集 APP + Grade
        for app in (s.top_apps or []):
            if app.app_name not in product_groups[product_label]["apps"]:
                product_groups[product_label]["apps"][app.app_name] = app.top_grades

    items = []
    for i, (product_label, group) in enumerate(product_groups.items()):
        num = {0: "①", 1: "②", 2: "③"}.get(i, f"{i+1}")
        line = f"{num} {product_label}（{group['total_cases']}单）"
        if group["apps"]:
            line += "\n主要APP："
            for app_name, grades in group["apps"].items():
                line += f"\n• {app_name}"
                if grades:
                    top_grade = grades[0]
                    line += f"\n　↓ Grade {top_grade[0]}（{top_grade[1]}单）"
        items.append(line)

    if items:
        return header + "\n\n📌 今日重点：\n" + "\n".join(items)

    return header


def _management_action(decision: MultiDimAlertDecision) -> str:
    """生成管理动作引导行（单量视角，引用具体 APP / 产品名）。"""
    top3 = _build_top3_deduped(decision)

    def _collect_app_names(anomalies) -> list:
        """收集所有异常维度的 Top2 APP 名称（去重保持顺序）。"""
        names = []
        seen = set()
        for a in anomalies:
            for app in (a.top_apps or [])[:2]:
                if app.app_name not in seen:
                    seen.add(app.app_name)
                    names.append(app.app_name)
        return names

    if not top3:
        return ""

    # 产品标签（Grade 维度用 primary_product，去重）
    product_names = list(dict.fromkeys(
        s.primary_product if s.level == 3 else s.bucket_label
        for s in top3[:2]
    ))
    app_names = _collect_app_names(top3[:2])
    all_names = product_names + [a for a in app_names if a not in product_names]

    if decision.overall_anomaly is not None:
        has_persistent = any("连续走弱" in (s.persistence_label or "") for s in top3[:2])
        if has_persistent:
            base = (
                f"👉 建议：今日优先排查 **{'、'.join(all_names[:3])}**，"
                f"确认走弱来源并制定应对方案。"
                f"若明日继续走弱，升级为风险评审。"
            )
        else:
            base = (
                f"👉 建议：今日重点关注 **{'、'.join(all_names[:3])}**，"
                f"确认是否为单日波动或趋势性走弱。明日若持续则升级处理。"
            )
        return base
    else:
        base = (
            f"👉 建议：整体策略暂不调整，重点监控 **{'、'.join(all_names[:3])}**。"
            f"若明日扩散至整体或持续走弱，再启动排查。"
        )
        return base


# ============================================================
#  辅助
# ============================================================

def _status_emoji(decision: MultiDimAlertDecision) -> str:
    if decision.overall_anomaly is not None:
        return "🚨"
    elif decision.top3_segments:
        return "🟡"
    else:
        return "🟢"


def _card_color(decision: MultiDimAlertDecision) -> str:
    worst = "GREEN"
    all_anomalous = []
    if decision.overall_anomaly:
        all_anomalous.append(decision.overall_anomaly)
    all_anomalous.extend(decision.top3_segments)

    rank = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
    for a in all_anomalous:
        if rank.get(a.worst_alert_level, 0) > rank.get(worst, 0):
            worst = a.worst_alert_level

    color_map = {"RED": "red", "ORANGE": "orange", "YELLOW": "yellow", "GREEN": "green"}
    return color_map.get(worst, "green")


def _country_flag(country_code: str) -> str:
    flags = {"MX": "🇲🇽", "AR": "🇦🇷"}
    return flags.get(country_code, "")


def _currency_label(country_code: str) -> str:
    currency_map = {"MX": "MXN", "AR": "ARS"}
    return currency_map.get(country_code, "")


# ============================================================
#  飞书发送
# ============================================================

def send_alert_v2(
    decision: MultiDimAlertDecision,
    receive_id: str,
    feishu_client=None,
    prefer_card: bool = True,
) -> dict:
    """发送 V2 告警到飞书。"""
    if feishu_client is None:
        from ..send_feishu import FeishuClient
        feishu_client = FeishuClient()

    if prefer_card:
        card = build_alert_card_v2(decision)
        return feishu_client.send_card(receive_id, card)
    else:
        text = build_text_message_v2(decision)
        return feishu_client.send_text(receive_id, text)
