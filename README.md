# 📚 我的知识库

基于 **MkDocs + Material for MkDocs** 的个人知识管理系统。

## ✨ 特性

- 📝 **纯 Markdown** — 所有内容都是 Markdown 文件，便携、永不过期
- 🔍 **全文搜索** — 内置离线全文搜索，秒级定位
- 🌓 **明暗主题** — 支持亮色/暗色模式切换
- 📱 **响应式设计** — 电脑、平板、手机都能完美显示
- 🏷️ **标签系统** — 按标签分类和浏览内容
- 🚀 **零成本部署** — 可部署到 GitHub Pages 等静态托管

## 🚀 快速开始

### 首次使用（新电脑上）

```bash
# 1. 克隆仓库
git clone <你的仓库地址> knowledge-base
cd knowledge-base

# 2. 创建虚拟环境（在项目目录内，不占 C 盘）
python -m venv .venv

# 3. 安装依赖（在虚拟环境中）
.venv/Scripts/pip install -r requirements.txt

# 4. 启动本地服务
.venv/Scripts/mkdocs serve

# 5. 在浏览器中打开 http://127.0.0.1:8000
```

### 日常使用

```bash
# 进入项目目录
cd D:/knowledge-base

# 启动本地预览
.venv/Scripts/mkdocs serve

# 或者使用快捷脚本（Windows）
start.bat
```

## 📁 目录结构

```
knowledge-base/
├── docs/                 # 📄 文档目录（Markdown 文件）
│   ├── index.md          # 首页
│   ├── guide/            # 📚 学习笔记
│   ├── dev/              # 💻 技术文档
│   ├── reference/        # 📖 参考资料
│   └── tags.md           # 🏷️ 标签页面
├── .venv/                # 🐍 Python 虚拟环境（不提交到 Git）
├── mkdocs.yml            # ⚙️ MkDocs 配置文件
├── requirements.txt      # 📦 Python 依赖
├── start.bat             # 🚀 Windows 快捷启动
└── README.md             # 📖 本文件
```

## 🔄 跨电脑同步

### 提交更新

```bash
git add .
git commit -m "添加了 xxx 笔记"
git push
```

### 换电脑后恢复

```bash
# 只需要克隆 + 装环境两步
git clone <你的仓库地址> knowledge-base
cd knowledge-base
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

> 💡 **在 Claude Code 中也能直接读写这些 Markdown 文件，与知识库无缝配合。**

## 🛠️ 常用命令

| 命令 | 说明 |
|:---|:---|
| `mkdocs serve` | 启动本地开发服务器（热重载） |
| `mkdocs build` | 构建静态网站到 `site/` 目录 |
| `mkdocs gh-deploy` | 部署到 GitHub Pages |

## 📝 如何添加内容

1. 在 `docs/` 下创建 `.md` 文件
2. 编辑 `mkdocs.yml` 的 `nav` 部分添加导航
3. 保存后浏览器自动刷新

详细用法见 [快速开始](guide/getting-started.md)。
