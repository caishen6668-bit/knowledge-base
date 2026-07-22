#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓库健康检查器 (Repository Health Check)

每次重构、迁移、修改配置后运行，自动检查 6 个维度：
  1. 环境变量 — .env 存在性 + 必需 Secret 非空
  2. 硬编码路径 — 扫描绝对路径、旧目录引用
  3. API 连通性 — Quick BI / 飞书 / Redash / 催收系统
  4. Import 健康 — 关键模块是否可导入
  5. 文件存在性 — 被引用的文件是否存在
  6. 配置完整性 — .gitignore / .env.example / 项目 README

用法:
    python 基础设施/health_check.py              # 全部检查
    python 基础设施/health_check.py --quick      # 跳过 API 连通性（离线模式）
    python 基础设施/health_check.py --env        # 仅环境变量
    python 基础设施/health_check.py --paths      # 仅硬编码路径
    python 基础设施/health_check.py --json       # JSON 格式输出（CI 用）

输出:
    终端彩色报告 + 退出码（0=全部通过, 1=有警告, 2=有错误）
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

# Windows GBK 编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 定位仓库根目录 ─────────────────────────────────────────
_REPO = Path(__file__).resolve()
while not (_REPO / ".git").exists() and _REPO != _REPO.parent:
    _REPO = _REPO.parent
ROOT = _REPO
sys.path.insert(0, str(ROOT))

# ── 终端颜色 ────────────────────────────────────────────────
class Color:
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

def _c(color, text):
    return f"{color}{text}{Color.RESET}"

PASS = _c(Color.GREEN,  "  ✅ PASS")
WARN = _c(Color.YELLOW, "  ⚠️  WARN")
FAIL = _c(Color.RED,    "  ❌ FAIL")
INFO = _c(Color.CYAN,   "  ℹ️  INFO")

# ── 全局状态 ────────────────────────────────────────────────
_results = {"pass": 0, "warn": 0, "fail": 0, "checks": []}
_quiet = False

def _record(name, status, detail=""):
    _results["checks"].append({"name": name, "status": status, "detail": detail})
    _results[status] += 1
    if _quiet and status == "pass":
        return
    icon = {"pass": PASS, "warn": WARN, "fail": FAIL}[status]
    print(f"{icon}  {name}")
    if detail:
        for line in detail.splitlines():
            print(f"       {line}")


# ╔══════════════════════════════════════════════════════════════╗
# ║  检查 1：环境变量                                            ║
# ╚══════════════════════════════════════════════════════════════╝

# 必需凭据（认证用，缺失→FAIL）
REQUIRED_SECRETS = [
    "QBI_ACCESS_KEY",
    "QBI_SECRET_KEY",
    "FEISHU_COLLECTION_APP_ID",
    "FEISHU_COLLECTION_APP_SECRET",
    "FEISHU_SYNC_APP_ID",
    "FEISHU_SYNC_APP_SECRET",
    "ARG_COLLECT_TOKEN",
    "MEX_COLLECT_TOKEN",
    "REDASH_EMAIL",
    "REDASH_PASSWORD",
]

# 可选配置（缺失→WARN，因有默认值）
OPTIONAL_CONFIG = [
    "COLLECT_URL",
    "COLLECT_CONFIG_URL",
    "COLLECT_ORGAN_URL",
    "COLLECT_REGION",
    "FEISHU_CHAT_ID",
    "FEISHU_CHAT_MX",
    "FEISHU_CHAT_AR",
    "MY_OPEN_ID",
    "GROUP_CHAT_ID",
    "DEBUG",
]

# 已知不会出现在 .env 中、在各脚本里有硬编码默认值的 key
# （检查它们是否被 .env 空值覆盖）
FALLBACK_KEYS = {
    "COLLECT_URL":         "催收系统排班 API",
    "COLLECT_CONFIG_URL":  "催收系统 queryConfig API",
    "COLLECT_ORGAN_URL":   "催收系统 queryOrganPage API",
    "COLLECT_REGION":      "催收系统区域",
    "FEISHU_CHAT_ID":      "飞书默认 Chat ID",
    "FEISHU_CHAT_MX":      "飞书墨西哥 Chat ID",
    "FEISHU_CHAT_AR":      "飞书阿根廷 Chat ID",
    "MY_OPEN_ID":          "飞书私聊 Open ID",
    "GROUP_CHAT_ID":       "飞书群聊 Chat ID",
}

def _env_has(key):
    """检查 os.environ 中 key 是否存在且非空"""
    return bool(os.environ.get(key, ""))

def check_env():
    print(f"\n{_c(Color.BOLD, '━━━ 1. 环境变量检查 ━━━')}")

    # 确保 .env 已加载到 os.environ（import 触发 env.py 模块级代码）
    import 基础设施.配置.env as _env_loader  # noqa: F401 — side effect: loads .env into os.environ
    env_file = ROOT / ".env"
    if env_file.exists():
        _record(".env 文件存在", "pass")
    else:
        _record(".env 文件存在", "fail",
                f"缺少 {env_file}\n       请从 .env.example 复制并填入真实值")
        # 后续检查无意义
        return

    # 1b. .env.example 存在性
    example_file = ROOT / ".env.example"
    if example_file.exists():
        _record(".env.example 模板存在", "pass")
    else:
        _record(".env.example 模板存在", "warn",
                "缺少模板文件，新用户无法知道需要哪些环境变量")

    # 1c. 必需 Secret
    missing_required = []
    empty_required = []
    for key in REQUIRED_SECRETS:
        val = os.environ.get(key, "")
        if key not in os.environ:
            missing_required.append(key)
        elif not val:
            empty_required.append(key)

    if not missing_required and not empty_required:
        _record(f"必需 Secret ({len(REQUIRED_SECRETS)} 项)", "pass")
    else:
        if missing_required:
            _record(f"必需 Secret ({len(REQUIRED_SECRETS)} 项)", "fail",
                    f"缺失: {', '.join(missing_required)}")
        if empty_required:
            _record(f"必需 Secret 空值", "fail",
                    f"空值: {', '.join(empty_required)}\n"
                    f"       这些 Key 在 .env 中存在但值为空，请填入真实值")

    # 1d. 可选配置 — 检查是否在 .env 中被设为空值
    empty_optional = []
    for key in OPTIONAL_CONFIG:
        if key in os.environ and not os.environ.get(key, ""):
            empty_optional.append(key)

    if empty_optional:
        _record("可选配置空值覆盖", "warn",
                f"以下 Key 在 .env 中为空值，将覆盖代码中的默认值:\n"
                f"       {', '.join(empty_optional)}\n"
                f"       如非故意，请从 .env 中删除这些行")
    else:
        _record("可选配置未被空值覆盖", "pass")

    # 1e. 检查是否有未在 .env.example 中声明的 key
    if env_file.exists():
        example_keys = set()
        if example_file.exists():
            for line in example_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k = line.partition("=")[0].strip()
                    if k:
                        example_keys.add(k)

        env_keys = set()
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k = line.partition("=")[0].strip()
                v = line.partition("=")[2].strip().strip('"').strip("'")
                if k and v:  # 非空
                    env_keys.add(k)

        undocumented = env_keys - example_keys - {"COLLECT_REGION"}  # COLLECT_REGION 已知
        if undocumented:
            _record(".env 与 .env.example 一致性", "warn",
                    f".env 中有 {len(undocumented)} 个 Key 未在 .env.example 中声明:\n"
                    f"       {', '.join(sorted(undocumented))}")
        else:
            _record(".env 与 .env.example 一致性", "pass")

    # 1f. 验证 get() 修复 — 确认基础设施 get() 对空值返回 default
    from 基础设施.配置.env import get as infra_get
    test_val = infra_get("__HEALTH_CHECK_NONEXISTENT__", "default_ok")
    if test_val == "default_ok":
        _record("infra get() 空值→默认值逻辑", "pass")
    else:
        _record("infra get() 空值→默认值逻辑", "fail",
                f"预期 'default_ok'，实际 '{test_val}'")


# ╔══════════════════════════════════════════════════════════════╗
# ║  检查 2：硬编码路径                                          ║
# ╚══════════════════════════════════════════════════════════════╝

# 危险模式：(正则, 描述, 严重级别)
DANGEROUS_PATTERNS = [
    (r'D:[\\/]knowledge-base[\\/]scripts',  "旧 scripts/ 目录绝对路径", "fail"),
    (r'D:[\\/]新建文件夹[\\/]xby',           "外部目录 D:/新建文件夹/xby", "fail"),
    (r'C:[\\/]Users[\\/]Administrator[\\/]Desktop', "硬编码 Desktop 绝对路径", "warn"),
    (r'sys\.path\.insert\(.*["\']D:[\\/]',  "硬编码 D:/ 路径在 sys.path", "fail"),
    (r'python -m daily_alert\.',            "旧包名 daily_alert（应为 首逾预警系统）", "warn"),
    (r'python -m employee_rating_v2\.',     "旧包名 employee_rating_v2（应为 员工评级系统）", "warn"),
    (r'["\']scripts[\\/]',                  "旧 scripts/ 目录引用", "warn"),
]

# 排除检查的文件/目录
EXCLUDE_GLOBS = [
    ".git/*", "__pycache__/*", "*.pyc", "site/*", ".venv/*",
    "已归档项目/*",  # 归档项目不检查
    "基础设施/health_check.py",  # 本文件
]

def _should_skip(filepath):
    import fnmatch
    rel = str(Path(filepath).relative_to(ROOT))
    for pattern in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(filepath).name, pattern):
            return True
    return False

def check_paths():
    print(f"\n{_c(Color.BOLD, '━━━ 2. 硬编码路径检查 ━━━')}")

    import re as _re

    # 收集所有匹配
    findings = {pattern: [] for pattern, _, _ in DANGEROUS_PATTERNS}

    for py_file in ROOT.rglob("*.py"):
        if _should_skip(py_file):
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for pattern, desc, severity in DANGEROUS_PATTERNS:
            for match in _re.finditer(pattern, content):
                line_no = content[:match.start()].count("\n") + 1
                line_text = content.splitlines()[line_no - 1].strip()[:120]
                findings[pattern].append({
                    "file": str(py_file.relative_to(ROOT)),
                    "line": line_no,
                    "text": line_text,
                    "severity": severity,
                    "desc": desc,
                })

    total = sum(len(v) for v in findings.values())
    if total == 0:
        _record(f"硬编码路径扫描", "pass", "未发现危险路径模式")
        return

    # 按严重级别分组报告
    fails = []
    warns = []
    for pattern, matches in findings.items():
        for m in matches:
            entry = f"{m['file']}:{m['line']}  {m['text'][:100]}"
            if m['severity'] == "fail":
                fails.append(entry)
            else:
                warns.append(entry)

    if fails:
        _record(f"硬编码路径 — 严重 ({len(fails)} 处)", "fail",
                "\n".join(f"       {f}" for f in fails[:10]) +
                (f"\n       ... 还有 {len(fails) - 10} 处" if len(fails) > 10 else ""))

    if warns:
        _record(f"硬编码路径 — 警告 ({len(warns)} 处)", "warn",
                "\n".join(f"       {w}" for w in warns[:10]) +
                (f"\n       ... 还有 {len(warns) - 10} 处" if len(warns) > 10 else ""))

    if not fails and not warns:
        _record(f"硬编码路径扫描", "pass", f"共 {total} 处匹配，均已处理或为误报")


# ╔══════════════════════════════════════════════════════════════╗
# ║  检查 3：API 连通性                                          ║
# ╚══════════════════════════════════════════════════════════════╝

def _try_connect(url, timeout=5, label=""):
    """尝试 HTTP 连接，返回 (ok, message)"""
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url, method="HEAD")
        urllib.request.urlopen(req, timeout=timeout)
        return True, f"{label or url} — 可达"
    except urllib.error.HTTPError as e:
        # 401/403 也算连通（只是没认证），说明服务在运行
        if e.code in (401, 403):
            return True, f"{label or url} — 可达 (HTTP {e.code}, 需认证后访问)"
        return False, f"{label or url} — HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"{label or url} — 不可达: {e.reason}"
    except Exception as e:
        return False, f"{label or url} — 错误: {e}"

def check_api():
    print(f"\n{_c(Color.BOLD, '━━━ 3. API 连通性检查 ━━━')}")

    endpoints = [
        ("https://quickbi-public.cn-hangzhou.aliyuncs.com", "Quick BI"),
        ("https://open.feishu.cn",                          "飞书开放平台"),
        ("http://redash.oneprestamos.com",                  "Redash"),
    ]

    # 催收系统端点（根据区域不同）
    collect_url_mx = os.environ.get("COLLECT_URL",
        "https://loan-collect.maxicredito.loan/vitech-collect-gateway/collect/staff/staffSchedulePage")
    collect_url_ar = "https://loan-collect.middlela.com/vitech-collect-gateway/collect/staff/staffSchedulePage"

    # 提取 host
    for url in [collect_url_mx, collect_url_ar]:
        from urllib.parse import urlparse
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if host not in [e[0] for e in endpoints]:
            endpoints.append((host, f"催收系统 ({urlparse(url).netloc})"))

    all_ok = True
    details = []
    for url, label in endpoints:
        ok, msg = _try_connect(url, timeout=5, label=label)
        if not ok:
            all_ok = False
        details.append(msg)

    if all_ok:
        _record(f"API 连通性 ({len(endpoints)} 个端点)", "pass",
                "\n".join(f"       {d}" for d in details))
    else:
        failed = [d for d in details if "不可达" in d or "错误" in d]
        _record(f"API 连通性 ({len(endpoints)} 个端点)", "warn",
                "\n".join(f"       {d}" for d in details) +
                "\n       注意：部分 API 可能需要 VPN 或内网环境")


# ╔══════════════════════════════════════════════════════════════╗
# ║  检查 4：Import 健康                                         ║
# ╚══════════════════════════════════════════════════════════════╝

IMPORT_CHECKS = [
    # (模块路径, 用途, 是否关键)
    ("基础设施.配置.env",    "环境变量加载",    True),
    ("基础设施.配置.paths",  "公共路径管理",    False),
    ("基础设施.配置.constants", "公共常量",     False),
    ("pathlib",              "标准库",          True),
    ("json",                 "标准库",          True),
    ("openpyxl",             "Excel 读写",      False),
    ("requests",             "HTTP 请求",       False),
]

def check_imports():
    print(f"\n{_c(Color.BOLD, '━━━ 4. Import 健康检查 ━━━')}")
    import importlib

    critical_fails = []
    optional_fails = []

    for module, purpose, critical in IMPORT_CHECKS:
        try:
            importlib.import_module(module)
        except ImportError as e:
            if critical:
                critical_fails.append(f"{module} ({purpose}): {e}")
            else:
                optional_fails.append(f"{module} ({purpose}): {e}")

    if not critical_fails and not optional_fails:
        _record(f"模块导入 ({len(IMPORT_CHECKS)} 个)", "pass")
    else:
        if critical_fails:
            _record(f"关键模块导入失败", "fail",
                    "\n".join(f"       {f}" for f in critical_fails))
        if optional_fails:
            _record(f"可选模块导入失败", "warn",
                    "\n".join(f"       {f}" for f in optional_fails))


# ╔══════════════════════════════════════════════════════════════╗
# ║  检查 5：文件存在性                                          ║
# ╚══════════════════════════════════════════════════════════════╝

# 关键文件（缺失→FAIL）
CRITICAL_FILES = [
    ".gitignore",
    "README.md",
    "mkdocs.yml",
    ".env.example",
    "基础设施/配置/env.py",
    "基础设施/配置/__init__.py",
    "基础设施/__init__.py",
]

# 每个项目的 README.md（缺失→WARN）
PROJECT_README_PATHS = [
    "项目/催收业务/墨西哥/首逾预警系统/README.md",
    "项目/催收业务/墨西哥/员工评级系统/README.md",
    "项目/催收业务/墨西哥/每日案件预估/README.md",
    "项目/催收业务/墨西哥/日报生成/README.md",
    "项目/催收业务/墨西哥/周报生成/README.md",
    "项目/催收业务/阿根廷/每日案件预估/README.md",
    "项目/WW项目/绩效计算器/README.md",
    "项目/通用工具/README.md",
    "项目/实验项目/飞书同步/README.md",
]

def check_files():
    print(f"\n{_c(Color.BOLD, '━━━ 5. 文件存在性检查 ━━━')}")

    missing_critical = []
    for rel in CRITICAL_FILES:
        if not (ROOT / rel).exists():
            missing_critical.append(rel)

    if missing_critical:
        _record(f"关键文件 ({len(CRITICAL_FILES)} 个)", "fail",
                f"缺失: {', '.join(missing_critical)}")
    else:
        _record(f"关键文件 ({len(CRITICAL_FILES)} 个)", "pass")

    missing_readmes = []
    for rel in PROJECT_README_PATHS:
        if not (ROOT / rel).exists():
            # 检查项目目录是否存在
            proj_dir = Path(rel).parent
            if (ROOT / proj_dir).is_dir():
                missing_readmes.append(rel)

    if missing_readmes:
        _record(f"项目 README ({len(PROJECT_README_PATHS)} 个)", "warn",
                f"缺失 README:\n" +
                "\n".join(f"       {r}" for r in missing_readmes))
    else:
        _record(f"项目 README ({len(PROJECT_README_PATHS)} 个)", "pass")

    # 检查 .env 在 .gitignore 中
    gitignore = ROOT / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8", errors="replace")
        if ".env" in content and "!.env.example" in content:
            _record(".env 已 gitignore (含 !.env.example 例外)", "pass")
        elif ".env" in content:
            _record(".env 已 gitignore", "warn",
                    "未设置 !.env.example 例外，.env.example 也会被忽略")
        else:
            _record(".env 已 gitignore", "fail",
                    ".gitignore 中未包含 .env，敏感凭据可能被提交！")
    else:
        _record(".gitignore 存在", "fail", "文件缺失")


# ╔══════════════════════════════════════════════════════════════╗
# ║  检查 6：配置完整性                                          ║
# ╚══════════════════════════════════════════════════════════════╝

def check_config():
    print(f"\n{_c(Color.BOLD, '━━━ 6. 配置完整性检查 ━━━')}")

    # 6a. mkdocs.yml nav 与 文档/ 目录一致性
    mkdocs_yml = ROOT / "mkdocs.yml"
    docs_dir = ROOT / "文档"
    if mkdocs_yml.exists():
        content = mkdocs_yml.read_text(encoding="utf-8", errors="replace")
        if "docs_dir: 文档" in content:
            _record("mkdocs.yml docs_dir 指向 文档/", "pass")
        else:
            _record("mkdocs.yml docs_dir", "fail",
                    "docs_dir 未指向 '文档'，MkDocs 构建会失败")

        # 检查 nav 中引用的 .md 文件是否存在
        import re as _re
        nav_md_files = _re.findall(r'["\']?([一-鿿\w_\-/+]+\.md)["\']?', content)
        missing_nav = []
        for md in nav_md_files:
            if not (docs_dir / md).exists():
                missing_nav.append(md)

        if missing_nav:
            _record(f"MkDocs nav 链接有效性 ({len(nav_md_files)} 个)", "warn",
                    f"以下文件在 mkdocs.yml nav 中引用但不存在:\n" +
                    "\n".join(f"       文档/{m}" for m in missing_nav))
        else:
            _record(f"MkDocs nav 链接有效性 ({len(nav_md_files)} 个)", "pass")

    # 6b. 基础设施层完整性
    infra_files = [
        "基础设施/__init__.py",
        "基础设施/配置/__init__.py",
        "基础设施/配置/env.py",
        "基础设施/配置/paths.py",
        "基础设施/配置/constants.py",
        "基础设施/日志/__init__.py",
        "基础设施/工具/__init__.py",
        "基础设施/README.md",
    ]
    missing_infra = [f for f in infra_files if not (ROOT / f).exists()]
    if missing_infra:
        _record("基础设施层文件完整性", "fail",
                f"缺失: {', '.join(missing_infra)}")
    else:
        _record(f"基础设施层文件完整性 ({len(infra_files)} 个)", "pass")

    # 6c. 检查有 bootstrap 模式的文件数量
    import re as _re
    bootstrap_count = 0
    for py_file in ROOT.glob("项目/**/*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            if "from 基础设施.配置.env import" in content:
                bootstrap_count += 1
        except Exception:
            pass
    _record(f"已接入基础设施的项目文件", "pass", f"共 {bootstrap_count} 个文件使用基础设施导入")


# ╔══════════════════════════════════════════════════════════════╗
# ║  主入口                                                      ║
# ╚══════════════════════════════════════════════════════════════╝

def main():
    global _quiet

    parser = argparse.ArgumentParser(
        description="仓库健康检查器 — 重构/迁移/配置变更后自动验收",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python 基础设施/health_check.py              # 全部 6 项检查
  python 基础设施/health_check.py --quick      # 跳过 API 连通性
  python 基础设施/health_check.py --json       # JSON 输出（适合 CI）
  python 基础设施/health_check.py --env        # 仅环境变量检查
        """
    )
    parser.add_argument("--quick", action="store_true", help="跳过 API 连通性检查")
    parser.add_argument("--env", action="store_true", help="仅检查环境变量")
    parser.add_argument("--paths", action="store_true", help="仅检查硬编码路径")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--quiet", action="store_true", help="仅显示警告和错误")
    args = parser.parse_args()

    _quiet = args.quiet
    _json_mode = args.json

    if _json_mode:
        # JSON 模式：完全静默，最后只输出 JSON
        _quiet = True

    start = time.time()

    if not _json_mode:
        print(f"\n{_c(Color.BOLD, '╔══════════════════════════════════════╗')}")
        print(f"{_c(Color.BOLD, '║   仓 库 健 康 检 查 器               ║')}")
        print(f"{_c(Color.BOLD, '╚══════════════════════════════════════╝')}")
        print(f"  仓库: {ROOT}")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if args.env:
        check_env()
    elif args.paths:
        check_paths()
    else:
        check_env()
        check_paths()
        if not args.quick:
            check_api()
        elif not _json_mode:
            print(f"\n{_c(Color.BOLD, '━━━ 3. API 连通性检查 ━━━')}")
            print(f"{INFO}  已跳过 (--quick 模式)")
        if not args.quick:
            pass  # API check already printed its own header
        elif _json_mode:
            _record("API 连通性检查", "pass", "已跳过 (--quick 模式)")
        check_imports()
        check_files()
        check_config()

    elapsed = time.time() - start

    total = _results["pass"] + _results["warn"] + _results["fail"]

    if _results["fail"] > 0:
        exit_code = 2
        conclusion = "存在需要修复的问题，建议修复后再提交。"
    elif _results["warn"] > 0:
        exit_code = 1
        conclusion = "基本健康，有警告项，建议检查。"
    else:
        exit_code = 0
        conclusion = "仓库健康，可以安全提交。"

    if _json_mode:
        print(json.dumps({
            "repo": str(ROOT),
            "timestamp": datetime.now().isoformat(),
            "elapsed": round(elapsed, 2),
            "summary": {
                "total": total, "pass": _results["pass"],
                "warn": _results["warn"], "fail": _results["fail"]
            },
            "exit_code": exit_code,
            "conclusion": conclusion,
            "checks": _results["checks"],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"  总计: {total} 项检查  |  "
              f"{_c(Color.GREEN, str(_results['pass']) + ' 通过')}  "
              f"{_c(Color.YELLOW, str(_results['warn']) + ' 警告')}  "
              f"{_c(Color.RED, str(_results['fail']) + ' 失败')}  |  "
              f"耗时: {elapsed:.1f}s")

        if exit_code == 2:
            print(f"\n  {_c(Color.RED, '结论: ' + conclusion)}")
        elif exit_code == 1:
            print(f"\n  {_c(Color.YELLOW, '结论: ' + conclusion)}")
        else:
            print(f"\n  {_c(Color.GREEN, '结论: ' + conclusion)}")

        print(f"{_c(Color.BOLD, '══════════════════════════════════════')}\n")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
