"""
员工评级系统 V2 — 集中配置

所有规则/凭证/字段口径集中于此。改规则不用动算法（rating_engine.py）。
V2 相对 V1 的唯一变化：数据源从 Excel 改为 Quick BI OpenAPI，评分规则完全保持一致。
"""

import os
import sys
from pathlib import Path

# 定位仓库根目录，加载基础设施
_repo = Path(__file__).resolve()
while not (_repo / ".git").exists() and _repo != _repo.parent:
    _repo = _repo.parent
sys.path.insert(0, str(_repo))
from 基础设施.配置.env import require

# ============================================================
#  版本信息
# ============================================================
VERSION = "V2.1.1"
RELEASE_DATE = "2026-07-14"

# ============================================================
#  Quick BI 认证（复用日报/周报项目同一套凭证）
# ============================================================
QBI_AK = require("QBI_ACCESS_KEY")
QBI_SK = require("QBI_SECRET_KEY")
QBI_ENDPOINT = "quickbi-public.cn-hangzhou.aliyuncs.com"

# API IDs
QBI_API_PERFORMANCE = "c4f429db60b3"   # 业绩（按 statis_date，返回该日起向后至最新可用日的区间）
QBI_API_ATTENDANCE = "7f9969dc9020"    # 出勤/排班（按 day，返回该日起向后约 50 天窗口）

# ============================================================
#  国家 / 部门 / 语言（多国预留：以后做阿根廷版只需改这里，评分引擎不动）
#    MX: COUNTRY="MX" DEPARTMENT="PL"    EXPORT_LANGUAGE="mx"
#    AR: COUNTRY="AR" DEPARTMENT="AR_IN" EXPORT_LANGUAGE="es_ar"
# ============================================================
COUNTRY = "MX"              # MX | AR
DEPARTMENT = "PL"          # 评级部门过滤：PL | AR_IN
LANGUAGE = "zh"            # 主导出语言（中文版恒定输出）
EXPORT_LANGUAGE = "mx"     # 附加导出语言版本：mx | es_ar ...

# ============================================================
#  API 容错
# ============================================================
API_TIMEOUT = 60           # 单次请求超时（秒）
API_RETRIES = 3            # 失败自动重试次数
API_RETRY_BACKOFF = 2.0    # 重试退避基数（秒）：第 n 次失败后 sleep n*backoff

# ============================================================
#  业绩口径
# ============================================================
# 达成率 = (今日回款 / 今日入催) / target
#   — 经真实数据验证：只有除以 target 才落在 0~1.5/2.0，匹配 V1 的封顶与评分区间。
#   — 若写成 repay/enter（恒<1），几乎所有人 pct≈50 → 业绩分 0，评分规则会失真。
PERF_FIELD_ENTER = "today_enter_collect_amount"
PERF_FIELD_REPAY = "today_repay_amount"
PERF_FIELD_TARGET = "target"
PERF_FIELD_STAGE = "overdue_level"       # 逾期阶段（V1 的 stage）
PERF_FIELD_GROUP = "coll_group_name"     # 催收组
PERF_FIELD_CUST = "cust_type"            # 客群（new/old）
PERF_FIELD_NAME = "staff_name"
PERF_FIELD_ACCT = "user_name"
PERF_FIELD_DATE = "statis_date"

# 达成率封顶：D0/D1 封 1.5，其余封 2.0（V1 一致）
STAGE_CAP_150 = {"D0", "D1"}
CAP_150 = 1.5
CAP_200 = 2.0

# 排除的逾期阶段（取数时过滤，不纳入考核）
#   S3 阶段不参与员工评级，在 fetch 层即剔除。
EXCLUDED_STAGES = {"S3"}  # 匹配时忽略大小写与后缀，如 "S3(new)" 也会被排除

# 异常数据过滤（非评分规则）：日订单催回率 = total_repay_num / today_enter_collect_num
#   当该值 >= 100% 时，剔除该工作日数据（详见 rating_engine 注释与 README）。
PERF_FIELD_REPAY_NUM = "total_repay_num"        # 当日结清/回款单量
PERF_FIELD_ENTER_NUM = "today_enter_collect_num"  # 当日入催单量
ORDER_RECOVERY_ANOMALY = 1.0                     # 日订单催回率 >= 此值视为异常，剔除

# 注：达成率考核的是「金额达成率」。日订单催回率仅用于异常数据过滤（见上），不参与评分。

# ============================================================
#  出勤口径（沿用 V1 中文标签语义，未来出现 缺勤/年假 也能正确处理）
# ============================================================
ATT_FIELD_NAME = "name"
ATT_FIELD_ACCT = "employer_no"
ATT_FIELD_DAY = "day"
ATT_FIELD_STATUS = "working_status"
ATT_FIELD_DEPT = "depart_name"
ATT_FIELD_JOIN = "join_date"
ATT_FIELD_RESIGNED = "is_resigned"
ATT_FIELD_STAGE = "overdue_bukcet"   # 出勤记录关联的逾期阶段，用于排除 S3 等不考核阶段

ATT_ABSENT = {"缺勤"}          # 缺勤：每次扣 10
ATT_WORKDAY = {"上班", "年假"}  # 出勤天数：上班 + 年假（年假不扣分）
ATT_LEAVE = {"请假"}           # 请假：每次扣 5
# 休息 / "-" ：既不计出勤也不扣分
ATT_BASE_SCORE = 20
ATT_ABSENT_PENALTY = 10
ATT_LEAVE_PENALTY = 5

ON_JOB_VALUE = "在职"          # is_resigned == 在职 视为在职

# ============================================================
#  评分权重（三期加权业绩）
# ============================================================
WEIGHTS = {
    "current": 0.5,   # 本期
    "prev1": 0.3,     # 上一期
    "prev2": 0.2,     # 上上期
}

# 出勤天数 ≤ 该值时，业绩分封顶（V1：≤6 天封 45）
LOW_ATTENDANCE_DAYS = 6
LOW_ATTENDANCE_PERF_CAP = 45

# ============================================================
#  等级 & 奖金系数（综合评分 → 等级，阈值从高到低）
# ============================================================
GRADE_TABLE = [
    (105, "SSS", 1.30),
    (100, "SS", 1.25),
    (95,  "S",  1.20),
    (90,  "AA", 1.15),
    (85,  "A",  1.10),
    (65,  "B",  1.00),
    (55,  "C",  1.00),
    (0,   "D",  1.00),
]

# ============================================================
#  路径
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LOG_DIR = os.path.join(BASE_DIR, "logs")
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Desktop")

# ============================================================
#  导出字段（中文版列顺序，与 V1 一致；末尾追加 V2.1 评级组成列）
#    合规分 V1 亦未导出，V2 预留
# ============================================================
COLS_ZH = [
    "催员姓名", "组员账号", "当前阶段", "是否在职",
    "达成率_本期", "达成率_近1期", "达成率_近2期",
    "业绩分_本期", "业绩分_近1期", "业绩分_近2期", "业绩分_三期加权",
    "缺勤次数", "请假次数", "出勤天数", "出勤分", "入职分",
    "综合评分", "最终等级", "奖金系数", "最终名次",
    # === V2.1 新增：评级组成，方便员工看提升方向 ===
    "业绩贡献", "出勤贡献", "工龄贡献", "距上一等级",
]

ZH_TO_MX = {
    "催员姓名": "Nombre del agente",
    "组员账号": "ID del equipo",
    "当前阶段": "Etapa actual",
    "是否在职": "Estado laboral",
    "达成率_本期": "Tasa de logro actual",
    "达成率_近1期": "Tasa de logro ciclo anterior",
    "达成率_近2期": "Tasa de logro dos ciclos",
    "业绩分_本期": "Puntaje actual",
    "业绩分_近1期": "Puntaje ciclo anterior",
    "业绩分_近2期": "Puntaje dos ciclos",
    "业绩分_三期加权": "Puntaje ponderado",
    "缺勤次数": "Faltas",
    "请假次数": "Permisos",
    "出勤天数": "Días trabajados",
    "出勤分": "Puntaje de asistencia",
    "入职分": "Puntaje de antigüedad",
    "综合评分": "Puntaje total",
    "最终等级": "Nivel final",
    "奖金系数": "Coeficiente de bono",
    "最终名次": "Ranking final",
    # === V2.1 新增列 ===
    "业绩贡献": "Aporte de desempeño",
    "出勤贡献": "Aporte de asistencia",
    "工龄贡献": "Aporte de antigüedad",
    "距上一等级": "Puntos al siguiente nivel",
}

# ============================================================
#  导出语言版本（附加版）：文件名后缀标签 + 字段映射表
#    以后阿根廷版新增 "es_ar" 一项即可
# ============================================================
EXPORT_LANG_LABELS = {
    "zh": "中文版",
    "mx": "Mexico",
    "es_ar": "Argentina",
}
EXPORT_MAPS = {
    "mx": ZH_TO_MX,
    "es_ar": ZH_TO_MX,   # 预留：阿根廷西语映射，暂复用
}
