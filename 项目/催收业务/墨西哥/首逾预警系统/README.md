# 首逾智能预警系统 v1.0.2

> **Production Version：v1.0.2** &nbsp;|&nbsp; **Status：Stable** &nbsp;|&nbsp; **Build：2026-07-07**
>
> 首逾计算公式已于 2026-07-06 与 Quick BI 验证一致。详见 [docs/业务口径.md](docs/业务口径.md)。

每天自动计算首逾率（First Overdue Rate），按国家、阶段、产品类型、包体、风控等级逐层下钻，发现异常后通过飞书机器人推送告警。

---

## 目录

- [功能概述](#功能概述)
- [环境要求](#环境要求)
- [安装](#安装)
- [配置](#配置)
  - [Quick BI](#quick-bi)
  - [飞书机器人](#飞书机器人)
  - [国家配置](#国家配置)
  - [预警阈值](#预警阈值)
- [运行](#运行)
  - [每日运行](#每日运行)
  - [命令行参数](#命令行参数)
  - [退出码](#退出码)
- [定时任务](#定时任务)
- [UAT 验收](#uat-验收)
- [字段说明](#字段说明)
  - [回收率 API（524c3ccd429c）](#回收率-api524c3ccd429c)
  - [案件量 API（c2f93e0fa45b）](#案件量-apic2f93e0fa45b)
  - [字段验证结论](#字段验证结论)
- [日志](#日志)
- [项目结构](#项目结构)
- [版本历史](#版本历史)

---

## 功能概述

- **多层级下钻分析**：整体 → 产品类型（单期/分期） → 包体（非分期/借款分期/展期分期/展期N期） → 订单风控等级（A~F）
- **多国支持**：墨西哥（MX）、阿根廷（AR），可独立或合并运行
- **多阶段监控**：D-2、D-1、D0、D1、S1、S2
- **飞书告警**：Interactive 卡片 + 纯文本降级，多国合并发送
- **UAT 自动验收**：程序自动拉取 Quick BI 数据并交叉验证，生成 Excel 报告
- **字段检查工具**：API 字段结构探查 + 深度验证
- **生产就绪**：日志、配置校验、异常退出、失败通知

---

## 环境要求

- Python 3.8+
- 网络可访问 Quick BI API（`quickbi-public.cn-hangzhou.aliyuncs.com`）
- 网络可访问飞书开放平台 API（`open.feishu.cn`）

---

## 安装

```bash
cd D:\knowledge-base\scripts\daily_alert
pip install -r requirements.txt
```

依赖：
| 包 | 用途 |
|---|------|
| `requests` | HTTP 调用 Quick BI / 飞书 API |
| `openpyxl` | 生成 UAT 验收 Excel 报告 |

---

## 配置

所有配置集中在 [config.py](config.py)，按需修改。

### Quick BI

```python
QBI_AK = os.environ.get("QBI_ACCESS_KEY")  # Access Key（从 .env 读取）
QBI_SK = "..."                               # Secret Key
QBI_ENDPOINT = "quickbi-public.cn-hangzhou.aliyuncs.com"
QBI_API_RECOVERY = "524c3ccd429c"            # 回收率数据集
QBI_API_CASES = "c2f93e0fa45b"               # 案件量数据集
```

### 飞书机器人

```python
FEISHU_APP_ID = "cli_aaab85f1c1791bd5"
FEISHU_APP_SECRET = "..."

# 群聊 ID（可通过环境变量覆盖）
FEISHU_CHAT_MX = "oc_8b5ef4aee4e93b29326cd8c0f3c24d90"   # 墨西哥群
FEISHU_CHAT_AR = "oc_..."                                   # 阿根廷群
```

环境变量覆盖：
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_CHAT_MX`
- `FEISHU_CHAT_AR`

### 国家配置

```python
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
```

### 预警阈值

```python
ALERT_RULES = {
    "D-2": {"red": 2,   "orange": 1,   "yellow": 0.5},
    "D-1": {"red": 2,   "orange": 1,   "yellow": 0.5},
    "D0":  {"red": 5,   "orange": 3,   "yellow": 1},
    "D1":  {"red": 2,   "orange": 1,   "yellow": 0.5},
    "S1":  {"red": 1,   "orange": 0.5, "yellow": 0.2},
    "S2":  {"red": 0.5, "orange": 0.3, "yellow": 0.1},
}
```

---

## 运行

### 每日运行

```bash
# 方式一：wrapper 脚本
cd D:\knowledge-base\scripts\daily_alert
python 每日数据预警.py

# 方式二：模块运行
cd D:\knowledge-base\scripts
python -m daily_alert.main

# 方式三：仅计算不发飞书（调试用）
python -m daily_alert.main --dry-run
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--date` | 运行日期（业务日期 = 运行日期 - 1） | 今天 |
| `--country` | 国家: `MX` / `AR` / `ALL` | `ALL` |
| `--stage` | 分析阶段: `D-2` / `D-1` / `D0` / `D1` / `S1` / `S2` | `D0` |
| `--dry-run` | 仅计算，不发送飞书 | — |
| `--text-only` | 纯文本降级发送 | — |
| `--list-chats` | 列出可用飞书群聊 | — |
| `--dump-fields` | 字段检查：打印所有 API 字段 + 类型 + Unique 值 | — |
| `--verify-fields` | 字段验证：深度分析 6 个关键字段 | — |
| `--uat` | UAT 自动验收：拉取数据 → 计算 → 生成 Excel 报告 | — |
| `--check` | 人工对数：按维度输出原始计算结果（不发飞书） | — |
| `--verify-formula` | 公式验证：枚举候选公式，对比 BI 基准值 | — |

### 退出码

| 退出码 | 含义 |
|--------|------|
| 0 | ✅ 运行成功 |
| 1 | ❌ 运行失败（配置错误 / Quick BI 失败 / 数据为空 / 程序异常） |

---

## 定时任务

### Windows 任务计划程序

```powershell
# 每天 10:00 运行（分析昨天的数据）
schtasks /create /tn "首逾每日预警" `
  /tr "python D:\knowledge-base\scripts\daily_alert\每日数据预警.py" `
  /sc daily /st 10:00
```

### Linux cron

```bash
# 每天 10:00 运行
0 10 * * * cd /path/to/daily_alert && python 每日数据预警.py >> logs/cron.log 2>&1
```

### 运行失败时的行为

如果 Quick BI 数据获取失败或返回空数据：
1. 程序不会发送错误的预警数据
2. 向所有配置的飞书群发送失败通知卡片
3. 退出码 = 1（便于计划任务/监控系统感知）
4. 日志记录详细错误信息

---

## UAT 验收

```bash
# 自动验收（无需人工填写 BI 值）
python -m daily_alert.main --uat

# 指定日期
python -m daily_alert.main --uat --date 2026-07-07

# 仅墨西哥
python -m daily_alert.main --uat --country MX
```

生成 [docs/UAT验收报告.xlsx](docs/UAT验收报告.xlsx)，包含 7 个工作表：

| 工作表 | 内容 |
|--------|------|
| 整体 | MX/AR × 6 阶段 → 首逾率 |
| 单期分期 | mult_no=1 vs ≥2 → 首逾率 |
| 包体 | 4 种包体 → 首逾率 + 到期本金 + 到期笔数 |
| 订单风控等级 | A~F × 6 阶段 → 首逾率 |
| 案件量交叉验证 | 回收率 API due_case vs 案件量 API case |
| 差异明细 | 仅展示差异项 |
| Summary | 通过率汇总 |

验证方法：
- **首逾率**：程序聚合计算 vs 逐行加权平均（备用路径），误差 < 0.01% = ✅
- **案件量**：两个独立 Quick BI 数据源互相校验

---

## 字段说明

### 回收率 API（524c3ccd429c）

| 字段 | 类型 | 说明 |
|------|------|------|
| `app_name` | 字符串 | APP 名称 |
| `order_type` | 字符串 | 包体类型（非分期/借款分期/展期分期/展期N期） |
| `cust_type` | 字符串 | 客户类型（新客/老客） |
| `order_grade` | 字符串 | 订单风控等级（A~F，6级） |
| `mult_no` | 数值 | 分期期数（1=单期, 2=2期, 3=3期, 4=4期） |
| `due_case` | 数值 | 到期笔数 |
| `{stage}_due_amt` | 数值 | 各阶段到期本金（stage: D_2/D_1/D0/D1/S1/S2） |
| `{stage}_pay_amt` | 数值 | 各阶段回款金额 |
| `D_3_pay_amt` | 数值 | D-3 回款金额 |
| `due_amt` | 数值 | ❌ Deprecated — 不是到期本金 |
| `due_day` | 数值 | 逾期天数 |
| `is_dl_order` | 数值 | 是否 DL 订单（0/1） |

### 案件量 API（c2f93e0fa45b）

| 字段 | 类型 | 说明 |
|------|------|------|
| `app` | 字符串 | APP 名称 |
| `due_date` | 日期 | 到期日期 |
| `order` | 字符串 | 包体类型 |
| `cust_type` | 字符串 | 客户类型 |
| `case` | 数值 | 案件数 |

### 字段验证结论

| 字段 | 状态 | 说明 |
|------|------|------|
| `due_amt` | ✅ 到期本金 | = D_2_due_amt + D_3_pay_amt，所有阶段统一使用（v1.0.2 与 BI 验证一致） |
| `due_case` | ✅ 正式启用 | 到期笔数，100%非空 |
| `mult_no` | ✅ 正式启用 | 分期期数 |
| `order_grade` | ✅ 正式启用 | 订单风控等级 A~F（6级），比 cust_type 更细粒度 |
| `cust_type` | ⚠️ 保留兼容 | 不再作为主要分析维度 |
| `{stage}_due_amt` | ❌ Deprecated | 单阶段到期金额，v1.0.2 起不再使用 |

---

## 日志

每次运行自动生成日志文件：

```
logs/
└── daily_alert_20260707.log
```

日志内容：
- 开始时间 / 结束时间 / 运行耗时
- 运行日期 / 业务日期 / 阶段 / 国家
- Quick BI 数据拉取统计
- 异常节点数量
- 错误详情（如有）
- 飞书发送状态

工具模式（`--dump-fields`、`--verify-fields`、`--uat`）也会记录日志。

---

## 项目结构

```
daily_alert/
├── 每日数据预警.py          # Wrapper 入口脚本
├── main.py                  # CLI 入口 + 主编排逻辑
├── config.py                # 集中配置 + 启动校验
├── quickbi.py               # Quick BI API 数据获取 + 统一首逾计算函数
├── alert_engine.py          # 分析引擎（层级下钻）
├── analyzer.py              # 维度分析器 + 比较函数
├── report.py                # 报告构建
├── send_feishu.py           # 飞书卡片/文本发送 + 失败通知
├── check.py                 # 人工对数工具（--check）
├── verify_formula.py        # 公式验证工具（--verify-formula）
├── field_inspector.py       # API 字段检查工具
├── logger.py                # 生产日志模块
├── uat.py                   # UAT 自动验收模块
├── requirements.txt         # Python 依赖
├── README.md                # 本文档
├── CHANGELOG.md             # 版本变更记录
├── docs/
│   ├── 业务口径.md           # 首逾率公式定义（与 BI 验证一致）
│   ├── API字段说明.md        # 字段参考（--dump-fields 生成）
│   ├── 字段验证报告.md        # 字段验证（--verify-fields 生成）
│   └── UAT验收报告.xlsx      # UAT 验收报告（--uat 生成）
└── logs/
    └── daily_alert_*.log     # 每日运行日志
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| **v1.0.2** | 2026-07-07 | **Stable Release。** 修正 business_date 过滤口径（整周→单日）、修正首逾计算公式与 BI 完全一致、新增统一计算函数、新增 `--check` 和 `--verify-formula` 命令、完成字段验证、通过 UAT |
| v1.0.1 | 2026-07-07 | 性能优化：API 内存缓存 + 预热 + Profile 日志，请求数 ↓50% |
| v1.0.0 | 2026-07-07 | 初始发布。多层级下钻、飞书告警、字段验证、UAT 自动验收、生产就绪 |

---

## V2 路线图

后续所有新功能进入 V2，V1 保持稳定运行。

| 功能 | 说明 |
|------|------|
| 趋势分析 | 历史首逾率趋势图 + 同比/环比 |
| AI 总结 | LLM 自动生成每日预警摘要 |
| 会员等级 | 新增 member_level 分析维度 |
| 更多国家 | 扩展至更多国家 |
| 更多指标 | 新增回收率、迁徙率等指标 |

> ⚠️ **V1 已冻结。** 所有新增需求请基于 `v2/` 目录开发，不允许直接修改 v1.0.2 代码。


---

## 负责人

- 开发者：xiongwei
- 维护状态：V1 (v1.0.2) Stable 冻结，V2 开发中
