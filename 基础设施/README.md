# 基础设施

仓库级共享模块，所有项目统一引用。

## 目录

```
基础设施/
├── README.md
├── 配置/
│   ├── env.py          ← 环境变量加载（.env → os.environ）
│   ├── paths.py        ← 公共路径（仓库根、桌面、项目目录等）
│   └── constants.py    ← 公共常量（API 端点、数据集 ID、国家/App 名称）
├── 日志/               ← 统一日志配置（后续迭代）
└── 工具/               ← 公共工具函数（后续迭代：Excel、日期、文本处理）
```

## 使用方式

在每个项目文件的顶部添加引导代码：

```python
import sys
from pathlib import Path

# 定位仓库根目录，加载基础设施
_repo = Path(__file__).resolve()
while not (_repo / ".git").exists() and _repo != _repo.parent:
    _repo = _repo.parent
sys.path.insert(0, str(_repo))

from 基础设施.配置.env import require, get
```

然后使用：

```python
# 必需凭据（缺失时报错）
qbi_ak = require("QBI_ACCESS_KEY")

# 可选配置（有默认值）
chat_id = get("FEISHU_CHAT_MX", "oc_xxx")
```

## 环境变量

所有认证凭据统一在仓库根目录 `.env` 文件中配置。模板文件 `.env.example` 列出了所有可配置项。

`.env` 已在 `.gitignore` 中排除，不会被提交到 GitHub。

## 设计原则

- **零外部依赖** — `env.py` 不依赖 `python-dotenv`，直接解析 `.env` 文件
- **自动向上查找** — 引导代码通过 `.git` 目录定位仓库根，适配任意深度的项目
- **先加载后引用** — `env.py` 在 import 时自动将 `.env` 注入 `os.environ`，下游通过 `os.environ.get` 或 `require()` 读取
- **不修改已归档文件** — `已归档项目/` 中的代码保持原样

## 后续规划

| 模块 | 计划 |
|------|------|
| 日志 | 统一 `logging` 配置（格式、级别、文件轮转） |
| 工具/Excel | `美化格式.py` `Excel审核.py` 等公共函数提取 |
| 工具/日期 | 考核周期计算、日期范围推断等 |
| 配置/飞书 | 飞书卡片模板、消息构建器等 |
