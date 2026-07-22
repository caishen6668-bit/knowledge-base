"""
异常持续时间（Persistence）模块

纯辅助信息，不参与 Risk Score、不参与 should_alert、不参与 Root Cause。
仅用于帮助管理层判断：这是偶发波动，还是持续恶化。

算法：
  - 沿用 V2 多维度独立监控的 AND 逻辑判断每天是否异常
  - 对每个维度+bucket，回溯 N 天，构建异常布尔序列
  - 从序列中计算持续状态标签

不修改: trend_engine.py / dimension_scorer.py / root_cause.py / action_engine.py
"""

from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta

from . import config as v2_config
from .trend_engine import _fetch_historical_snapshots, _extract_dimension_rates


# ============================================================
#  配置
# ============================================================

PERSISTENCE_LOOKBACK_DAYS = 14       # 回溯天数（用于判断连续恶化）
PERSISTENCE_EXTRA_7D = 7             # 额外天数（用于计算 7d 均值）


# ============================================================
#  阈值解析（复用 dimension_scorer 逻辑）
# ============================================================

def _resolve_dim_config(dimension: str, country_code: str) -> dict:
    """解析某个维度在某国家的异常阈值配置。

    与 dimension_scorer._resolve_dimension_config 逻辑一致。
    """
    dim_cfg = v2_config.DIMENSION_ANOMALY_CONFIG

    if dimension in dim_cfg:
        sub = dim_cfg[dimension]
        if "MX" in sub or "AR" in sub:
            if country_code in sub:
                return sub[country_code]
            return next(iter(sub.values()))
        elif "min_conditions" in sub:
            return sub

    return dict(dim_cfg.get("default", {
        "min_conditions": 2,
        "dod_worsening_pp": 0.010,
        "avg3d_worsening_pp": 0.008,
        "avg7d_worsening_pp": 0.006,
    }))


# ============================================================
#  单日异常判定
# ============================================================

def _is_day_anomalous(
    key: Tuple[str, str],
    day_index: int,
    daily_rates: List[dict],
    cfg: dict,
) -> bool:
    """判断某个维度+bucket 在指定历史日期是否满足异常条件。

    使用与 dimension_scorer 完全相同的 AND 逻辑：
      DoD change >= threshold AND 3d_avg change >= threshold AND 7d_avg change >= threshold

    Args:
        key: (dimension, bucket) 如 ("order_type", "借款分期")
        day_index: 0=today, 1=yesterday, ...
        daily_rates: [today_rates, yesterday_rates, ...] 每个元素是 _extract_dimension_rates 的输出
        cfg: 维度异常配置 {"min_conditions": 2, "dod_worsening_pp": 0.010, ...}

    Returns:
        True 如果该日满足异常条件
    """
    current = daily_rates[day_index].get(key, {})
    current_value = current.get("value", 0.0)

    # 无有效数据
    if current_value == 0.0 and current.get("due", 0) == 0:
        return False

    min_conditions = cfg.get("min_conditions", 2)

    # ---- DoD: 当前 vs 前一天 ----
    dod_pass = False
    if day_index + 1 < len(daily_rates):
        prev = daily_rates[day_index + 1].get(key, {})
        prev_value = prev.get("value")
        if prev_value is not None:
            dod_change = current_value - prev_value
            dod_threshold = cfg.get("dod_worsening_pp", 0.010)
            dod_pass = dod_change > 0 and dod_change >= dod_threshold

    # ---- 3d_avg: 当前 vs 前3日均值 ----
    avg3d_pass = False
    vals_3d = []
    for j in range(1, 4):  # day_index+1, +2, +3
        idx = day_index + j
        if idx < len(daily_rates):
            data = daily_rates[idx].get(key, {})
            if data.get("due", 0) > 0:
                vals_3d.append(data.get("value", 0.0))
    if len(vals_3d) >= 2:
        avg_3d = sum(vals_3d) / len(vals_3d)
        avg3d_change = current_value - avg_3d
        avg3d_threshold = cfg.get("avg3d_worsening_pp", 0.008)
        avg3d_pass = avg3d_change > 0 and avg3d_change >= avg3d_threshold

    # ---- 7d_avg: 当前 vs 前7日均值 ----
    avg7d_pass = False
    vals_7d = []
    for j in range(1, 8):  # day_index+1 through +7
        idx = day_index + j
        if idx < len(daily_rates):
            data = daily_rates[idx].get(key, {})
            if data.get("due", 0) > 0:
                vals_7d.append(data.get("value", 0.0))
    if len(vals_7d) >= 3:
        avg_7d = sum(vals_7d) / len(vals_7d)
        avg7d_change = current_value - avg_7d
        avg7d_threshold = cfg.get("avg7d_worsening_pp", 0.006)
        avg7d_pass = avg7d_change > 0 and avg7d_change >= avg7d_threshold

    conditions_met = sum([dod_pass, avg3d_pass, avg7d_pass])
    return conditions_met >= min_conditions


# ============================================================
#  持续状态标签生成
# ============================================================

def _compute_persistence_label(anomaly_status: List[bool]) -> str:
    """从异常布尔序列生成人类可读的持续状态标签。

    anomaly_status[0] = today, [1] = yesterday, [2] = day before, ...

    规则：
      - 连续 >= 2 天异常 → "🔥 连续走弱 X 天"
      - 今天首次异常（昨天正常，历史无异常）→ "🆕 今日首次出现"
      - 今天再次异常（曾经异常→恢复→今天又异常）→ "↩️ 再次走弱（恢复 X 天后）"
      - 今天恢复正常 → "✅ 已恢复 X 天"
      - 今天正常且昨天也正常 → ""（无需展示）
    """
    if not anomaly_status:
        return ""

    today = anomaly_status[0]

    if today:
        # ---- 今天异常 ----
        consecutive = 0
        for status in anomaly_status:
            if status:
                consecutive += 1
            else:
                break

        if consecutive >= 2:
            return f"🔥 连续走弱 {consecutive} 天"

        # consecutive == 1: 仅今天异常
        past = anomaly_status[1:]
        has_past_anomaly = any(past)

        if has_past_anomaly:
            # 曾经异常过 → 计算正常间隔天数
            recovery_days = 0
            for i in range(1, len(anomaly_status)):
                if not anomaly_status[i]:
                    recovery_days += 1
                else:
                    break
            return f"↩️ 再次走弱（恢复 {recovery_days} 天后）"
        else:
            return "🆕 今日首次出现"

    else:
        # ---- 今天正常 ----
        if len(anomaly_status) > 1 and anomaly_status[1]:
            # 昨天异常 → 今天恢复
            normal_consecutive = 0
            for status in anomaly_status:
                if not status:
                    normal_consecutive += 1
                else:
                    break
            return f"✅ 已恢复 {normal_consecutive} 天"

        return ""


def count_recent_anomalies(
    persistence_map: Dict[Tuple[str, str], str],
    dimension: str,
    bucket: str,
) -> int:
    """从 persistence label 中提取近7天异常次数。

    - "连续走弱 N天" → N
    - "今日首次出现" / "再次走弱" → 1
    - 无 label → 0
    """
    import re
    key = (dimension, bucket)
    label = persistence_map.get(key, "")
    if not label:
        return 0
    if "连续走弱" in label:
        m = re.search(r'(\d+)天', label)
        return int(m.group(1)) if m else 2
    if "今日首次" in label or "再次走弱" in label:
        return 1
    return 0


# ============================================================
#  主入口
# ============================================================

def compute_persistence(
    country_code: str,
    business_date: str,
    stage: str,
    lookback_days: int = PERSISTENCE_LOOKBACK_DAYS,
) -> Dict[Tuple[str, str], str]:
    """计算所有维度+bucket 的异常持续时间标签。

    对每个维度+bucket，回溯 lookback_days 天，
    按 V2 AND 逻辑判断每天是否异常，生成持续状态标签。

    Args:
        country_code: "MX" | "AR"
        business_date: 业务日期 "2026-07-07"
        stage: 分析阶段 "D0" | "D1" | ...
        lookback_days: 回溯天数（默认 14）

    Returns:
        {(dimension, bucket): persistence_label}
        例如: {("order_type", "借款分期"): "🔥 连续走弱 4 天",
               ("order_grade", "F"): "🔥 连续走弱 7 天",
               ("order_grade", "C"): "🆕 今日首次出现"}
    """
    # ---- 1. 拉取历史数据 ----
    # 需要额外 PERSISTENCE_EXTRA_7D 天用于计算 7d 均值
    total_days = lookback_days + PERSISTENCE_EXTRA_7D + 1  # +1 = today
    snapshots = _fetch_historical_snapshots(business_date, country_code, total_days)

    if not snapshots or snapshots[0] is None:
        return {}

    # ---- 2. 提取每天各维度的首逾率 ----
    daily_rates: List[dict] = []
    for snap in snapshots:
        if snap:
            daily_rates.append(_extract_dimension_rates(snap, stage))
        else:
            daily_rates.append({})

    # ---- 3. 收集所有维度+bucket 组合 ----
    all_keys: set = set()
    for rates in daily_rates:
        all_keys.update(rates.keys())

    # ---- 4. 对每个 key 构建异常布尔序列并生成标签 ----
    results: Dict[Tuple[str, str], str] = {}

    for key in all_keys:
        dim, bucket = key
        cfg = _resolve_dim_config(dim, country_code)

        # 构建每天的异常状态（仅 lookback_days + 1 天有意义的判断）
        anomaly_status: List[bool] = []
        for day_idx in range(lookback_days + 1):
            is_anom = _is_day_anomalous(key, day_idx, daily_rates, cfg)
            anomaly_status.append(is_anom)

        # 生成标签
        label = _compute_persistence_label(anomaly_status)
        if label:
            results[key] = label

    return results


# ============================================================
#  便捷函数：将 persistence 标签附加到 DimensionAnomaly 列表
# ============================================================

def attach_persistence_labels(
    anomalies: list,
    persistence_map: Dict[Tuple[str, str], str],
) -> None:
    """将持久性标签附加到 DimensionAnomaly 对象上（原地修改）。

    Args:
        anomalies: DimensionAnomaly 列表
        persistence_map: compute_persistence() 的输出
    """
    for anomaly in anomalies:
        key = (anomaly.dimension, anomaly.bucket)
        if key in persistence_map:
            anomaly.persistence_label = persistence_map[key]


# ============================================================
#  调试输出
# ============================================================

def print_persistence(
    persistence_map: Dict[Tuple[str, str], str],
    country_code: str,
) -> None:
    """终端打印所有维度的持久性标签（调试用途）"""
    if not persistence_map:
        print(f"\n  📋 Persistence ({country_code}): 无数据")
        return

    print(f"\n{'─'*55}")
    print(f"  异常持续时间 — Persistence ({country_code})")
    print(f"{'─'*55}")

    # 按维度分组
    by_dim: Dict[str, list] = {}
    for (dim, bucket), label in sorted(persistence_map.items()):
        by_dim.setdefault(dim, []).append((bucket, label))

    for dim, items in by_dim.items():
        dim_labels = {
            "overall": "整体",
            "product_type": "产品类型",
            "order_type": "包体",
            "order_grade": "订单风控等级",
        }
        print(f"  [{dim_labels.get(dim, dim)}]")
        for bucket, label in items:
            print(f"    {bucket:<16s} → {label}")

    print(f"{'─'*55}")
