"""
首逾智能预警系统 — 集中配置

所有可修改参数统一管理。后续添加国家/维度/阈值只需改此文件。
"""

import os
import sys
from pathlib import Path

# 定位仓库根目录，加载基础设施
_repo = Path(__file__).resolve()
while not (_repo / ".git").exists() and _repo != _repo.parent:
    _repo = _repo.parent
sys.path.insert(0, str(_repo))
from 基础设施.配置.env import require, get

# ============================================================
#  Quick BI 认证
# ============================================================
QBI_AK = require("QBI_ACCESS_KEY")
QBI_SK = require("QBI_SECRET_KEY")
QBI_ENDPOINT = "quickbi-public.cn-hangzhou.aliyuncs.com"

# API IDs
QBI_API_RECOVERY = "524c3ccd429c"   # 各阶段回收率
QBI_API_CASES = "c2f93e0fa45b"      # 到期案件量

# ============================================================
#  飞书配置
# ============================================================
FEISHU_APP_ID = require("FEISHU_COLLECTION_APP_ID")
FEISHU_APP_SECRET = require("FEISHU_COLLECTION_APP_SECRET")
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
FEISHU_CHAT_LIST_URL = "https://open.feishu.cn/open-apis/im/v1/chats"

# 飞书群聊 ID（可通过环境变量覆盖）
FEISHU_CHAT_MX = os.environ.get(
    "FEISHU_CHAT_MX", "oc_8b5ef4aee4e93b29326cd8c0f3c24d90")
FEISHU_CHAT_AR = os.environ.get(
    "FEISHU_CHAT_AR", "oc_8b5ef4aee4e93b29326cd8c0f3c24d90")

# ============================================================
#  国家配置
# ============================================================
COUNTRIES = {
    "MX": {
        "name": "🇲🇽 墨西哥",
        "code": "MX",
        "apps": ["AndaLana", "Cridit", "Kredizo", "ServiCash", "TruCred"],
        "chat_id": FEISHU_CHAT_MX,
    },
    "AR": {
        "name": "🇦🇷 阿根廷",
        "code": "AR",
        "apps": ["Instamonei"],
        "chat_id": FEISHU_CHAT_AR,
    },
}

# ============================================================
#  预警阈值 — 每个阶段独立阈值
# ============================================================
# 逾期率变化（绝对值，百分点）超过阈值 → 告警
# D0 容忍度更高（波动大），S1/S2 容忍度低（细微变化也需关注）
ALERT_RULES = {
    "D-2": {"red": 2,   "orange": 1,   "yellow": 0.5},
    "D-1": {"red": 2,   "orange": 1,   "yellow": 0.5},
    "D0":  {"red": 5,   "orange": 3,   "yellow": 1},
    "D1":  {"red": 2,   "orange": 1,   "yellow": 0.5},
    "S1":  {"red": 1,   "orange": 0.5, "yellow": 0.2},
    "S2":  {"red": 0.5, "orange": 0.3, "yellow": 0.1},
}

# 告警图标和颜色
ALERT_DISPLAY = {
    "RED":    {"icon": "🔴", "label": "严重", "color": "red"},
    "ORANGE": {"icon": "🟠", "label": "警告", "color": "orange"},
    "YELLOW": {"icon": "🟡", "label": "关注", "color": "yellow"},
    "GREEN":  {"icon": "🟢", "label": "正常", "color": "green"},
}

# ============================================================
#  样本过滤（低于阈值不报警，避免小样本噪声）
# ============================================================
MIN_CASE = 50         # 最小订单数
MIN_AMOUNT = 50000    # 最小入催本金

# ============================================================
#  连续异常监控（默认关闭，后续按需开启）
# ============================================================
ENABLE_CONTINUOUS_ALERT = False
CONTINUOUS_DAYS_UP = 3         # 连续 N 天环比上涨
CONTINUOUS_DAYS_ABOVE_AVG = 5  # 连续 N 天高于近 7 日均值

# ============================================================
#  分析维度配置（声明式 — 增删维度只需改这里，引擎自动适配）
# ============================================================
# ============================================================
#  层级下钻顺序（从整体到最细粒度）
# ============================================================
# 分析流程：整体 → 产品类型 → 包体 → 订单风控等级
# 引擎按此顺序逐层下钻，每层用 raw_rows 做交叉过滤
HIERARCHY = ["product_type", "package", "risk_level"]

DIMENSIONS = {
    "product_type": {
        "label": "产品类型",
        "field": "product",              # quickbi 中映射后的字段（单期/分期）
        "source_field": "order_type",    # raw_rows 中的原始字段
        "filter_fn": "map",              # 用 ORDER_TYPE_MAP 映射
        "buckets": ["单期", "分期"],
        "enabled": True,
        "order": 1,                      # 下钻顺序：第一层
    },
    "package": {
        "label": "包体",
        "field": "order_type",           # quickbi 中原始 order_type 值
        "source_field": "order_type",    # raw_rows 中的原始字段
        "filter_fn": "direct",           # 直接取 order_type 值
        "buckets": ["非分期", "借款分期", "展期分期", "展期N期"],
        "enabled": True,
        "order": 2,                      # 下钻顺序：第二层
    },
    "risk_level": {
        "label": "订单风控等级",
        "field": "cust_type",
        "source_field": "cust_type",
        "filter_fn": "direct",
        "buckets": ["新客", "老客"],
        "enabled": True,
        "order": 3,                      # 下钻顺序：第三层
    },
    # === 预留扩展维度（暂未启用） ===
    # "member_level": {
    #     "label": "会员等级",
    #     "field": "member_level",
    #     "source_field": "member_level",
    #     "filter_fn": "direct",
    #     "buckets": [],
    #     "enabled": False,
    #     "order": 4,
    # },
    # "channel": {
    #     "label": "渠道",
    #     "field": "channel",
    #     "source_field": "channel",
    #     "filter_fn": "direct",
    #     "buckets": [],
    #     "enabled": False,
    #     "order": 5,
    # },
}

# 向后兼容别名
ANALYSIS_DIMENSIONS = DIMENSIONS

# ============================================================
#  比较方法配置
# ============================================================
# 每种比较方法的显示名称和阈值适用
COMPARISON_METHODS = {
    "vs_yesterday": {
        "label": "较昨日",
        "description": "对比昨天同维度数据",
    },
    "vs_7days": {
        "label": "较7日均值",
        "description": "对比过去7天均值",
    },
    "vs_target": {
        "label": "较目标",
        "description": "对比预设目标值",
    },
}

# 默认启用的比较方法
DEFAULT_COMPARISONS = ["vs_yesterday", "vs_7days"]

# ============================================================
#  首逾指标定义
# ============================================================
# 首逾率 = 1 - 回收率，关注 D0/D1 阶段
FIRST_OVERDUE_STAGES = ["D0", "D1"]

# API 字段映射
STAGE_KEY_MAP = {
    "D_2": "D-2", "D_1": "D-1", "D0": "D0",
    "D1": "D1", "S1": "S1", "S2": "S2",
}

# 订单类型 → 产品分类
# ⚠️ DEPRECATED: 后续版本将用 mult_no 替代此映射
#   mult_no = 1  → 单期
#   mult_no >= 2 → 分期
ORDER_TYPE_MAP = {
    "借款分期": "分期", "展期分期": "分期", "展期N期": "分期",
    "非分期": "单期",
}

# ============================================================
#  字段验证结论（2026-07-07 UAT）
# ============================================================
# ✅ due_case      — 到期笔数（100%非空，在回收率 API 中直接提供）
# ✅ mult_no       — 分期期数（1=单期, 2=2期, 3=3期, 4=4期）
# ✅ order_grade   — 订单风控等级（A~F, 6级），比 cust_type 更细粒度
# ⚠️ cust_type     — 保留兼容，不再作为主要风控维度
# ❌ due_amt       — Deprecated。公式: due_amt = D_2_due_amt + D_3_pay_amt
#                    不是到期本金，禁止用于权重/贡献/影响金额计算
# 📝 影响金额       — 统一使用 {stage}_due_amt（如 D0_due_amt, D1_due_amt）

# ============================================================
#  展示配置
# ============================================================
HIDE_GREEN_NODES = True      # 默认隐藏 GREEN 节点，仅展示异常
SHOW_IMPACT_AMOUNT = True    # 显示影响金额

# ============================================================
#  版本信息
# ============================================================
VERSION = "1.0.2"
BUILD = "2026-07-07"


# ============================================================
#  配置校验（启动时调用）
# ============================================================

_REQUIRED_CONFIG = {
    "QBI_AK": "Quick BI Access Key",
    "QBI_SK": "Quick BI Secret Key",
    "QBI_ENDPOINT": "Quick BI Endpoint",
    "QBI_API_RECOVERY": "Quick BI Recovery API ID",
    "QBI_API_CASES": "Quick BI Case API ID",
    "FEISHU_APP_ID": "飞书 App ID",
    "FEISHU_APP_SECRET": "飞书 App Secret",
    "COUNTRIES": "国家配置字典",
}

_REQUIRED_COUNTRY_FIELDS = ["name", "code", "apps", "chat_id"]
_REQUIRED_ALERT_STAGES = ["D-2", "D-1", "D0", "D1", "S1", "S2"]


def validate_config():
    """
    启动时校验所有必需配置。

    Returns:
        [] 如果所有配置正确，否则返回错误消息列表。
    """
    errors = []

    # 1. 检查顶层必需字段
    for field, label in _REQUIRED_CONFIG.items():
        val = globals().get(field)
        if val is None or val == "":
            errors.append(f"缺少配置: {label} ({field})")

    # 2. 检查 COUNTRIES
    countries = globals().get("COUNTRIES", {})
    if not isinstance(countries, dict) or len(countries) == 0:
        errors.append("COUNTRIES 配置为空或格式错误")
    else:
        for cc, cfg in countries.items():
            if not isinstance(cfg, dict):
                errors.append(f"COUNTRIES['{cc}'] 不是字典类型")
                continue
            for f in _REQUIRED_COUNTRY_FIELDS:
                if f not in cfg or cfg[f] == "" or cfg[f] is None:
                    errors.append(f"COUNTRIES['{cc}'] 缺少字段: {f}")
            # apps 必须是列表
            if isinstance(cfg.get("apps"), list) and len(cfg["apps"]) == 0:
                errors.append(f"COUNTRIES['{cc}'].apps 为空列表")

    # 3. 检查 ALERT_RULES 阶段完整性
    alert_rules = globals().get("ALERT_RULES", {})
    for stage in _REQUIRED_ALERT_STAGES:
        if stage not in alert_rules:
            errors.append(f"ALERT_RULES 缺少阶段: {stage}")

    # 4. 检查 STAGE_KEY_MAP
    stage_map = globals().get("STAGE_KEY_MAP", {})
    if len(stage_map) != 6:
        errors.append(f"STAGE_KEY_MAP 应有 6 个阶段映射，实际: {len(stage_map)}")

    # 5. 检查 ORDER_TYPE_MAP
    order_map = globals().get("ORDER_TYPE_MAP", {})
    required_orders = ["非分期", "借款分期", "展期分期", "展期N期"]
    for ot in required_orders:
        if ot not in order_map:
            errors.append(f"ORDER_TYPE_MAP 缺少: {ot}")

    # 6. 检查飞书 Chat ID（非空字符串，但允许为占位符）
    for cc, cfg in countries.items() if isinstance(countries, dict) else {}:
        if isinstance(cfg, dict):
            chat_id = cfg.get("chat_id", "")
            if not chat_id or not chat_id.startswith("oc_"):
                errors.append(f"COUNTRIES['{cc}'].chat_id 格式异常: {chat_id[:20]}...")

    # 7. 检查 VERSION
    version = globals().get("VERSION", "")
    if not version:
        errors.append("VERSION 未设置")

    return errors
