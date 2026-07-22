# xby每日.py

## 功能说明

XBY 每日数据处理。读取每日催收数据，进行清洗、汇总和格式化输出。

## 完整代码

```python
import pandas as pd
import os
from datetime import datetime

# =====================
# 路径（改成你的）
# =====================
base_path = r"D:\新建文件夹\xby\自动报表"

input_file = os.path.join(base_path, "system_data.xlsx")
output_dir = os.path.join(base_path, "output")

# 自动创建 output 文件夹
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# =====================
# 读取数据
# =====================
df = pd.read_excel(input_file)

# 日期处理
df['日期'] = pd.to_datetime(df['日期'])

# =====================
# 回收数计算（你的业务口径）
# =====================

# 新客（首借）
df_new = df.copy()
df_new['客群'] = '新客'
df_new['回收数'] = (
    df_new['首借结清单数'].fillna(0)
    + df_new['首借延期单数'].fillna(0)
)

# 老客（复借）
df_old = df.copy()
df_old['客群'] = '老客'
df_old['回收数'] = (
    df_old['复借结清单数'].fillna(0)
    + df_old['复借延期单数'].fillna(0)
    + df_old['分期结清单数'].fillna(0)
)

# 合并
df_all = pd.concat([df_new, df_old])

# =====================
# 汇总每日数据
# =====================
daily = df_all.groupby(['日期','账户','客群']).agg({
    '在手案件数':'sum',
    '回收数':'sum'
}).reset_index()

daily.rename(columns={'在手案件数':'案件数'}, inplace=True)

# =====================
# 计算回收率
# =====================
daily['回收率'] = daily['回收数'] / daily['案件数']
daily['回收率'] = daily['回收率'].fillna(0)

# =====================
# 文件名带“数据日期”
# =====================
max_date = daily['日期'].max().strftime('%Y-%m-%d')
output_file = os.path.join(output_dir, f"自动日报_{max_date}.xlsx")

# =====================
# 输出
# =====================
with pd.ExcelWriter(output_file) as writer:
    daily.to_excel(writer, sheet_name='每日明细', index=False)

print("✅ 报表已生成：", output_file)
```
