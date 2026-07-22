# 墨西哥每日案件预估

## 项目简介

每天自动从 Quick BI 获取墨西哥催收业务的到期案件数据和实际上班人力，完成业务计算后生成图片，通过飞书机器人推送到群聊。

支持预览审核流程：先发私聊 → 确认后再转发群聊。

## 项目结构

```
每日案件预估/
├── README.md                           ← 本文件
├── 墨西哥每日案件及人力预估_v2.py       ← 主脚本
├── manual_staff_template.json          ← 手动排班模板（--manual-staff 参数时使用）
├── weekend_attendance.csv              ← 周末出勤配置
└── deploy_forecast.sh                  ← Linux 服务器部署脚本
```

> 启动器位于上级目录：`../每日案件预估启动器.py`（可同时启动 MX + AR）

## 运行方式

```bash
python 墨西哥每日案件及人力预估_v2.py                    # 发送飞书卡片
python 墨西哥每日案件及人力预估_v2.py --dry-run           # 仅计算，不发送
python 墨西哥每日案件及人力预估_v2.py --text-only         # 纯文本降级
python 墨西哥每日案件及人力预估_v2.py --list-chats        # 列出可用群聊
```

## 输入

| 输入 | 来源 | 说明 |
|------|------|------|
| 到期案件数据 | Quick BI API | 每日到期笔数、金额 |
| 出勤人力数据 | Quick BI API | 实际上班人数 |
| 手动排班 | manual_staff_template.json | `--manual-staff` 时替代 API |

## 输出

- 飞书卡片消息（含预估图表图片）
- 支持私聊预览 → 群聊转发的审核流程

## 依赖

```bash
pip install requests openpyxl
```

Quick BI API 凭证与飞书机器人配置内嵌在脚本中（`config.py` 同级维护）。

## 修改记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v2 | 2026-07 | 全新架构：不再依赖 Excel，所有数据从 API 获取 |
| v1 | 2026-06 | 初版，基于 Excel 输入 |

## 注意事项

- 网络需可访问 Quick BI API（`quickbi-public.cn-hangzhou.aliyuncs.com`）和飞书开放平台
- `--dry-run` 模式不会发送任何消息，适合调试
- 私聊审核通过后，通过启动器 `每日案件预估启动器.py` 转发到群聊

## 负责人

- 开发者：xiongwei
