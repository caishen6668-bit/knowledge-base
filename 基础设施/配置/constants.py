"""
基础设施/配置/constants.py — 公共常量

用法:
    from 基础设施.配置.constants import QBI_ENDPOINT, FEISHU_TOKEN_URL

存放无需保密的固定值：
    - API 端点 URL
    - Quick BI 数据集 ApiId
    - 国家/App 名称
    - 业务常量
"""

# ============================================================
# Quick BI
# ============================================================
QBI_ENDPOINT = "quickbi-public.cn-hangzhou.aliyuncs.com"
QBI_API_BASE = "https://quickbi-public.cn-hangzhou.aliyuncs.com"

# 数据集 ApiId
QBI_API_RECOVERY = "524c3ccd429c"     # 回收率
QBI_API_CASES = "c2f93e0fa45b"        # 案件量
QBI_API_PERFORMANCE = "c4f429db60b3"  # 业绩
QBI_API_ATTENDANCE = "7f9969dc9020"   # 出勤

# ============================================================
# 飞书开放平台
# ============================================================
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_CHAT_LIST_URL = "https://open.feishu.cn/open-apis/im/v1/chats"

# ============================================================
# Redash
# ============================================================
REDASH_URL = "http://redash.oneprestamos.com"
REDASH_QUERY_ID = 79  # 催收员成绩统计报表

# ============================================================
# 业务常量
# ============================================================
MEXICO_APPS = {"AndaLana", "Cridit", "Kredizo", "ServiCash", "TruCred"}
ARGENTINA_APPS = {"Instamonei"}
