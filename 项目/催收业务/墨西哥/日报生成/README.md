# 催收日报自动填充

## 项目简介

每天自动从 Redash 获取催收员成绩统计数据，写入 Excel 日报模板，生成简报后同步到 Google Sheets。

## 项目结构

```
日报生成/
├── README.md          ← 本文件
├── xby日报.py          ← V1 版本（Redash → Excel）
├── xby日报V2.py        ← V2 版本（Redash → Excel → 简报 → Google）
└── compare_v1_v2.py   ← V1 vs V2 对比工具
```

## 运行方式

```bash
# V2（推荐）
python xby日报V2.py                       # 当天运行
python xby日报V2.py 2026-07-09            # 指定日期
python xby日报V2.py --sync-only           # 仅同步 Google（读 Excel）
python xby日报V2.py --dry-run             # 仅查询+简报，不写 Excel/Google
python xby日报V2.py --query-only          # 仅查询+保存原始 JSON

# V1（旧版）
python xby日报.py [YYYY-MM-DD]
python xby日报.py --sync-google [YYYY-MM-DD]
```

## 输入

| 输入 | 来源 | 说明 |
|------|------|------|
| 催收员成绩统计 | Redash Query #79 | 回收业绩数据 |
| Excel 日报模板 | 本地路径 | 模板文件 |

## 输出

- 填充后的 Excel 日报文件
- Google Sheets 同步（V2）
- 控制台简报输出

## 依赖

```bash
pip install requests openpyxl
```

Google Sheets 同步额外需要：
- Google API 凭证 JSON 文件
- Google Sheet 已分享给服务账号

## 修改记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v2.2.0 | 2026-07-07 | 当前 V2 最新版 |
| v2.0 | 2026-06 | 新增简报生成 + Google 同步 |
| v1.0 | 2026-05 | 初版：Redash → Excel |

## 注意事项

- V2 设计原则：Redash 查询结束后所有模块禁止再次访问 Redash
- Excel 只写入数据值，不触碰公式/汇总/目标/达成率
- Google 同步从 Excel 读取（用户可能手动修改过数据）
- 每次启动全新 Session，查询结束立即释放

## 负责人

- 开发者：xiongwei
