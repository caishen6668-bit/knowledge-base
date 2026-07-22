# 员工评级系统 V2.1

**Version：V2.1.1　Release Date：2026-07-14**

给催收员工做半月一次评级：从 Quick BI 直接取业绩与出勤数据，自动计算三个考核周期，
按固定规则算分、评等级、排名，导出中文版 + 墨西哥西语版 Excel（含 Summary 汇总页）。

相对 V1 的变化：**取消所有 Excel 输入**，改为直接调用 Quick BI OpenAPI；
**评分规则与 V1 完全一致，未做任何改动**。

---

## 版本说明

- **V2.1.1 起，评分计算统一采用 Quick BI API 原始精度，不再模拟 Excel 导出时的两位小数显示精度。**
  四舍五入属于 Excel 展示格式、非评分规则；V2 直接使用 API 原始数据，计算精度高于 V1。
  与 V1 同周期比对时，个别员工可能在等级边界出现极小差异（约 0.5~1 分），
  属历史 Excel 快照与当前 API 数据口径差异，不再为对齐旧 Excel 而降低精度。
- 后续若发现结果差异，**优先核查数据是否发生变化（如晚到还款、数据订正），而非怀疑算法**。

---

## 数据口径说明

- **V1 使用历史 Excel 快照**作为输入数据（导出当时的定格数据）。
- **V2 使用 Quick BI API 实时数据**作为输入数据（每次运行取当前最新值）。
- 若 BI 存在**历史修数、晚到还款、数据订正**等情况，V2 结果与历史 Excel 可能存在**少量差异**。
- **该差异属于数据源变化，不属于评分算法变化。** 评分规则自 V2 起从未改动。
- 后续若发现评分结果与历史文件不同，**请优先核查数据是否发生变化，而不是怀疑评分算法**。

> 为便于追溯，导出的 **Summary 首页** 记录两项时间元信息：
> **数据生成时间**（本次报表导出时刻）与 **Quick BI 取数时间**（数据实际取自 BI 的时刻；
> 命中缓存时为首次取数时间。API 不提供更新时间，故以程序落缓存的时间为准）。

---

## 一、项目结构

```
employee_rating_v2/
├── main.py            # 主入口：解析参数 → 取数 → 评分 → 导出，全程日志
├── config.py          # 所有配置：凭证 / API ID / 权重 / 等级 / 字段口径 / 国家 / 版本
├── period.py          # 自动计算三个半月考核周期
├── api.py             # Quick BI OpenAPI 调用（HMAC-SHA1 签名 + 重试/超时）
├── fetch.py           # 业绩 / 出勤数据获取 + 本地 pkl 缓存
├── rating_engine.py   # 评分逻辑（业绩分/三期加权/出勤分/入职分/等级/排名）—— 勿改
├── exporter.py        # 导出 Summary + 评级明细，中文版 + 附加语言版
├── utils.py           # 日志、安全数值转换、日期、姓名归一
├── compliance.py      # 合规模块（预留，当前版本不实现）
├── requirements.txt   # 依赖
├── README.md          # 本文件
├── cache/             # 运行时生成：当日取数缓存 (*.pkl)
└── logs/              # 运行时生成：运行日志 (employee_rating_YYYYMMDD.log)
```

---

## 二、运行方式

在 `D:/knowledge-base/scripts` 目录下（`employee_rating_v2` 的父目录）以模块方式运行：

```bash
# 1. 安装依赖（首次）
pip install -r employee_rating_v2/requirements.txt

# 2. 正常运行（用系统当天日期，优先读缓存）
python -m employee_rating_v2.main
```

一条命令即完成：自动算周期 → 取数（缓存优先）→ 评分 → 导出中文版 + Mexico 版 + Summary → 写日志。

---

## 三、参数说明

| 参数 | 作用 | 示例 |
|------|------|------|
| `--date YYYY-MM-DD` | 指定某天作为“今天”来计算三个周期，用于**补跑历史评级**。默认系统当天。 | `python -m employee_rating_v2.main --date 2026-07-15` |
| `--refresh` | **忽略缓存**，强制重新请求 Quick BI。默认优先读缓存。 | `python -m employee_rating_v2.main --refresh` |

> 注意：`--date` 请指向**已有数据的历史周期**。若指向尚未产生数据的未来半月，会得到 0 人（正常现象，不是故障）。

### 考核周期规则（半月制）

每月分两期：`[1, 15]` 和 `[16, 月末]`。根据评估日期自动定位：

| 评估日期 | 本期 | 上一期 | 上上期 |
|----------|------|--------|--------|
| 2026-07-14 | 07-01 ~ 07-15 | 06-16 ~ 06-30 | 06-01 ~ 06-15 |
| 2026-07-20 | 07-16 ~ 07-31 | 07-01 ~ 07-15 | 06-16 ~ 06-30 |

三期加权：本期 0.5 / 上一期 0.3 / 上上期 0.2（见 `config.WEIGHTS`）。

---

## 四、Quick BI API 来源

复用日报/周报项目同一套凭证与签名方式（`config.py` 中的 `QBI_AK/QBI_SK/QBI_ENDPOINT`），
HMAC-SHA1 签名逻辑在 `api.py`。

| 用途 | ApiId | 请求参数 | 关键返回字段 |
|------|-------|----------|--------------|
| 业绩 | `c4f429db60b3` | `{"statis_date": "YYYYMMDD"}` | staff_name, dept_name, coll_group_name, overdue_level, today_enter_collect_amount, today_repay_amount, target, statis_date |
| 出勤 | `7f9969dc9020` | `{"day": "YYYYMMDD"}` | name, employer_no, day, working_status, depart_name, group, is_resigned, join_date |

**重要 API 语义（决定取数方式，已用真实数据验证）：**

- 业绩 API：`statis_date=X` 返回 **从 X 到最新可用日** 的区间（每人每天一行），
  **不是单日**。因此每个周期只请求一次（用周期起始日），再按周期区间过滤。
  规格最初设想的“逐日循环请求”会造成重复计数，故未采用。
- 出勤 API：`day=X` 返回 **从 X 起向后约 50 天** 的排班窗口。因此一次请求（用本期起始日）
  即可覆盖本期，再按本期区间过滤。
- 两个 API 均只取 `DEPARTMENT`（默认 `PL`）部门的行。

**达成率口径**：员工评级考核的是**金额达成率**。
`达成率 = (today_repay_amount / today_enter_collect_amount) / target`。
（除以 target 才与 V1 的封顶 150%/200% 及评分区间一致；D0/D1 封顶 1.5，其余封顶 2.0。）

**异常数据过滤（非评分规则）**：
当某工作日的**订单催回率 ≥ 100%** 时，该工作日数据将被剔除，不参与绩效计算。
原因是员工缺勤时，其历史已分配案件仍可能自然还款，而订单无法重新分配，
会造成订单催回率 ≥ 100% 且回款金额仍归属该员工——这并非员工当天的真实催收表现。
订单催回率不是评分指标，此处仅作异常数据过滤。
日订单催回率由 API 的 `total_repay_num / today_enter_collect_num` 重建。

---

## 五、缓存机制

- 每次运行会把取到的数据按**评估日期**缓存到 `cache/`：
  - `performance_YYYYMMDD.pkl`（三个周期的业绩，dict[周期 → DataFrame]）
  - `attendance_YYYYMMDD.pkl`（本期出勤 DataFrame）
- 同一评估日期再次运行时，**直接读缓存、不再请求 Quick BI**，秒级完成。
- 用 `--refresh` 可强制忽略缓存、重新取数并覆盖缓存。
- 缓存按评估日期区分，补跑不同历史日期互不影响。

---

## 六、日志位置

- 目录：`logs/employee_rating_YYYYMMDD.log`（YYYYMMDD 为评估日期）。
- 同时输出到控制台。
- 记录：启动版本号、评估日期、三个周期、取数步骤、缓存是否命中、评分开始/完成、
  导出路径、总耗时。
- API 请求失败会记录：HTTP 状态、接口 ApiId、异常信息、第几次重试。

---

## 七、输出文件说明

导出到桌面（`~/Desktop`），文件名带评估日期：

- `最终评级结果_YYYY.MM.DD_中文版.xlsx`
- `最终评级结果_YYYY.MM.DD_Mexico.xlsx`（西班牙语字段）

每个文件两个 Sheet：

1. **Summary**（第一页）：本周期、**数据生成时间、Quick BI 取数时间**、总/在职/离职人数、
   各等级人数与占比、平均综合分/业绩分/出勤分/入职分、平均达成率、最高/最低综合分。
2. **评级明细**：每人一行，字段与 V1 一致，末尾追加 V2.1 评级组成列
   （业绩贡献 / 出勤贡献 / 工龄贡献 / 距上一等级）。

> 若有下游脚本读取评级数据，请按 **Sheet 名「评级明细」** 读取（Summary 是第一页）。

---

## 八、后续如何增加新国家（MX / AR）

评分引擎、取数逻辑**完全不用改**，只需修改 `config.py` 顶部四个开关：

```python
# 墨西哥（当前）
COUNTRY = "MX"
DEPARTMENT = "PL"
EXPORT_LANGUAGE = "mx"

# 阿根廷（将来）
COUNTRY = "AR"
DEPARTMENT = "AR_IN"
EXPORT_LANGUAGE = "es_ar"   # 已在 EXPORT_LANG_LABELS / EXPORT_MAPS 预留占位
```

做阿根廷版时，补一份 `es_ar` 的字段译名到 `config.ZH_TO_MX` 同类映射即可
（当前 `es_ar` 暂复用西语映射作占位）。

---

## 九、修改规则去哪里改

所有可调项都在 `config.py`，改规则**不用动算法**：

- `WEIGHTS`：三期权重
- `GRADE_TABLE`：等级阈值与奖金系数
- `STAGE_CAP_150 / CAP_150 / CAP_200`：达成率封顶
- `ATT_*`：出勤扣分规则、状态标签
- `LOW_ATTENDANCE_DAYS / LOW_ATTENDANCE_PERF_CAP`：低出勤封顶
- `API_TIMEOUT / API_RETRIES / API_RETRY_BACKOFF`：接口容错


---

## 负责人

- 开发者：xiongwei
- 维护状态：V2.1.1 生产运行中
