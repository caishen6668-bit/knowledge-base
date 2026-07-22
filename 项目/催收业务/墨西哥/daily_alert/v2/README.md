# V2 — 首逾智能预警系统

> ⚠️ **V1 (v1.0.2) 已冻结为 Stable Release。**
>
> V2 基于 V1 数据层构建，所有新增功能在此目录下开发，
> 不修改任何 V1 代码。

---

## V2 开发计划

| 功能 | 阶段 | 状态 | 说明 |
|------|------|------|------|
| 趋势预警引擎 | Phase 1 | ✅ 已完成 | DoD/3d/7d/目标值对比 + 自动判断 🟢🟡🟠🔴 |
| 飞书趋势告警 | Phase 2 | 🔲 待开发 | 基于 TrendResult 发送飞书趋势卡片 |
| AI 分析总结 | Phase 3 | 🔲 待开发 | LLM 读取 TrendResult 生成预警摘要 |
| 异常定位引擎 | Phase 4 | 🔲 待开发 | 自动下钻定位异常根因 |
| 会员等级 | Phase 5 | 🔲 待开发 | 新增 member_level 分析维度 |
| 更多国家 | Phase 6 | 🔲 待开发 | 扩展至更多国家 |
| 更多指标 | Phase 7 | 🔲 待开发 | 回收率、迁徙率等新指标 |

---

## Phase 1: 趋势预警引擎 (当前)

### 功能

1. **昨天对比 (DoD)** — 当前首逾率 vs 昨天
2. **近3日均值对比** — 当前 vs 近3天均值
3. **近7日均值对比** — 当前 vs 近7天均值
4. **目标值对比** — 当前 vs 预设目标
5. **自动判断** — 根据变化幅度自动分级：

| 等级 | 图标 | 含义 |
|------|------|------|
| GREEN | 🟢 | 正常 |
| YELLOW | 🟡 | 关注 |
| ORANGE | 🟠 | 警告 |
| RED | 🔴 | 严重 |

### 输出

统一的 **TrendResult** — 所有后续 Phase 的 AI 分析、异常定位、飞书预警全部基于此结构。

### 运行

```bash
# 从 daily_alert 父目录运行
cd D:\knowledge-base\scripts

# 所有国家 D0
python -m daily_alert.v2.main

# 墨西哥 D0
python -m daily_alert.v2.main --country MX --stage D0

# 阿根廷 D1
python -m daily_alert.v2.main --country AR --stage D1

# 指定日期
python -m daily_alert.v2.main --date 2026-07-07
```

### 架构

```
v2/
├── __init__.py          # 包声明
├── config.py            # V2 配置（趋势阈值、目标值）
├── models.py            # TrendResult / TrendComparison 数据结构
├── trend_engine.py      # 核心趋势计算引擎
├── main.py              # CLI 入口
└── README.md            # 本文档
```

### 依赖

- 复用 V1 `quickbi.py` 的数据获取层（`fetch_overdue_data`、`calculate_first_overdue_rate`、API 缓存）
- 复用 V1 `config.py` 的国家配置、ALERT_DISPLAY 图标
- 不修改任何 V1 代码

---

## 技术规范

- 基于 V1 (v1.0.2) Stable Release
- 复用 V1 的数据获取层（`quickbi.py`）和统一首逾计算函数
- 所有新功能以独立模块方式添加，不影响 V1 运行
- 入口脚本独立：`v2/main.py`（通过 `python -m daily_alert.v2.main` 运行）

---

## V1 参考

| 文件 | 说明 |
|------|------|
| `../config.py` | 集中配置 |
| `../quickbi.py` | Quick BI 数据获取 + `calculate_first_overdue_rate()` 统一函数 |
| `../analyzer.py` | 分析引擎核心 |
| `../docs/业务口径.md` | 首逾率公式定义 |
| `../CHANGELOG.md` | 版本变更记录 |
