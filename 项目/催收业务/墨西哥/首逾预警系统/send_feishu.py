"""
飞书消息发送

- FeishuClient: 飞书 API 封装（认证、发送卡片/文本、列出群聊）
- build_alert_card / build_text_message: 从 FormattedReport 生成消息
- V1.1: 管理层报告格式 — 紧凑、聚焦异常来源 + 建议
"""

import json
import time
from datetime import datetime

import requests

from . import config
from .report import FormattedReport, ReportSection


# ============================================================
#  飞书 API 客户端
# ============================================================

class FeishuClient:
    """飞书 API 客户端"""

    def __init__(self, app_id=None, app_secret=None):
        self.app_id = app_id or config.FEISHU_APP_ID
        self.app_secret = app_secret or config.FEISHU_APP_SECRET
        self._token = None
        self._token_expire = 0

    def _get_token(self):
        if self._token and time.time() < self._token_expire:
            return self._token
        resp = requests.post(
            config.FEISHU_TOKEN_URL,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"Feishu auth failed: {data.get('msg')}")
        self._token = data["tenant_access_token"]
        self._token_expire = time.time() + data.get("expire", 7200) - 300
        return self._token

    def _headers(self):
        return {"Authorization": f"Bearer {self._get_token()}"}

    @staticmethod
    def _detect_id_type(receive_id):
        return "open_id" if receive_id.startswith("ou_") else "chat_id"

    def list_chats(self):
        """列出所有群聊"""
        all_chats = []
        page_token = None
        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(
                config.FEISHU_CHAT_LIST_URL,
                headers=self._headers(), params=params, timeout=15,
            )
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"List chats failed: {data.get('msg')}")
            items = data.get("data", {}).get("items", [])
            all_chats.extend(items)
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token")
        return all_chats

    def send_card(self, receive_id, card):
        """发送 interactive 卡片消息"""
        id_type = self._detect_id_type(receive_id)
        if isinstance(card, dict):
            card = json.dumps(card, ensure_ascii=False)
        payload = {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": card,
        }
        resp = requests.post(
            config.FEISHU_MSG_URL + f"?receive_id_type={id_type}",
            headers=self._headers(), json=payload, timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"Send failed: {data.get('msg')} (code={data.get('code')})")
        return data

    def send_text(self, receive_id, text):
        """发送纯文本消息（降级方案）"""
        id_type = self._detect_id_type(receive_id)
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        resp = requests.post(
            config.FEISHU_MSG_URL + f"?receive_id_type={id_type}",
            headers=self._headers(), json=payload, timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"Send failed: {data.get('msg')} (code={data.get('code')})")
        return data


# ============================================================
#  管理层飞书卡片 — 单国家
# ============================================================

def build_alert_card(report: FormattedReport) -> dict:
    """V1 Release 飞书卡片 — 手机优化版"""
    elements = []

    # === ① 摘要（最前面，一行） ===
    if report.executive_summary:
        # 处理多行摘要
        for line in report.executive_summary.split("\n"):
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": line}
            })

    # === ② 连续异常 🔥 ===
    if report.continuous_alert and report.continuous_alert.is_continuous_anomaly:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"🔥 {report.continuous_alert.warning_text}"
            }
        })

    # === ③ 层级下钻（紧凑，仅异常节点） ===
    if report.tree_rows:
        elements.append({"tag": "hr"})
        row_texts = [row["text"] for row in report.tree_rows]

        # 手机每屏约 6-8 行，分批
        for i in range(0, len(row_texts), 8):
            chunk = row_texts[i:i + 8]
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n".join(chunk)}
            })

    # === ④ 建议 ===
    if report.recommendations:
        elements.append({"tag": "hr"})
        rec_lines = ["**💡 建议**"] + [f"• {r}" for r in report.recommendations]
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(rec_lines)}
        })

    # === ⑤ 底部（业务日期 + 生成时间） ===
    elements.append({"tag": "hr"})
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"业务日期：{report.business_date}  |  生成时间：{gen_time}  |  {report.stage}  |  v{config.VERSION}"
        }
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": report.summary_title},
            "template": report.summary_color,
        },
        "elements": elements,
    }


# ============================================================
#  管理层飞书卡片 — 多国家合并
# ============================================================

def build_combined_alert_card(reports: list) -> dict:
    """V1 Release 多国合并卡片 — 手机优化版"""
    elements = []

    # === ① 摘要汇总 ===
    summary_lines = []
    for r in reports:
        if r.executive_summary:
            for line in r.executive_summary.split("\n"):
                summary_lines.append(line)
    if summary_lines:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(summary_lines)}
        })

    # === ② 连续异常 ===
    for r in reports:
        if r.continuous_alert and r.continuous_alert.is_continuous_anomaly:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"🔥 {r.continuous_alert.warning_text}"}
            })

    # === ③ 各国层级下钻 ===
    for i, report in enumerate(reports):
        if not report.tree_rows:
            continue
        elements.append({"tag": "hr"})
        # 国家标签
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{report.country_name}**"}
        })
        row_texts = [row["text"] for row in report.tree_rows]
        for j in range(0, len(row_texts), 8):
            chunk = row_texts[j:j + 8]
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "\n".join(chunk)}
            })

    # === ④ 合并建议 ===
    all_recs = list(dict.fromkeys(
        rec for report in reports for rec in report.recommendations))[:4]
    if all_recs:
        elements.append({"tag": "hr"})
        rec_lines = ["**💡 建议**"] + [f"• {r}" for r in all_recs]
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(rec_lines)}
        })

    # === ⑤ 底部（业务日期 + 生成时间） ===
    elements.append({"tag": "hr"})
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    # 多国合并：取第一个 report 的日期（同一批次日期相同）
    biz_date = reports[0].business_date if reports else ""
    run_date_str = reports[0].run_date if reports else ""
    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"业务日期：{biz_date}  |  生成时间：{gen_time}  |  {'  '.join(r.stage for r in reports)}  |  v{config.VERSION}"
        }
    })

    has_any_anomaly = any(r.has_anomaly for r in reports)
    worst_color = "green"
    for r in reports:
        if r.has_anomaly and r.summary_color in ("red", "orange", "yellow"):
            worst_color = r.summary_color
            break

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "🚨 首逾每日预警" if has_any_anomaly else "🟢 首逾每日正常"},
            "template": worst_color,
        },
        "elements": elements,
    }


# ============================================================
#  纯文本降级
# ============================================================

def build_text_message(report: FormattedReport) -> str:
    """单国家纯文本降级消息（V1.1 层级下钻格式）"""
    sign = "+" if report.overall_change_abs >= 0 else ""
    icon = report.overall_alert_icon

    lines = [
        f"{report.summary_title}",
        f"{report.country_name}  整体首逾 {report.overall_rate:.2%}  较上周 {sign}{report.overall_change_abs:.2%}  {icon}",
        f"本周 {report.due_week_current} vs 上周 {report.due_week_baseline} | 阶段 {report.stage}",
        "─" * 40,
    ]

    # 层级下钻树
    if report.tree_rows:
        lines.append("")
        lines.append("📊 层级下钻:")
        for row in report.tree_rows:
            lines.append(row["text"])

    # 连续异常
    if report.continuous_alert and report.continuous_alert.is_continuous_anomaly:
        lines.append("")
        lines.append(f"🔥 {report.continuous_alert.warning_text}")

    # 建议
    if report.recommendations:
        lines.append("")
        lines.append("💡 建议:")
        for rec in report.recommendations:
            lines.append(f"  • {rec}")

    lines.append("")
    lines.append(f"业务日期：{report.business_date}  |  阶段 {report.stage}  |  v{config.VERSION}")
    return "\n".join(lines)


def build_combined_text_message(reports: list) -> str:
    """多国家纯文本降级消息（V1.1 层级下钻格式）"""
    lines = ["🚨 首逾每日预警", "━" * 40]

    for report in reports:
        sign = "+" if report.overall_change_abs >= 0 else ""
        icon = report.overall_alert_icon

        lines.append("")
        lines.append(f"{report.country_name}")
        lines.append(f"整体首逾 {report.overall_rate:.2%}  较上周 {sign}{report.overall_change_abs:.2%}  {icon}")

        # 层级下钻树
        if report.tree_rows:
            for row in report.tree_rows:
                lines.append(row["text"])

        if report.continuous_alert and report.continuous_alert.is_continuous_anomaly:
            lines.append(f"🔥 {report.continuous_alert.warning_text}")

    # 合并建议
    all_recs = []
    for report in reports:
        all_recs.extend(report.recommendations)
    all_recs = list(dict.fromkeys(all_recs))[:4]

    if all_recs:
        lines.append("")
        lines.append("💡 建议:")
        for rec in all_recs:
            lines.append(f"  • {rec}")

    lines.append("")
    biz_date = reports[0].business_date if reports else ""
    lines.append(f"业务日期：{biz_date}  |  v{config.VERSION}")
    return "\n".join(lines)


# ============================================================
#  运行失败告警卡片
# ============================================================

def build_failure_card(error_msg: str, business_date: str, run_date: str) -> dict:
    """Quick BI 获取失败时发送的告警卡片"""
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⚠️ 每日首逾预警 — 运行失败"},
            "template": "red",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**❌ 本次运行失败，未发送预警数据。**"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**失败原因：**\n{error_msg}"
                }
            },
            {"tag": "hr"},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"业务日期：{business_date}  |  运行日期：{run_date}  |  生成时间：{gen_time}  |  v{config.VERSION}"
                }
            },
        ],
    }


def build_failure_text(error_msg: str, business_date: str, run_date: str) -> str:
    """运行失败纯文本降级消息"""
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    return "\n".join([
        "⚠️ 【每日首逾预警】运行失败",
        "",
        f"原因：{error_msg}",
        "",
        f"业务日期：{business_date}",
        f"运行日期：{run_date}",
        f"生成时间：{gen_time}",
        f"Version: v{config.VERSION}",
    ])


# ============================================================
#  辅助
# ============================================================

def _circle_number(index: int) -> str:
    """序号转圆圈数字 ① ② ③ ..."""
    circles = "①②③④⑤⑥⑦⑧⑨⑩"
    if index < len(circles):
        return circles[index]
    return f"{index + 1}."
