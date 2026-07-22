"""
V2 配置 — 趋势预警引擎

独立于 V1 config.py，不修改 V1 任何配置。
复用 V1 的 ALERT_DISPLAY 图标定义。
"""

from .. import config as v1_config

# ============================================================
#  V2 版本信息
# ============================================================

V2_VERSION = "2.0.0-uat"
V2_PHASE = "3.5"
V2_BUILD = "2026-07-08-rc2"

# ============================================================
#  历史回溯配置
# ============================================================

LOOKBACK_DAYS = 7       # 回溯天数（用于 3d/7d 均值计算）
MIN_LOOKBACK_DAYS = 3   # 最少需要的历史天数（不足则标记数据不足）

# ============================================================
#  目标首逾率（按国家+阶段）
# ============================================================
# 目标值用于 "vs_target" 比较。
# 设为 None 表示该国家/阶段不启用目标比较。

TARGET_RATES = {
    "MX": {
        "D0": 0.30,    # 30% — 当前 ~32%
        "D1": 0.25,    # 25%
        "D-2": 0.08,
        "D-1": 0.12,
        "S1": 0.35,
        "S2": 0.40,
    },
    "AR": {
        "D0": 0.32,    # 32% — 当前 ~34.5%
        "D1": 0.28,
        "D-2": 0.10,
        "D-1": 0.15,
        "S1": 0.38,
        "S2": 0.42,
    },
}

# ============================================================
#  趋势告警阈值（V2 独立于 V1）
# ============================================================
# 每个比较方法有独立的四级阈值（百分点，小数形式）。
# 例如 yellow=0.005 = 0.5pp 变化触发 🟡
#
# 设计原则：
#   - DoD（日环比）：单日波动大，阈值最宽
#   - 3d_avg：消除部分噪声，阈值居中
#   - 7d_avg：最稳定，阈值最严
#   - target：关注绝对偏离，阈值最敏感
#
# 阈值按阶段分组，D0 最宽（波动大），S1/S2 最严（细微变化也关注）。

TREND_THRESHOLDS = {
    "D-2": {
        "dod":    {"yellow": 0.003, "orange": 0.008, "red": 0.015},
        "3d_avg": {"yellow": 0.002, "orange": 0.005, "red": 0.010},
        "7d_avg": {"yellow": 0.0015, "orange": 0.004, "red": 0.008},
        "target": {"yellow": 0.001, "orange": 0.003, "red": 0.005},
    },
    "D-1": {
        "dod":    {"yellow": 0.003, "orange": 0.008, "red": 0.015},
        "3d_avg": {"yellow": 0.002, "orange": 0.005, "red": 0.010},
        "7d_avg": {"yellow": 0.0015, "orange": 0.004, "red": 0.008},
        "target": {"yellow": 0.001, "orange": 0.003, "red": 0.005},
    },
    "D0": {
        "dod":    {"yellow": 0.005, "orange": 0.015, "red": 0.030},
        "3d_avg": {"yellow": 0.004, "orange": 0.012, "red": 0.025},
        "7d_avg": {"yellow": 0.003, "orange": 0.010, "red": 0.020},
        "target": {"yellow": 0.002, "orange": 0.005, "red": 0.010},
    },
    "D1": {
        "dod":    {"yellow": 0.003, "orange": 0.010, "red": 0.020},
        "3d_avg": {"yellow": 0.0025, "orange": 0.008, "red": 0.015},
        "7d_avg": {"yellow": 0.002, "orange": 0.006, "red": 0.012},
        "target": {"yellow": 0.0015, "orange": 0.004, "red": 0.008},
    },
    "S1": {
        "dod":    {"yellow": 0.002, "orange": 0.005, "red": 0.010},
        "3d_avg": {"yellow": 0.0015, "orange": 0.004, "red": 0.008},
        "7d_avg": {"yellow": 0.001, "orange": 0.003, "red": 0.005},
        "target": {"yellow": 0.001, "orange": 0.002, "red": 0.004},
    },
    "S2": {
        "dod":    {"yellow": 0.0015, "orange": 0.004, "red": 0.008},
        "3d_avg": {"yellow": 0.001, "orange": 0.003, "red": 0.005},
        "7d_avg": {"yellow": 0.0008, "orange": 0.002, "red": 0.004},
        "target": {"yellow": 0.0005, "orange": 0.0015, "red": 0.003},
    },
}

# ============================================================
#  比较方法显示信息
# ============================================================

TREND_METHOD_LABELS = {
    "dod": "较昨日",
    "3d_avg": "较近3日均值",
    "7d_avg": "较近7日均值",
    "target": "较目标值",
}

# 默认启用的比较方法（target 仅在配置了目标值时启用）
DEFAULT_TREND_METHODS = ["dod", "3d_avg", "7d_avg"]

# ============================================================
#  分析维度配置
# ============================================================
# V2 趋势分析的维度：
#   - overall: 整体（必定分析）
#   - product_type: 单期 / 分期
#   - order_type: 非分期 / 借款分期 / 展期分期 / 展期N期
#   - order_grade: A~F（从 raw_rows 实时聚合）

TREND_DIMENSIONS = [
    {"key": "overall",     "label": "整体",       "source": "overall"},
    {"key": "product_type", "label": "产品类型",   "source": "dimensions", "field": "product"},
    {"key": "order_type",  "label": "包体",       "source": "dimensions", "field": "order_type"},
    {"key": "order_grade", "label": "订单风控等级", "source": "raw_rows",  "field": "order_grade",
     "buckets": ["A", "B", "C", "D", "E", "F"]},
]

# ============================================================
#  样本过滤（继承 V1 阈值，可在 V2 中覆盖）
# ============================================================

MIN_CASE = v1_config.MIN_CASE          # 50
MIN_AMOUNT = v1_config.MIN_AMOUNT      # 50000

# ============================================================
#  Alert Decision 阈值 — 决定是否发送飞书告警
# ============================================================
# 满足任一条件即触发飞书推送，否则仅记录日志。

# 条件 1: 影响金额 >= 此阈值 → 发送
ALERT_MIN_IMPACT = 100_000            # 10万

# 条件 2: 高于目标值超过此 pp → 发送
ALERT_TARGET_PP = 0.010               # 1.0pp（小数形式）

# 条件 3: 连续 N 天首逾率上升 → 发送
ALERT_CONSECUTIVE_DAYS = 3            # 连续 3 天

# ============================================================
#  Alert Decision V2 配置 — 整体驱动 + 分国家阈值
# ============================================================
# 核心原则: "整体驱动，局部解释"
#   1. 整体趋势必须同时满足 3 个条件才触发告警:
#      DoD 恶化 AND 高于近3日均值 AND 高于近7日均值
#   2. Target（高于目标值）仅作展示参考，不参与 should_alert 判断
#   3. Root Cause 仅负责解释"为什么整体恶化"，不独立触发
#   4. 每个国家独立配置阈值

ALERT_V2_CONFIG = {
    "MX": {
        # 整体趋势阈值（必须全部满足才发送 — AND 逻辑）
        "min_conditions": 3,              # 3/3 必须全部满足
        "dod_worsening_pp": 0.015,        # 1.5pp — 较昨日恶化
        "avg3d_worsening_pp": 0.012,      # 1.2pp — 高于近3日均值
        "avg7d_worsening_pp": 0.010,      # 1.0pp — 高于近7日均值
        "target_pp": 0.025,               # 2.5pp — 高于目标（仅展示，不触发）
        "min_impact_for_mention": 100_000,
    },
    "AR": {
        "min_conditions": 3,              # 3/3 必须全部满足
        "dod_worsening_pp": 0.025,        # 2.5pp — 较昨日恶化
        "avg3d_worsening_pp": 0.022,      # 2.2pp — 高于近3日均值
        "avg7d_worsening_pp": 0.020,      # 2.0pp — 高于近7日均值
        "target_pp": 0.030,               # 3.0pp — 高于目标（仅展示，不触发）
        "min_impact_for_mention": 200_000,
    },
}

# ============================================================
#  Alert Confidence 权重配置
# ============================================================
# 置信度 = 各维度满足条件时累加对应权重，满分 100。
#
# 四个维度:
#   Trend      — DoD 恶化幅度（较昨日上升）
#   Target     — 高于目标值的偏离
#   Consecutive — 连续恶化天数
#   Scale      — 业务规模（到期本金 / 到期笔数）
#
# 每个维度有独立的条件阈值，满足条件 → 累加权重。

ALERT_CONFIDENCE_CONFIG = {
    # 各维度权重（总和 = 100）
    "weights": {
        "trend": 30,        # DoD 恶化
        "target": 25,       # 高于目标
        "consecutive": 20,  # 连续恶化
        "scale": 25,        # 业务规模
    },

    # 分国家阈值 — 满足阈值才计入对应权重
    "MX": {
        "trend_dod_pp": 0.010,           # DoD ≥ 1.0pp → 计入 Trend 权重
        "target_pp": 0.015,              # 高于目标 ≥ 1.5pp → 计入 Target 权重
        "consecutive_days": 3,           # 连续 ≥ 3 天恶化 → 计入 Consecutive 权重
        "scale_min_amount": 500_000,     # 到期本金 ≥ 50万 → 计入 Scale 权重
        "scale_min_cases": 200,          # 到期笔数 ≥ 200 → 计入 Scale 权重
    },
    "AR": {
        "trend_dod_pp": 0.020,           # DoD ≥ 2.0pp（AR 波动大，阈值更宽）
        "target_pp": 0.020,              # 高于目标 ≥ 2.0pp
        "consecutive_days": 3,
        "scale_min_amount": 300_000,     # 到期本金 ≥ 30万（AR 规模较小）
        "scale_min_cases": 100,          # 到期笔数 ≥ 100
    },
}

# ============================================================
#  Business Scale 过滤 — 规模过小默认不告警
# ============================================================
# 整体到期本金或到期笔数低于此阈值 → should_alert=False。
# 这是硬性过滤，在置信度计算之前执行。

ALERT_BUSINESS_SCALE_MIN = {
    "MX": {
        "due_amount": 300_000,   # 到期本金 ≥ 30万
        "case_count": 100,       # 到期笔数 ≥ 100
    },
    "AR": {
        "due_amount": 150_000,   # 到期本金 ≥ 15万
        "case_count": 50,        # 到期笔数 ≥ 50
    },
}

# ============================================================
#  多维度独立监控 — 每个维度独立判断异常
# ============================================================
# 设计原则：
#   - Overall: 3/3 严格 AND（整体信号最可靠）
#   - product_type / order_type: 2/3（子维度默认）
#   - order_grade: 2/3（样本小但阈值更宽）
#   - 每个维度的异常判断不依赖 Overall

DIMENSION_ANOMALY_CONFIG = {
    # ---- 默认阈值（product_type / order_type 使用） ----
    "default": {
        "min_conditions": 2,              # 2/3 — 子维度更宽松
        "dod_worsening_pp": 0.010,        # 1.0pp
        "avg3d_worsening_pp": 0.008,      # 0.8pp
        "avg7d_worsening_pp": 0.006,      # 0.6pp
    },

    # ---- 整体维度 — 分国家严格 3/3 AND ----
    "overall": {
        "MX": {
            "min_conditions": 3,          # 3/3 严格 AND
            "dod_worsening_pp": 0.015,    # 1.5pp
            "avg3d_worsening_pp": 0.012,  # 1.2pp
            "avg7d_worsening_pp": 0.010,  # 1.0pp
        },
        "AR": {
            "min_conditions": 3,          # 3/3 严格 AND
            "dod_worsening_pp": 0.025,    # 2.5pp
            "avg3d_worsening_pp": 0.022,  # 2.2pp
            "avg7d_worsening_pp": 0.020,  # 2.0pp
        },
    },

    # ---- 订单风控等级 — 样本小，阈值更宽 ----
    "order_grade": {
        "min_conditions": 2,              # 2/3
        "dod_worsening_pp": 0.020,        # 2.0pp — 更宽
        "avg3d_worsening_pp": 0.015,      # 1.5pp
        "avg7d_worsening_pp": 0.012,      # 1.2pp
    },
}

# ============================================================
#  Risk Score 权重 — 用于 Top 3 排序
# ============================================================
# conditions(50%) + magnitude(30%) + severity(20%) = 100

RISK_SCORE_CONFIG = {
    "weights": {
        "conditions": 50,    # 几个条件通过
        "magnitude": 30,     # 变化幅度相对阈值
        "severity": 20,      # 最严重的 alert_level
    },
    "magnitude_cap_multiplier": 3.0,  # 3x 阈值 → magnitude 满分
}

# ============================================================
#  子维度样本量过滤 — 样本过小不参与 Top 3
# ============================================================

DIMENSION_SAMPLE_MIN = {
    "overall": {
        "min_cases": 100,
        "min_amount": 300_000,
    },
    "product_type": {
        "min_cases": 50,
        "min_amount": 100_000,
    },
    "order_type": {
        "min_cases": 30,
        "min_amount": 50_000,
    },
    "order_grade": {
        "min_cases": 20,          # Grade E/F 可能很少
        "min_amount": 30_000,
    },
    "default": {
        "min_cases": 30,
        "min_amount": 50_000,
    },
}

# ============================================================
#  轻量动作建议映射 — 不修改 action_engine.py
# ============================================================
# 当 Overall 正常（action_engine 不触发）时，
# 子维度异常使用此映射生成简洁的一行建议。

SIMPLE_ACTION_MAP = {
    "grade_A_B": {
        "action": "复核高风险案件准入与分案策略，排查主要异常 APP",
        "reason": "高风险等级首逾上升",
    },
    "grade_C_D": {
        "action": "确认近期风控策略是否有调整，排查主要异常 APP 与放款渠道",
        "reason": "中风险等级对策略变更敏感",
    },
    "grade_E_F": {
        "action": "排查放款渠道与客户质量，确认是否为单一大额 APP 驱动",
        "reason": "低风险客户异常逾期",
    },
    "product_单期": {
        "action": "优先查看主要异常 APP（Top2），再下钻至对应 Grade 确认风险集中点",
        "reason": "非分期产品首逾上升",
    },
    "product_分期": {
        "action": "优先查看借款分期、展期分期、展期N期的分布，定位异常 APP 与 Grade",
        "reason": "分期产品首逾上升",
    },
    # 注: order_非分期 已合并到 product_单期（同一业务实体），此 key 保留但不再命中
    "order_借款分期": {
        "action": "定位借款分期中 Grade 级别走弱集中点，排查主要异常 APP",
        "reason": "借款分期首逾上升",
    },
    "order_展期分期": {
        "action": "排查展期分期客户资质与历史逾期变化，确认主要异常 APP",
        "reason": "展期分期首逾上升",
    },
    "order_展期N期": {
        "action": "评估展期N期客户还款能力与展期政策，确认主要异常 APP",
        "reason": "展期N期首逾上升",
    },
    "default": {
        "action": "持续监控该维度趋势，确认是否为短期波动",
        "reason": "",
    },
}

# ============================================================
#  业务模型 — 维度树与显示名称映射
# ============================================================
# 将 trend_engine 产出的扁平 (dimension, bucket) 映射到新业务层级树。
# 核心变化:
#   1. "单期" + "非分期" → 合并为 "非分期产品"（同一业务实体）
#   2. 产品分类下钻为 分期子类型（借款分期/展期分期/展期N期）
#   3. 风控等级是叶子节点的子层级，不是平级维度
#
# 不修改 trend_engine.py — 仅在下游做映射。

BUSINESS_MODEL = {
    # ---- 维度键映射 ----
    # (dimension, bucket) → 新业务身份
    "key_map": {
        # 产品分类（level 1）
        ("product_type", "单期"): {
            "dim_label": "非分期产品",
            "bucket_label": "非分期产品",
            "level": 1,
            "parent_key": "",
        },
        ("product_type", "分期"): {
            "dim_label": "分期产品",
            "bucket_label": "分期产品",
            "level": 1,
            "parent_key": "",
        },

        # 分期子类型（level 2，父=分期产品）
        ("order_type", "借款分期"): {
            "dim_label": "分期产品",
            "bucket_label": "借款分期",
            "level": 2,
            "parent_key": "product_type:分期",
        },
        ("order_type", "展期分期"): {
            "dim_label": "分期产品",
            "bucket_label": "展期分期",
            "level": 2,
            "parent_key": "product_type:分期",
        },
        ("order_type", "展期N期"): {
            "dim_label": "分期产品",
            "bucket_label": "展期N期",
            "level": 2,
            "parent_key": "product_type:分期",
        },

        # 风控等级（level 3，父=其所属的产品/order_type）
        # order_grade 的动态父节点在 _apply_business_model 中计算
    },

    # ---- 合并规则 ----
    # 这些 key 将被标记为 is_merged_away=True，不参与 Top3
    "merged_keys": [
        ("order_type", "非分期"),   # 与 ("product_type", "单期") 是同一业务实体
    ],

    # ---- 层级树（用于 Root Cause 展示和路径构建） ----
    "tree": {
        "非分期产品": {
            "level": 1,
            "parent": None,
            "children": ["order_grade"],
        },
        "分期产品": {
            "level": 1,
            "parent": None,
            "children": ["借款分期", "展期分期", "展期N期"],
        },
        "借款分期": {
            "level": 2,
            "parent": "分期产品",
            "children": ["order_grade"],
        },
        "展期分期": {
            "level": 2,
            "parent": "分期产品",
            "children": ["order_grade"],
        },
        "展期N期": {
            "level": 2,
            "parent": "分期产品",
            "children": ["order_grade"],
        },
    },
}
