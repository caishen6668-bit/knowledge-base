"""
基础设施/配置/paths.py — 公共路径管理

用法:
    from 基础设施.配置.paths import REPO_ROOT, DESKTOP, DATA_DIR
"""

import os
from pathlib import Path

# 仓库根目录
REPO_ROOT = Path(__file__).resolve().parents[2]

# 用户桌面
DESKTOP = Path.home() / "Desktop"

# 项目根目录
项目 = REPO_ROOT / "项目"
催收业务 = 项目 / "催收业务"
墨西哥业务 = 催收业务 / "墨西哥"
阿根廷业务 = 催收业务 / "阿根廷"

# 文档目录
文档 = REPO_ROOT / "文档"

# 数据目录（后续可扩展）
DATA_DIR = REPO_ROOT / "示例数据"
