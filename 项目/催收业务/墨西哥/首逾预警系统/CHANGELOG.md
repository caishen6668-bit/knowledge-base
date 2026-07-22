# Changelog

## v1.0.2 (2026-07-07) — Stable Release

### 🔧 关键修正

- **修正 business_date 过滤口径**：`fetch_overdue_data()` 增加 `due_day` 过滤，从整周聚合改为单日口径，与 BI 页面（借款结束日期(day)）完全一致
- **修正首逾计算公式**：统一采用 BI 口径
  - 到期本金 = `due_amt`（`D_2_due_amt + D_3_pay_amt`）
  - 累计回款 = `D_3_pay + D_2_pay + D_1_pay + ... + {stage}_pay`（按阶段递增累计）
  - 金额首逾率 = `1 - 累计回款 / 到期本金`
- **新增统一计算函数** `calculate_first_overdue_rate(row, stage)` — 所有模块调用同一函数，消除手写公式
- 公式已于 2026-07-06 与 Quick BI 验证一致（MX D0: 873,068.33 / 834笔 / 31.85%，偏差 0.00pp）

### 📝 新增

- `--check` 命令：人工对数工具，按维度输出原始计算结果
- `--verify-formula` 命令：公式验证工具，枚举候选公式对比 BI 基准
- `check.py`：对数计算模块
- `verify_formula.py`：公式验证模块
- `docs/业务口径.md`：首逾率公式文档

---

## v1.0.1 (2026-07-07)

### ⚡ 性能优化

- **API 内存缓存** `_query_cache`：相同 `(api_id, conditions)` 只请求一次
- **Cache 预热**：`warm_cache_recovery()` / `warm_cache_cases()` 在 country loop 前统一拉取
- **消除重复请求**：多国共享同一批原始数据，请求数从 6 次 → 3 次（↓50%）
- **Profile 日志**：每次运行输出 API 调用次数、行数、耗时、缓存命中率、各阶段耗时

---

## v1.0.0 (2026-07-07) — Initial Release

### 🎯 功能

- 多层级下钻分析：整体 → 产品类型（单期/分期）→ 包体（非分期/借款分期/展期分期/展期N期）→ 订单风控等级（A~F）
- 多国支持：墨西哥（MX）、阿根廷（AR），可独立或合并运行
- 多阶段监控：D-2、D-1、D0、D1、S1、S2
- 飞书 Interactive Card 告警 + 纯文本降级
- 贡献度分析 + 影响金额计算
- 连续异常检测（可选）

### 🛡️ 生产就绪

- 版本号显示 + 飞书卡片底部
- 日志系统：`logs/daily_alert_YYYYMMDD.log`
- 异常处理 + 飞书失败通知
- 退出码：成功 0 / 失败 1
- 配置校验：启动时检查所有必需配置项
- `requirements.txt`：完整依赖声明

### 🔬 工具

- `--dump-fields`：API 字段结构探查（23 个字段）
- `--verify-fields`：6 个未使用字段的深度交叉验证
- `--uat`：UAT 自动验收 + Excel 报告生成

### 📋 字段验证

- `due_case` ✅ 到期笔数（100% 非空）
- `mult_no` ✅ 分期期数
- `order_grade` ✅ 订单风控等级 A~F（6 级）
- `cust_type` ⚠️ 保留兼容

---

## 路线图

| 版本 | 计划内容 |
|------|----------|
| **v2.0** | 趋势分析、AI 总结、会员等级、更多国家、更多指标 |
