#!/usr/bin/env python3
"""
飞书云文档 → MkDocs 知识库同步脚本

使用说明：
    1. 在飞书开发者后台创建应用，添加 drive:drive:readonly 等权限
    2. 将 App ID 和 App Secret 填入下方
    3. 运行: python scripts/sync_feishu.py

依赖：
    pip install requests python-docx markdown
"""

import os
import sys
import json
import re
import time
from pathlib import Path

import requests

# 定位仓库根目录，加载基础设施
_repo = Path(__file__).resolve()
while not (_repo / ".git").exists() and _repo != _repo.parent:
    _repo = _repo.parent
sys.path.insert(0, str(_repo))
from 基础设施.配置.env import require

# ============================================================
# 配置区 - 飞书应用凭证（从环境变量加载）
# ============================================================
APP_ID = require("FEISHU_SYNC_APP_ID")
APP_SECRET = require("FEISHU_SYNC_APP_SECRET")

# 知识库路径
BASE_DIR = Path(__file__).parent.parent
DOCS_DIR = BASE_DIR / "docs" / "work" / "feishu"
NAV_FILE = BASE_DIR / "mkdocs.yml"

# 需要同步的文件夹（设为 None 则同步整个云文档）
SYNC_FOLDER_TOKEN = None  # 例如: "nodcn9g4D5BcCMZoH6vLyHWItXk"


class FeishuSync:
    """飞书云文档同步器"""

    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = None
        self.token_expire = 0

    def _get_token(self):
        """获取 tenant_access_token"""
        if time.time() < self.token_expire:
            return self.token

        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"认证失败: {data.get('msg')}")

        self.token = data["tenant_access_token"]
        self.token_expire = time.time() + data["expire"] - 60
        return self.token

    def _headers(self):
        return {"Authorization": f"Bearer {self._get_token()}"}

    def list_files(self, folder_token=None, page_size=50):
        """列出云文档文件"""
        url = "https://open.feishu.cn/open-apis/drive/v1/files"
        params = {"page_size": page_size}
        if folder_token:
            params["folder_token"] = folder_token

        all_files = []
        page_token = None

        while True:
            if page_token:
                params["page_token"] = page_token

            resp = requests.get(url, headers=self._headers(), params=params, timeout=10)
            data = resp.json()

            if data.get("code") != 0:
                raise Exception(f"列出文件失败: {data.get('msg')}")

            files = data.get("data", {}).get("files", [])
            all_files.extend(files)

            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("next_page_token")

        return all_files

    def get_document_content(self, token, doc_type="docx"):
        """获取文档内容"""
        if doc_type == "docx":
            # 新版文档格式
            url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{token}/raw_content"
            resp = requests.get(url, headers=self._headers(), timeout=15)
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", {}).get("content", "")

            # 尝试纯文本格式
            url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{token}/raw_content?type=text"
            resp = requests.get(url, headers=self._headers(), timeout=15)
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", {}).get("content", "")

        elif doc_type == "sheet":
            # 电子表格 - 只获取元信息
            url = f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{token}"
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                return f"[电子表格] 需要手动查看"
            return "[电子表格]"

        elif doc_type == "file":
            # 普通文件 - 无法直接读取内容
            return "[文件附件]"

        return "[无法读取内容]"

    def get_filename(self, token, doc_type="docx"):
        """获取文档的文件名"""
        if doc_type == "docx":
            url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{token}"
        elif doc_type == "sheet":
            url = f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{token}"
        else:
            return f"unknown_{token}"

        resp = requests.get(url, headers=self._headers(), timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", {}).get("document", {}).get("title", token)
        return token

    def sanitize_filename(self, name):
        """清理文件名中的非法字符"""
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        name = re.sub(r'\s+', ' ', name).strip()
        return name[:80]  # 限制长度

    def docx_to_markdown(self, raw_content, title):
        """将飞书文档内容转换为 Markdown"""
        lines = []
        lines.append(f"# {title}")
        lines.append("")
        lines.append(f"> 自动从飞书同步 - 来源: 飞书云文档")
        lines.append("")

        # 飞书 raw_content 已经是 Markdown 格式
        # 基本清理
        content = raw_content.replace("\r\n", "\n")
        lines.append(content)

        return "\n".join(lines)

    def sync_folder(self, folder_token=None, path_prefix=""):
        """同步文件夹下的所有文档"""
        files = self.list_files(folder_token)
        synced = []

        for f in files:
            name = f.get("name", "未命名")
            token = f.get("token", "")
            ftype = f.get("type", "file")

            print(f"  📄 {name} ({ftype})")

            if ftype == "folder":
                # 递归处理子文件夹
                sub_path = f"{path_prefix}/{name}" if path_prefix else name
                sub_synced = self.sync_folder(token, sub_path)
                synced.extend(sub_synced)
                continue

            if ftype not in ("docx", "sheet"):
                print(f"    ⏭️ 跳过不支持的格式: {ftype}")
                continue

            try:
                # 读取文档内容
                content = self.get_document_content(token, ftype)

                if content.startswith("[") and content.endswith("]"):
                    print(f"    ⏭️ {content}")
                    continue

                # 生成 Markdown 文件
                md_content = self.docx_to_markdown(content, name)

                # 确定保存路径
                rel_path = path_prefix.replace("/", os.sep) if path_prefix else ""
                target_dir = DOCS_DIR / rel_path
                target_dir.mkdir(parents=True, exist_ok=True)

                safe_name = self.sanitize_filename(name)
                md_path = target_dir / f"{safe_name}.md"

                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)

                print(f"    ✅ 已同步: {md_path.relative_to(BASE_DIR)}")
                synced.append({
                    "name": name,
                    "path": str(md_path.relative_to(BASE_DIR)),
                    "type": ftype,
                    "folder": path_prefix or "/",
                })

            except Exception as e:
                print(f"    ❌ 失败: {e}")

        return synced

    def update_nav(self, synced_files):
        """更新 mkdocs.yml 导航"""
        nav_path = NAV_FILE
        with open(nav_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 查找导航中的 feishu 段
        feishu_start = content.find("  - 📥 飞书文档:")
        feishu_end = -1

        feishu_nav = "  - 📥 飞书文档:\n    - 概览: work/feishu/index.md\n"

        # 按文件夹分组
        folders = {}
        for sf in synced_files:
            folder = sf["folder"]
            if folder not in folders:
                folders[folder] = []
            folders[folder].append(sf)

        for folder, items in sorted(folders.items()):
            if folder == "/":
                for item in items:
                    safe_name = self.sanitize_filename(item["name"])
                    feishu_nav += f"    - {item['name']}: work/feishu/{safe_name}.md\n"
            else:
                feishu_nav += f"    - {folder}:\n"
                for item in items:
                    safe_name = self.sanitize_filename(item["name"])
                    feishu_nav += f"      - {item['name']}: work/feishu/{item['folder']}/{safe_name}.md\n"

        if feishu_start >= 0:
            # 替换已有导航
            new_content = content[:feishu_start] + feishu_nav
        else:
            # 在 copyright 前插入
            copyright_idx = content.find("copyright:")
            new_content = content[:copyright_idx] + feishu_nav + "\n" + content[copyright_idx:]

        with open(nav_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        print(f"\n📝 已更新导航配置: {nav_path}")


def main():
    print("🚀 开始同步飞书云文档...")
    print(f"📂 同步到: {DOCS_DIR}")
    print()

    sync = FeishuSync(APP_ID, APP_SECRET)

    # 创建飞书文档目录
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # 创建概览页
    index_content = """# 📥 飞书云文档

> 自动从飞书同步的云文档

## 内容列表

"""
    with open(DOCS_DIR / "index.md", "w", encoding="utf-8") as f:
        f.write(index_content)

    print("📋 扫描飞书云文档...\n")

    try:
        # 同步文档
        synced = sync.sync_folder(SYNC_FOLDER_TOKEN)

        if not synced:
            print("\n⚠️ 没有同步到任何文档")
            return

        print(f"\n✅ 同步完成！共同步 {len(synced)} 篇文档")

        # 更新导航
        sync.update_nav(synced)

        print("\n💡 提示: 运行以下命令重新构建站点")
        print("   .venv/Scripts/mkdocs build")
        print("   .venv/Scripts/mkdocs gh-deploy  # 部署到 GitHub Pages")

    except Exception as e:
        print(f"\n❌ 同步失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
