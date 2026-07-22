"""
基础设施/配置/env.py — 统一环境变量加载

用法:
    import sys
    from pathlib import Path
    _repo = Path(__file__).resolve()
    while not (_repo / ".git").exists() and _repo != _repo.parent:
        _repo = _repo.parent
    sys.path.insert(0, str(_repo))

    from 基础设施.配置.env import require, get, REPO_ROOT

设计原则:
    - 所有认证凭据从 .env 读取，不设默认值
    - require() 用于必需凭据（缺失时明确报错）
    - get() 用于有合理默认值的配置项
"""

import os
import sys
from pathlib import Path

# 仓库根目录（本文件向上 3 级: env.py → 配置/ → 基础设施/ → knowledge-base/）
_REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------- 加载 .env（模块级别，仅执行一次） ----------
_env_file = _REPO_ROOT / ".env"
if _env_file.exists():
    # 用 exec 读取而非 load_dotenv，避免引入额外依赖
    _env_text = _env_file.read_text(encoding="utf-8")
    for _line in _env_text.splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _val = _line.partition("=")
        _key = _key.strip()
        _val = _val.strip().strip('"').strip("'")
        if _key and _val and _key not in os.environ:
            os.environ[_key] = _val

# 确保仓库根在 sys.path 中（下游 import 依赖此路径）
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ---------- 导出 ----------
REPO_ROOT = _REPO_ROOT

def require(key: str) -> str:
    """读取必需的认证凭据，缺失时明确报错"""
    val = os.environ.get(key, "")
    if not val:
        raise EnvironmentError(
            f"缺少必需的环境变量: {key}\n"
            f"请在仓库根目录创建 .env 文件（参考 .env.example）并填入真实值\n"
            f"文件位置: {_REPO_ROOT / '.env'}"
        )
    return val

def get(key: str, default: str = "") -> str:
    """读取可选的环境变量，不存在或为空返回默认值

    注意：与 os.environ.get() 不同，本函数将空字符串视为"未配置"，
    此时返回 default 而非空字符串。这避免了 .env 中 KEY= 静默覆盖默认值。
    """
    val = os.environ.get(key, "")
    return val if val else default
