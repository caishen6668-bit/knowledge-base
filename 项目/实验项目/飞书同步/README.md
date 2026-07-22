# 飞书同步（实验项目）

## 项目简介

飞书云文档 → MkDocs 知识库同步脚本。

从飞书开放平台 API 读取云文档内容，转换为 Markdown 格式，写入 MkDocs 文档目录，实现飞书文档与本地知识库的双向同步。

## 项目结构

```
飞书同步/
├── README.md      ← 本文件
└── 飞书同步.py     ← 主脚本
```

## 运行方式

```bash
python 飞书同步.py
```

## 输入

| 输入 | 来源 | 说明 |
|------|------|------|
| 飞书云文档 | 飞书开放平台 API | 需要 App ID + App Secret |

## 输出

- Markdown 文件（写入 MkDocs 文档目录）

## 依赖

```bash
pip install requests python-docx markdown
```

## 修改记录

| 日期 | 变更 |
|------|------|
| 2026-06 | 初版 |

## 注意事项

- ⚠️ 实验项目，未完成完整的错误处理和增量同步
- 需要在飞书开发者后台创建应用，配置 `drive:drive:readonly` 权限
- App ID / App Secret 当前硬编码在脚本中，生产使用前建议改为环境变量

## 负责人

- 开发者：xiongwei
