# 🚀 快速开始

## 什么是 MkDocs？

MkDocs 是一个快速、简单的静态站点生成器，专为项目文档设计。它使用 Markdown 编写内容，生成漂亮的 HTML 页面。

## 如何添加内容

### 1. 创建新的 Markdown 文件

在 `docs/` 目录下创建 `.md` 文件，例如：

```markdown
# 我的新笔记

这是笔记内容，支持 **粗体**、*斜体*、~~删除线~~ 等格式。

## 二级标题

- 列表项 1
- 列表项 2
```

### 2. 注册导航

在 `mkdocs.yml` 的 `nav` 部分添加文件路径：

```yaml
nav:
  - 新笔记: path/to/your-file.md
```

### 3. 查看效果

启动本地服务后，浏览器会自动刷新显示新内容。

## 常用 Markdown 语法

### 提示块

```markdown
!!! tip "提示标题"
    这是提示内容，支持多段落。

!!! warning "警告"
    注意：这是一个警告信息。

!!! danger "危险"
    这是一个危险提示！
```

效果：

!!! tip "提示"
    这是提示块的效果

### 代码块

````markdown
```python
def hello():
    print("Hello, World!")
```
````

### 任务列表

```markdown
- [x] 已完成任务
- [ ] 未完成任务
```

### 表格

```markdown
| 名称 | 说明 | 版本 |
|:---|:---|:---:|
| Python | 编程语言 | 3.13 |
| MkDocs | 文档工具 | 1.6 |
```

### 标签页

```markdown
=== "Windows"
    Windows 系统的操作说明

=== "macOS"
    macOS 系统的操作说明

=== "Linux"
    Linux 系统的操作说明
```

## 本地预览

```bash
cd D:/knowledge-base
.venv/Scripts/mkdocs serve
```

然后在浏览器中打开 `http://127.0.0.1:8000` 即可预览。
