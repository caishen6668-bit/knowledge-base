#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
首逾智能预警系统 — 入口脚本

用法:
  python 每日数据预警.py                        # 当天，墨西哥+阿根廷
  python 每日数据预警.py --country MX           # 仅墨西哥
  python 每日数据预警.py --country AR           # 仅阿根廷
  python 每日数据预警.py --date 2026-07-07      # 指定日期
  python 每日数据预警.py --dry-run              # 仅计算，不发送
  python 每日数据预警.py --text-only            # 纯文本降级
  python 每日数据预警.py --list-chats           # 列出可用群聊
  python 每日数据预警.py --stage D1             # 分析 D1 阶段
"""

import sys
import os

# 确保父目录在 path 中，使 daily_alert 包可以被导入
_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from daily_alert.main import main

if __name__ == "__main__":
    main()
