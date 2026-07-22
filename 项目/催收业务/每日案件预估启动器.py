#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日案件预估（全部）— 总启动脚本

流程（每个国家只跑一遍）：
  1. 运行脚本 → 发送到用户私聊（预览审核）
  2. 等待用户确认
  3. 启动器从私聊读取刚发送的消息 → 转发到群聊

本脚本仅作为启动器，不包含任何业务逻辑。
"""

import subprocess
import sys
import os
import json
import urllib.request
from pathlib import Path

# 定位仓库根目录，加载基础设施
_repo = Path(__file__).resolve()
while not (_repo / ".git").exists() and _repo != _repo.parent:
    _repo = _repo.parent
sys.path.insert(0, str(_repo))
from 基础设施.配置.env import require, get

# 强制使用 UTF-8 输出，避免 Windows GBK 编码问题
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ------------------------------------------------------------
# 飞书配置（启动器用于：读取私聊消息 + 转发到群聊）
# ------------------------------------------------------------
FEISHU_APP_ID = require("FEISHU_COLLECTION_APP_ID")
FEISHU_APP_SECRET = require("FEISHU_COLLECTION_APP_SECRET")
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

# 用户私聊 open_id（以 ou_ 开头）— 预览审核用
MY_OPEN_ID = os.environ.get("MY_OPEN_ID", "ou_ffab3a07f1ff9fbca2a593c0d5e152ac")

# 群聊 chat_id（以 oc_ 开头）— 审核通过后转发目标
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID", "oc_8b5ef4aee4e93b29326cd8c0f3c24d90")


class FeishuForwarder:
    """飞书消息转发器（仅读取消息 + 转发，不涉及业务）"""

    def __init__(self):
        self._token = None
        self._private_chat_id = None

    @property
    def token(self):
        if self._token is None:
            self._token = self._get_token()
        return self._token

    def _get_token(self):
        body = json.dumps({
            "app_id": FEISHU_APP_ID,
            "app_secret": FEISHU_APP_SECRET,
        }).encode("utf-8")
        req = urllib.request.Request(
            FEISHU_TOKEN_URL, data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != 0:
            raise Exception(f"飞书认证失败: {data.get('msg')}")
        return data["tenant_access_token"]

    def _api_get(self, url):
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _api_post(self, url, body):
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.token}",
            },
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_private_chat_id(self, open_id):
        """通过用户 open_id 查找机器人与该用户的私聊 chat_id"""
        if self._private_chat_id:
            return self._private_chat_id

        url = (
            "https://open.feishu.cn/open-apis/im/v1/chats"
            + f"?user_id_type=open_id"
            + f"&user_id={open_id}"
            + "&page_size=10"
        )
        data = self._api_get(url)
        if data.get("code") != 0:
            raise Exception(f"查找私聊会话失败: {data.get('msg')}")

        chats = data.get("data", {}).get("items", [])
        for chat in chats:
            # 私聊的 chat_mode 为 "p2p"
            if chat.get("chat_mode") == "p2p":
                self._private_chat_id = chat["chat_id"]
                return self._private_chat_id

        raise Exception(f"未找到与用户 {open_id} 的私聊会话")

    def get_last_message(self, chat_id):
        """获取指定会话的最新一条消息"""
        url = (
            FEISHU_MSG_URL
            + f"?receive_id_type=chat_id"
            + f"&receive_id={chat_id}"
            + "&page_size=1"
            + "&sort_type=ByCreateTimeDesc"
        )
        data = self._api_get(url)
        if data.get("code") != 0:
            raise Exception(f"读取消息失败: {data.get('msg')}")
        items = data.get("data", {}).get("items", [])
        if not items:
            raise Exception("未找到消息")
        return items[0]

    def forward_message(self, msg, target_id, target_type="chat_id"):
        """将一条消息转发到目标会话（仅支持 card 和 text 类型）"""
        msg_type = msg.get("msg_type")
        body = msg.get("body", {}).get("content", "")

        if msg_type == "interactive":
            # card 消息：直接复用原始 card JSON
            card = json.loads(body) if isinstance(body, str) else body
            payload = {
                "receive_id": target_id,
                "msg_type": "interactive",
                "content": json.dumps(card, ensure_ascii=False),
            }
        elif msg_type == "text":
            payload = {
                "receive_id": target_id,
                "msg_type": "text",
                "content": body,
            }
        else:
            raise Exception(f"不支持的消息类型: {msg_type}")

        result = self._api_post(
            FEISHU_MSG_URL + f"?receive_id_type={target_type}",
            payload,
        )
        if result.get("code") != 0:
            raise Exception(f"转发失败: {result.get('msg')} (code={result.get('code')})")
        return result


def run_script(script_path: str, label: str, chat_id: str) -> bool:
    """
    运行一个 Python 脚本（仅一次），发送到指定 chat_id。
    返回 True 表示成功。
    """
    print(f"\n>>> 正在运行：{label}")
    print(f"    发送目标：{'私聊' if chat_id.startswith('ou_') else '群聊'}")
    print("-" * 60)

    env = os.environ.copy()
    env["FEISHU_CHAT_ID"] = chat_id

    result = subprocess.run(
        [sys.executable, script_path, "--chat-id", chat_id],
        cwd=os.path.dirname(script_path),
        env=env,
    )

    if result.returncode == 0:
        print(f"[OK] {label} 完成")
        return True
    else:
        print(f"[FAIL] {label} 失败（退出码: {result.returncode}）")
        return False


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    mexico_script = os.path.join(base_dir, "墨西哥每日案件及人力预估_v2.py")
    argentina_script = os.path.join(base_dir, "阿根廷每日案件及人力预估.py")

    fw = FeishuForwarder()

    print("=" * 60)
    print("开始执行每日案件预估（全部）")
    print("=" * 60)

    # 先查到私聊会话的 chat_id（飞书读消息 API 需要 chat_id）
    try:
        print("正在查找私聊会话...")
        private_chat_id = fw.get_private_chat_id(MY_OPEN_ID)
        print(f"[OK] 私聊会话: {private_chat_id}")
    except Exception as e:
        print(f"[FAIL] 无法查找私聊会话: {e}")
        return

    # ================================================================
    # 1. 墨西哥：跑一遍 → 私聊 → 审核 → 转发群聊
    # ================================================================
    ok = run_script(mexico_script, "墨西哥每日案件及人力预估_v2.py", MY_OPEN_ID)
    if ok:
        input("\n>>> 审核通过？按 Enter 转发到群聊（Ctrl+C 取消）...")
        try:
            print("    正在从私聊读取消息...")
            msg = fw.get_last_message(private_chat_id)
            fw.forward_message(msg, GROUP_CHAT_ID, "chat_id")
            print(f"[转发] 已发送到群聊 -> {GROUP_CHAT_ID}")
        except Exception as e:
            print(f"[FAIL] 转发失败: {e}")
    else:
        print("\n[WARN] 墨西哥日报失败，跳过转发")

    # ================================================================
    # 2. 阿根廷：跑一遍 → 私聊 → 审核 → 转发群聊
    # ================================================================
    ok = run_script(argentina_script, "阿根廷每日案件及人力预估.py", MY_OPEN_ID)
    if ok:
        input("\n>>> 审核通过？按 Enter 转发到群聊（Ctrl+C 取消）...")
        try:
            print("    正在从私聊读取消息...")
            msg = fw.get_last_message(private_chat_id)
            fw.forward_message(msg, GROUP_CHAT_ID, "chat_id")
            print(f"[转发] 已发送到群聊 -> {GROUP_CHAT_ID}")
        except Exception as e:
            print(f"[FAIL] 转发失败: {e}")
    else:
        print("\n[WARN] 阿根廷日报失败，跳过转发")

    print("\n" + "=" * 60)
    print("[DONE] 每日案件预估（全部）执行完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
