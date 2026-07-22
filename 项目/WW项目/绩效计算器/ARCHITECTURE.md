# WW 项目绩效计算器 — 架构设计

> 版本：v1.0（2026-07-15）
> 数据依据：《WW绩效计算器_结构分析报告.md》（桌面 WW项目每日数据 文件夹）
> 定位：每日使用的长期维护工具。核心设计目标 —— **新增目标版本、新增阶段、人员变动、命名变动，都不需要改代码**。

---

## 1. 目录结构

```
D:\knowledge-base\scripts\WW绩效计算器\
├── main.py            # 入口 / CLI / 流程编排（不含任何业务规则）
├── config.py          # 全部可变规则与路径（唯一需要日常维护的文件）
├── models.py          # 数据模型（dataclass，模块间传递的统一语言）
├── excel_reader.py    # 日报读取（只认结构，不认业务）
├── target_manager.py  # 目标表解析 + 版本选择 + 档位查询
├── calculator.py      # 绩效计算（队列→阶段、催回率公式、档位匹配）
├── report_writer.py   # 汇总 Excel 输出
├── utils.py           # 通用工具（名称标准化、文本转数值、日期推断、日志）
├── ARCHITECTURE.md    # 本文档
└── requirements.txt
```

数据目录（config 可改）：`C:\Users\Administrator\Desktop\WW项目每日数据\`

## 2. 数据流

```
                    config.py（规则/路径/映射）
                            │ 被所有模块引用
                            ▼
 日报 N号.xlsx ──► excel_reader ──► list[DailyReport]（含 StaffDayRecord）
                                            │
 目标.xlsx ──► target_manager ──► TargetLibrary（多版本，按生效日期索引）
                                            │
                            ┌───────────────┘
                            ▼
                      calculator ──► list[PerfResult]（每人每日：阶段/催回率/系数/绩效）
                            │
                            ▼
                     report_writer ──► XX-XX号WW项目绩效汇总.xlsx
                                        （每日 sheet + 绩效汇总 sheet）
```

模块间只通过 `models.py` 里的 dataclass 传数据，不传裸 tuple/dict，保证接口稳定。

## 3. 模块职责与接口

### 3.1 config.py — 规则中心（无逻辑，只有数据）

| 配置项 | 作用 | 对应待确认问题 |
|---|---|---|
| `DATA_DIR / TARGET_FILE / OUTPUT_DIR` | 路径 | — |
| `DAILY_FILE_PATTERN` | 日报文件名识别（`(\d+)号.xlsx`） | — |
| `BASE_COLUMNS / DIMENSION_BLOCKS / DIMENSION_FIELDS` | 日报表头→内部字段映射，**按表头文字定位列**，列顺序变化不影响读取 | — |
| `PERCENT_FIELDS / COUNT_FIELDS` | 字段类型（文本→数值转换规则） | — |
| `NAME_PREFIXES` | 姓名前缀清洗列表（Feimi- / Fm- / …），新增前缀加一行即可 | — |
| `QUEUE_STAGE_MAP` | 队列→计价阶段映射（S0→D0），**未列出的队列名 = 阶段名本身**，新阶段零改动 | — |
| `RATE_RULES` | 各阶段催回率公式选择（公式名→calculator 内注册的实现），`"*"` 为默认规则 | Q3（D1 公式） |
| `TARGET_VERSION_POLICY` | `"effective_date"`（按数据日期选版本）/ `"latest"`（永远最新） | Q1 |
| `BAND_BOUNDARY` | 档位边界归属：`"lower_inclusive"` \[min,max) / `"upper_inclusive"` (min,max] | Q4 |
| `RATE_LOOKUP_DECIMALS` | 查档前是否将催回率四舍五入（None=全精度） | — |
| `ABSENT_OUTPUT` | 缺勤日输出 `"zero"` / `"blank"` | — |
| `TOTAL_ROW_LABEL` | 日报汇总行标识（"汇总"） | — |
| `STAGE_NAME_PATTERN` | 目标表阶段名识别正则（自动识别 D-1/D0/D1/S1/S2/D2-3/未来新阶段） | — |

**规则变更 = 只改 config.py。四个待业务确认的问题全部落为 config 开关，确认后改一行。**

### 3.2 models.py — 数据模型

```
DimensionData    9 个指标（分案/应催/结清/未结清/户数催回率/应催总额/回款总额/当期回款/金额催回率）
StaffDayRecord   数据日期 + 姓名(原始/标准化) + 工号 + 部门 + 队列 + 覆盖率
                 + summary(债务汇总维度) + new_case(队列新案维度)
DailyReport      单个日报文件：数据日期、导出日期、记录列表、汇总行、结构警告
TargetBand       min / max / 系数 / 绩效金额（min=None 表示 '-' 无下限）
TargetVersion    生效日期 + {阶段名: [TargetBand]}
TargetLibrary    多版本集合，get_version_for(数据日期)
PerfResult       每人每日结果：工号/姓名/队列/阶段/催回率/系数/绩效/警告
```

**跨表匹配一律用工号**（feimi004~012），姓名仅展示。

### 3.3 excel_reader.py — 日报读取

- `discover_daily_files(data_dir)`：按文件名模式扫描，返回 (日, 路径) 升序列表
- `read_daily_report(path, month_override=None)`：读取单个日报 → `DailyReport`
- `read_all(data_dir, ...)`：批量读取

设计要点：
1. **表头驱动**：不写死列号。第 1 行找基础列 + 两个维度块起点，第 2 行按文字映射 9 个指标列。业务加列/调序不用改代码。
2. **不解释业务**：队列文本原样保留，阶段识别在 calculator 层做 → 新增阶段无需改本模块（需求 5）。
3. 数据日期 = 文件名的"日" + 从 sheet 名 `dataYYYY-MM-DD`（导出日期）推断年月：日 ≤ 导出日 → 同月；否则上月。支持 `--month` 手动覆盖。
4. 全部文本转数值（`'51'`、`'37.25%'`、`'20400.00'`）由 utils 统一处理。
5. 结构校验 + 数据校验（应催户数>分案户数等）产出 warnings，不静默。

### 3.4 target_manager.py — 目标管理

- `load(path) -> TargetLibrary`：
  - 在表内扫描**第 1 行的日期单元格**，每个日期 = 一个版本块的锚点（当前左块 2026-07-13、右块 2026-07-09；以后新增块/新 sheet 自动识别 → 需求 3）
  - 块内沿"阶段列"扫描符合 `STAGE_NAME_PATTERN` 的标签，向下收集 6 档（直到下一个阶段/空行）
  - 加载时校验：区间重叠（D0 0.22~0.23）、区间空洞（S2 0.08~0.085）→ 输出警告
- `TargetLibrary.get_version_for(data_date)`：按 `TARGET_VERSION_POLICY` 选版本（effective_date ≤ 数据日期的最新版）
- `lookup(version, stage, rate) -> (系数, 绩效)`：按 `BAND_BOUNDARY` 匹配档位

### 3.5 calculator.py — 绩效计算

- 公式注册表 `RATE_FORMULAS = {公式名: fn(record)->rate}`，目前两个：
  - `summary_amount_rate`：债务汇总维度·金额催回率（D0 用）
  - `cross_hybrid`：债务汇总·回款总额 ÷ 队列新案·应催总额（S1/默认）
- `calculate_day(report, target_lib) -> list[PerfResult]`：
  1. 队列 → 阶段（`QUEUE_STAGE_MAP`，缺省恒等映射）
  2. 按 `RATE_RULES` 选公式算催回率（覆盖率 0% → 直接 0）
  3. `target_manager.lookup` 查系数/绩效
  4. 阶段在目标表中不存在、队列为空 → 记警告，绩效 0
- 新阶段出现时：目标表加块即可，本模块零改动

### 3.6 report_writer.py — 输出

- 每日 sheet（sheet 名 = 日）：`姓名 | 队列 | 阶段 | 金额催回率 | 系数 | 绩效`，按催回率降序（修复旧手工表 13 号缺列问题，格式统一）
- `绩效汇总` sheet：`姓名 | 每日绩效… | 累计`，缺勤日按 `ABSENT_OUTPUT` 统一
- 文件名：`{起}-{止}号WW项目绩效汇总.xlsx`，输出到 `OUTPUT_DIR`，不覆盖手工历史文件

### 3.7 main.py — 入口

```
python main.py                     # 处理 DATA_DIR 下全部日报
python main.py --days 10-14       # 指定日期范围
python main.py --month 2026-07    # 手动指定年月（覆盖自动推断）
python main.py --check            # 只读取+校验，不出报表（日常自检）
```
流程：读目标 → 读日报 → 计算 → 输出 → 打印警告汇总。编排逻辑固定，后续规则变化不动 main。

## 4. 待确认业务问题 → 配置映射

| 问题 | 配置项 | 当前默认 |
|---|---|---|
| Q1 目标版本按日期还是永远最新 | `TARGET_VERSION_POLICY` | `effective_date`（按需求 3） |
| Q2 13号 S1 按 D1 档发放 | 程序按规则重算，差异会在核对时显现 | — |
| Q3 D1 公式 | `RATE_RULES` | 默认走 `cross_hybrid` |
| Q4 档位边界归属 | `BAND_BOUNDARY` | `lower_inclusive` \[min,max) |

## 5. 扩展场景验证（长期维护清单）

| 场景 | 需要做什么 |
|---|---|
| 目标出新版本（如 2026-08-01） | 目标 Excel 里加一个带日期的块，程序自动识别 |
| 新增阶段（如 S3） | 目标表加 S3 块即可；若催回率公式特殊，config 的 RATE_RULES 加一行 |
| 姓名前缀又变（如 MX-） | config 的 NAME_PREFIXES 加一项 |
| 队列改名 | config 的 QUEUE_STAGE_MAP 加一项 |
| 人员增减 | 不需要任何操作（以日报为准，工号为主键） |
| 日报加列/调整列顺序 | 表头文字不变则零改动；新列名在 config 映射里补一行 |
