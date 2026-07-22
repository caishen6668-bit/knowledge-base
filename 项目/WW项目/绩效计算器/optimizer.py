# -*- coding: utf-8 -*-
"""
WW 智能绩效与人员调配系统 — 人员调配优化器（Rule-Based）

目标：满足需求的前提下，以最少调岗成本实现团队综合能力最大化。

算法：贪心初始分配 + 配对交换改善 + 规则约束校验。
"""

from dataclasses import dataclass, field
from typing import Optional

import config
from utils import experience_score


# ============================================================
# 输出数据结构
# ============================================================

@dataclass
class StaffAssignment:
    """单人单岗分配结果"""
    staff_id: str
    name: str
    queue: str
    composite_score: float
    ability_score: float
    confidence: int
    experience: int
    experience_score: float
    is_locked: bool = False
    is_staying: bool = False       # 是否留在昨天岗位
    warning: Optional[str] = None  # 预警信息


@dataclass
class TransferRecord:
    """单条调岗记录"""
    staff_id: str
    name: str
    from_queue: str
    to_queue: str
    composite_gain: float
    reason: str


@dataclass
class StaffWarning:
    """能力预警"""
    staff_id: str
    name: str
    queue: str
    composite_score: float
    level: str  # "yellow"


@dataclass
class UnmetDemand:
    """未满足需求"""
    queue: str
    required: int
    filled: int
    shortfall: int


@dataclass
class OptimizationResult:
    """优化器完整输出"""
    assignments: list = field(default_factory=list)    # list[StaffAssignment]
    transfers: list = field(default_factory=list)      # list[TransferRecord]
    warnings: list = field(default_factory=list)        # list[StaffWarning]
    unmet_demand: list = field(default_factory=list)    # list[UnmetDemand]
    summary: dict = field(default_factory=dict)

    def print_report(self):
        """打印可读报告"""
        print("\n" + "=" * 60)
        print("  人员调配优化报告")
        print("=" * 60)

        print(f"\n  [总览] {self.summary.get('total_staff', 0)}人, "
              f"调岗 {self.summary.get('total_transfers', 0)}人, "
              f"综合总分 {self.summary.get('total_composite', 0):.1f}, "
              f"稳定性 {self.summary.get('stability_pct', 0):.0f}%")

        print(f"\n  -- 岗位安排 --")
        print(f"  {'姓名':18s} {'工号':10s} {'队列':5s} {'综合分':>6s} {'能力':>8s} {'置信':>4s} {'经验':>4s} {'经验分':>6s} {'锁定':>4s} {'保持':>4s} {'预警':>4s}")
        print(f"  {'-'*75}")
        for a in self.assignments:
            warn = a.warning if a.warning else "-"
            print(f"  {a.name:18s} {a.staff_id:10s} {a.queue:5s} {a.composite_score:6.1f} "
                  f"{a.ability_score:8.4f} {a.confidence:4d} {a.experience:4d} "
                  f"{a.experience_score:6.1f} {'是' if a.is_locked else '':>4s} "
                  f"{'是' if a.is_staying else '':>4s} {warn:>4s}")

        if self.transfers:
            print(f"\n  -- 调岗清单 --")
            print(f"  {'姓名':18s} {'工号':10s} {'从':>5s} {'到':>5s} {'收益':>6s}  原因")
            print(f"  {'-'*65}")
            for t in self.transfers:
                print(f"  {t.name:18s} {t.staff_id:10s} {t.from_queue:>5s} {t.to_queue:>5s} "
                      f"{t.composite_gain:6.1f}  {t.reason}")

        if self.warnings:
            print(f"\n  [!] 能力预警 (综合分 < {config.OPTIMIZER_WARNING_THRESHOLD}):")
            for w in self.warnings:
                print(f"  {w.name} ({w.staff_id}) [{w.queue}] 综合分={w.composite_score:.1f}")

        if self.unmet_demand:
            print(f"\n  [XX] 未满足需求:")
            for u in self.unmet_demand:
                print(f"  {u.queue}: 需要 {u.required}人, 已安排 {u.filled}人, 缺口 {u.shortfall}人")

        print()


# ============================================================
# 评分函数
# ============================================================

def composite_score(ability_score: float, confidence: int, experience_days: int,
                    overall_score: float = None) -> float:
    """计算综合能力评分。

    优先使用 ability_database 中的 OverallScore；
    如果未提供（兼容旧数据或缺失），则退化为自行计算。
    """
    if overall_score is not None:
        return overall_score
    # Fallback: 自行计算（兼容没有 OverallScore 的旧数据）
    ability_norm = ability_score * 100.0 if ability_score <= 1.0 else ability_score
    exp_s = experience_score(experience_days)
    return (ability_norm * config.OPTIMIZER_W_ABILITY
            + confidence * config.OPTIMIZER_W_CONFIDENCE
            + exp_s * config.OPTIMIZER_W_EXPERIENCE)


# ============================================================
# 主优化器
# ============================================================

def optimize(staff_pool, demand, ability_map, yesterday_map, locked=None):
    """执行人员调配优化。

    参数：
        staff_pool:     list[str] — 今日在岗员工工号列表（不含休息）
        demand:         dict[str, int] — 各队列需求人数 {"D0": 4, "D1": 3, "S1": 1}
        ability_map:    dict[str, dict] — 每人每阶段能力
                        {staff_id: {stage: {"ability_score": float, "confidence": int, "experience": int}}}
        yesterday_map:  dict[str, str|None] — 每人昨日队列 {staff_id: queue}，None 表示昨天没来
        locked:         dict[str, str] | None — 锁定岗位 {staff_id: queue}

    返回：
        OptimizationResult
    """
    locked = locked or {}

    # --- 1. 输入校验 ---
    total_demand = sum(demand.values())
    available_count = len(staff_pool)

    # 检查是否有足够的人（排除锁定人员的占位）
    locked_demand = sum(1 for sid, q in locked.items() if sid in staff_pool and demand.get(q, 0) > 0)
    staff_without_locked = [s for s in staff_pool if s not in locked]

    unmet = []
    remaining_demand = dict(demand)
    for sid, q in locked.items():
        if sid in staff_pool and remaining_demand.get(q, 0) > 0:
            remaining_demand[q] -= 1

    if len(staff_without_locked) < sum(remaining_demand.values()):
        # 人员不足
        for q, req in demand.items():
            filled = demand[q] - remaining_demand.get(q, 0)
            if filled < req:
                unmet.append(UnmetDemand(queue=q, required=req, filled=filled, shortfall=req - filled))

    # --- 2. 锁定分配 ---
    assignments = {}        # staff_id → StaffAssignment
    assigned_staff = set()

    for sid, q in locked.items():
        if sid not in staff_pool:
            continue
        ab = _get_ability(ability_map, sid, q)
        comp = composite_score(ab["ability_score"], ab["confidence"], ab["experience"],
                               overall_score=ab["overall_score"])
        exp_s = experience_score(ab["experience"])
        assignments[sid] = StaffAssignment(
            staff_id=sid, name=ab["name"], queue=q,
            composite_score=comp, ability_score=ab["ability_score"],
            confidence=ab["confidence"], experience=ab["experience"],
            experience_score=exp_s, is_locked=True,
            is_staying=(yesterday_map.get(sid) == q),
        )
        assigned_staff.add(sid)

    # --- 3. 贪心初始分配 ---
    # 生成所有 (staff, queue) 候选对
    candidates = []
    for sid in staff_pool:
        if sid in assigned_staff:
            continue
        yesterday_q = yesterday_map.get(sid)
        for q, need in demand.items():
            if need <= 0:
                continue
            ab = _get_ability(ability_map, sid, q)
            comp = composite_score(ab["ability_score"], ab["confidence"], ab["experience"],
                                   overall_score=ab["overall_score"])
            stay_bonus = config.OPTIMIZER_STAY_BONUS if q == yesterday_q else 0
            total = comp + stay_bonus
            candidates.append((total, comp, stay_bonus, sid, q, ab))

    # 按总分降序
    candidates.sort(key=lambda x: x[0], reverse=True)

    # 贪心分配
    demand_remaining = dict(demand)
    # 锁定分配已占位
    for sid, a in assignments.items():
        demand_remaining[a.queue] = max(0, demand_remaining.get(a.queue, 0) - 1)

    for total, comp, stay_bonus, sid, q, ab in candidates:
        if sid in assigned_staff:
            continue
        if demand_remaining.get(q, 0) <= 0:
            continue

        exp_s = experience_score(ab["experience"])
        is_staying = (stay_bonus > 0)
        assignments[sid] = StaffAssignment(
            staff_id=sid, name=ab["name"], queue=q,
            composite_score=comp, ability_score=ab["ability_score"],
            confidence=ab["confidence"], experience=ab["experience"],
            experience_score=exp_s, is_locked=False, is_staying=is_staying,
        )
        assigned_staff.add(sid)
        demand_remaining[q] -= 1

    # --- 4. 配对交换改善 ---
    transfers = []
    assigned_list = [sid for sid in assignments if not assignments[sid].is_locked]

    # 检查是否有未满足的需求（可能在贪心阶段没填满）
    unfilled = [q for q, n in demand_remaining.items() if n > 0]
    # 检查是否有富余的队列
    overfilled = [q for q, n in demand_remaining.items() if n < 0]

    improved = True
    max_iterations = 20
    iteration = 0

    while improved and iteration < max_iterations:
        improved = False
        iteration += 1

        best_gain = -999
        best_swap = None

        for i in range(len(assigned_list)):
            sid_a = assigned_list[i]
            if sid_a not in assignments or assignments[sid_a].is_locked:
                continue
            q_a = assignments[sid_a].queue

            for j in range(i + 1, len(assigned_list)):
                sid_b = assigned_list[j]
                if sid_b not in assignments or assignments[sid_b].is_locked:
                    continue
                q_b = assignments[sid_b].queue

                if q_a == q_b:
                    continue

                # 计算当前分数
                cur_a = _staff_total_score(sid_a, q_a, ability_map, yesterday_map)
                cur_b = _staff_total_score(sid_b, q_b, ability_map, yesterday_map)

                # 计算交换后分数
                new_a = _staff_total_score(sid_a, q_b, ability_map, yesterday_map)
                new_b = _staff_total_score(sid_b, q_a, ability_map, yesterday_map)

                gain = (new_a + new_b) - (cur_a + cur_b)

                if gain > best_gain:
                    best_gain = gain
                    best_swap = (sid_a, sid_b, q_a, q_b, gain)

        if best_swap and best_gain >= config.OPTIMIZER_TRANSFER_THRESHOLD:
            sid_a, sid_b, q_a, q_b, gain = best_swap

            # 执行交换
            _swap_assignments(assignments, sid_a, sid_b, q_a, q_b, ability_map, yesterday_map)
            improved = True

    # --- 5. 检测调岗（含初始分配变动 + 交换变动） ---
    for sid, a in assignments.items():
        yesterday_q = yesterday_map.get(sid)
        if yesterday_q and yesterday_q != a.queue:
            # 计算综合收益
            old_score = _staff_total_score(sid, yesterday_q, ability_map, yesterday_map)
            new_score = _staff_total_score(sid, a.queue, ability_map, yesterday_map)
            gain = new_score - old_score
            reason = _build_transfer_reason(sid, yesterday_q, a.queue, gain, ability_map, yesterday_map)
            transfers.append(TransferRecord(
                staff_id=sid, name=a.name,
                from_queue=yesterday_q, to_queue=a.queue,
                composite_gain=round(gain, 2),
                reason=reason,
            ))

    # --- 6. 检查剩余缺口 ---
    # 清空 Phase 1 中产生的未满足需求，以最终分配为准
    unmet.clear()

    # 重新计算各队列实际分配数
    queue_counts = {}
    for a in assignments.values():
        queue_counts[a.queue] = queue_counts.get(a.queue, 0) + 1

    for q, req in demand.items():
        filled = queue_counts.get(q, 0)
        if filled < req:
            unmet.append(UnmetDemand(queue=q, required=req, filled=filled, shortfall=req - filled))

    # --- 7. 预警检查 ---
    warnings = []
    for a in assignments.values():
        if a.composite_score < config.OPTIMIZER_WARNING_THRESHOLD:
            warnings.append(StaffWarning(
                staff_id=a.staff_id, name=a.name, queue=a.queue,
                composite_score=a.composite_score, level="yellow",
            ))

    # --- 7. 汇总 ---
    total_comp = sum(a.composite_score for a in assignments.values())
    staying_count = sum(1 for a in assignments.values() if a.is_staying)
    total_staff = len(assignments)
    stability_pct = (staying_count / total_staff * 100) if total_staff > 0 else 0

    summary = {
        "total_staff": total_staff,
        "total_transfers": len(transfers),
        "total_composite": round(total_comp, 1),
        "stability_pct": round(stability_pct, 1),
        "staying_count": staying_count,
        "transfer_threshold": config.OPTIMIZER_TRANSFER_THRESHOLD,
        "stay_bonus": config.OPTIMIZER_STAY_BONUS,
    }

    return OptimizationResult(
        assignments=list(assignments.values()),
        transfers=transfers,
        warnings=warnings,
        unmet_demand=unmet,
        summary=summary,
    )


# ============================================================
# 内部辅助函数
# ============================================================

def _get_ability(ability_map, staff_id, queue):
    """获取某人某阶段的能力数据，缺失时返回默认值（尝试从其他阶段获取姓名）。"""
    stages = ability_map.get(staff_id, {})
    entry = stages.get(queue, {})

    # 尝试从其他阶段获取姓名
    name = staff_id
    if entry and entry.get("name"):
        name = entry["name"]
    else:
        for s in stages.values():
            if s.get("name"):
                name = s["name"]
                break

    return {
        "ability_score": entry.get("ability_score", 0.0) if entry else 0.0,
        "confidence": entry.get("confidence", 0) if entry else 0,
        "experience": entry.get("experience", 0) if entry else 0,
        "overall_score": entry.get("overall_score", None) if entry else None,
        "name": name,
    }


def _staff_total_score(staff_id, queue, ability_map, yesterday_map):
    """计算某人分配到某队列的总分（OverallScore + 保持奖励）。"""
    ab = _get_ability(ability_map, staff_id, queue)
    overall = composite_score(ab["ability_score"], ab["confidence"], ab["experience"],
                              overall_score=ab["overall_score"])
    stay_bonus = config.OPTIMIZER_STAY_BONUS if yesterday_map.get(staff_id) == queue else 0
    return overall + stay_bonus


def _build_transfer_reason(staff_id, from_q, to_q, gain, ability_map, yesterday_map):
    """构建调岗原因说明。"""
    parts = [f"{from_q}→{to_q}"]
    if gain >= config.OPTIMIZER_TRANSFER_THRESHOLD:
        parts.append(f"综合收益{gain:.1f}≥{config.OPTIMIZER_TRANSFER_THRESHOLD}，建议调岗")
    elif gain >= 0:
        parts.append(f"综合收益{gain:.1f}，需求驱动调岗")
    else:
        parts.append(f"综合收益{gain:.1f}，需求强制调岗")
    if yesterday_map.get(staff_id) == to_q:
        parts.append("（回归昨日岗位）")
    return "；".join(parts)


def _swap_assignments(assignments, sid_a, sid_b, q_a, q_b, ability_map, yesterday_map):
    """在 assignments 字典中交换两个员工的队列。"""
    a = assignments[sid_a]
    b = assignments[sid_b]

    # 更新 A → q_b
    ab = _get_ability(ability_map, sid_a, q_b)
    comp_a = composite_score(ab["ability_score"], ab["confidence"], ab["experience"],
                             overall_score=ab["overall_score"])
    a.queue = q_b
    a.composite_score = comp_a
    a.ability_score = ab["ability_score"]
    a.confidence = ab["confidence"]
    a.experience = ab["experience"]
    a.experience_score = experience_score(ab["experience"])
    a.is_staying = (yesterday_map.get(sid_a) == q_b)

    # 更新 B → q_a
    ab = _get_ability(ability_map, sid_b, q_a)
    comp_b = composite_score(ab["ability_score"], ab["confidence"], ab["experience"],
                             overall_score=ab["overall_score"])
    b.queue = q_a
    b.composite_score = comp_b
    b.ability_score = ab["ability_score"]
    b.confidence = ab["confidence"]
    b.experience = ab["experience"]
    b.experience_score = experience_score(ab["experience"])
    b.is_staying = (yesterday_map.get(sid_b) == q_a)
