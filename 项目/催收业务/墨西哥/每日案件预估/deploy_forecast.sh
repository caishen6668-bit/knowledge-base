#!/bin/bash
# ======================================================
# 喜象&云盾案件量预估 — 服务器部署脚本
# ======================================================
# 用法:
#   chmod +x deploy.sh && ./deploy.sh
# ======================================================

set -e

PROJECT_DIR="/opt/case-forecast"
SCRIPT_NAME="daily_case_forecast.py"

echo "🚀 部署喜象&云盾案件量预估推送脚本..."

# 1. 创建目录
echo "[*] 创建项目目录..."
mkdir -p "$PROJECT_DIR/logs"
cd "$PROJECT_DIR"

# 2. 安装依赖
echo "[*] 安装 Python 依赖..."
pip3 install openpyxl requests -q

# 3. 配置
echo "[*] 配置..."
if [ ! -f config.json ]; then
    cat > config.json << 'EOF'
{
  "excel_path": "/data/excel/喜象&云盾案件量预估.xlsx",
  "feishu": {
    "app_id": "$FEISHU_SYNC_APP_ID",
    "app_secret": "$FEISHU_SYNC_APP_SECRET",
    "chat_id": "请替换为实际的chat_id"
  }
}
EOF
    echo "  已生成 config.json，请设置环境变量 FEISHU_SYNC_APP_ID 和 FEISHU_SYNC_APP_SECRET"
fi

# 4. 设置 cron
echo "[*] 配置 cron 定时任务 (北京时间每日 11:00)..."
CRON_JOB="0 3 * * * cd $PROJECT_DIR && /usr/bin/python3 $PROJECT_DIR/$SCRIPT_NAME >> $PROJECT_DIR/logs/cron.log 2>&1"

# 检查是否已存在
if crontab -l 2>/dev/null | grep -q "$SCRIPT_NAME"; then
    echo "  cron 任务已存在，跳过"
else
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "  cron 已配置: $CRON_JOB"
fi

echo ""
echo "✅ 部署完成！"
echo ""
echo "后续步骤:"
echo "  1. 编辑 $PROJECT_DIR/config.json 填入实际的 chat_id"
echo "  2. 确保 Excel 文件在 config.json 中指定的路径"
echo "  3. 手动测试: python3 $PROJECT_DIR/$SCRIPT_NAME --dry-run"
echo "  4. 发送测试: python3 $PROJECT_DIR/$SCRIPT_NAME"
echo "  5. 查看日志: tail -f $PROJECT_DIR/logs/cron.log"
