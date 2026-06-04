#!/bin/bash
cd "$(dirname "$0")"
echo "🚀 启动知识库..."
echo "浏览器打开: http://127.0.0.1:8000"
echo "按 Ctrl+C 停止服务"
echo ""
.venv/bin/mkdocs serve
