# 🌟 最佳实践

## 知识管理原则

### 1. 原子化

每条笔记只记录一个知识点，便于索引和复用。

- ✅ **好例子**: `Python 列表推导式.md`
- ❌ **坏例子**: `Python 各种高级特性.md`

### 2. 链接思维

利用 Markdown 链接将相关知识串联起来：

```markdown
了解更多关于 [Python 装饰器](../dev/python-decorators.md) 的内容
```

### 3. 定期整理

- **每日**: 随手记录新知识
- **每周**: 分类整理、添加标签
- **每月**: 复习回顾、更新过时内容

## 笔记模板

```markdown
---
tags:
  - tag1
  - tag2
---

# 标题

## 概述

简要描述这个知识点

## 核心内容

- 要点 1
- 要点 2

## 代码示例

```python
print("Hello")
```

## 参考资料

- [链接标题](https://example.com)
```

## 目录结构建议

```
docs/
├── index.md              # 首页
├── guide/                # 使用指南
│   ├── index.md
│   └── getting-started.md
├── dev/                  # 技术开发
│   ├── index.md
│   ├── python/
│   ├── javascript/
│   └── docker/
├── reference/            # 参考资料
│   └── index.md
└── tags.md               # 标签页面
```
