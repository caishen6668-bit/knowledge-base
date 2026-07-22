import pandas as pd
import os

# 桌面路径
desktop_path = r"C:\Users\Administrator\Desktop"
recovery_file = os.path.join(desktop_path, "9.8-9.10组员回收.xlsx")
assessment_file = os.path.join(desktop_path, "9.1-9.7组员考核.xlsx")  # 可作为模板
output_file = os.path.join(desktop_path, "组员考核_金额回收率_美化.xlsx")

# 读取 Excel
df_recovery = pd.read_excel(recovery_file)
df_recovery.columns = df_recovery.columns.str.strip()
df_assessment = pd.read_excel(assessment_file)
df_assessment.columns = df_assessment.columns.str.strip()

# 剔除订单回收率 >=100%
df_recovery = df_recovery[df_recovery['订单催回率'] < 100]

# 计算金额催回率
df_recovery['金额催回率'] = df_recovery['总还款金额'] / df_recovery['分案金额_观测口径']

# 目标百分比
df_recovery['目标'] = df_recovery['目标'] / 100  # 数值转百分比

# 计算达成率 = 金额催回率 / 目标
df_recovery['达成率'] = df_recovery['金额催回率'] / df_recovery['目标']

# 分客群 new/old
groups = ['new', 'old']
phase_list = df_recovery['逾期阶段'].unique()
final_list = []

for phase in phase_list:
    for group in groups:
        temp = df_recovery[(df_recovery['逾期阶段']==phase) & (df_recovery['客群_喜象']==group)]
        if temp.empty:
            continue
        # 按员工汇总
        temp_summary = temp.groupby(['员工姓名','在职状态','小组名称']).agg(
            客群=('客群_喜象','first'),
            目标=('目标','first'),
            分案金额总计=('分案金额_观测口径','sum'),
            总还款金额=('总还款金额','sum'),
            金额催回率=('金额催回率','mean'),
            达成率=('达成率','mean'),
            入催单量=('分案笔数_观测口径','sum'),
            实还单量=('结清笔数','sum')
        ).reset_index()
        temp_summary['逾期阶段'] = phase
        final_list.append(temp_summary)
    
    # 阶段小计 (all 客群)
    temp_all = df_recovery[df_recovery['逾期阶段']==phase]
    if not temp_all.empty:
        subtotal = pd.DataFrame({
            '员工姓名':[f'{phase}小计'],
            '在职状态':[''],
            '小组名称':[''],
            '客群':['all'],
            '目标':[temp_all['目标'].mean()],
            '分案金额总计':[temp_all['分案金额_观测口径'].sum()],
            '总还款金额':[temp_all['总还款金额'].sum()],
            '金额催回率':[temp_all['总还款金额'].sum()/temp_all['分案金额_观测口径'].sum()],
            '达成率':[ (temp_all['总还款金额'].sum()/temp_all['分案金额_观测口径'].sum()) / temp_all['目标'].mean() ],
            '入催单量':[temp_all['分案笔数_观测口径'].sum()],
            '实还单量':[temp_all['结清笔数'].sum()],
            '逾期阶段':[phase]
        })
        final_list.append(subtotal)

# 合并
df_final = pd.concat(final_list, ignore_index=True)

# 如果没有“出勤天数”列，先加空列
if '出勤天数' not in df_final.columns:
    df_final['出勤天数'] = ''

# 调整列顺序
cols_order = ['逾期阶段','员工姓名','小组名称','在职状态','出勤天数','客群',
              '目标','分案金额总计','总还款金额','金额催回率','达成率',
              '入催单量','实还单量']
df_final = df_final[[c for c in cols_order if c in df_final.columns]]

# 百分比显示
df_final['目标'] = (df_final['目标']*100).round(2).astype(str) + '%'
df_final['金额催回率'] = (df_final['金额催回率']*100).round(2).astype(str) + '%'
df_final['达成率'] = (df_final['达成率']*100).round(2).astype(str) + '%'

# 保存 Excel 美化
with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
    df_final.to_excel(writer, index=False, sheet_name='组员考核')
    workbook  = writer.book
    worksheet = writer.sheets['组员考核']
    
    # 设置列宽
    worksheet.set_column('A:A', 10)
    worksheet.set_column('B:B', 15)
    worksheet.set_column('C:C', 12)
    worksheet.set_column('D:D', 10)
    worksheet.set_column('E:E', 8)
    worksheet.set_column('F:F', 8)
    worksheet.set_column('G:G', 8)
    worksheet.set_column('H:J', 15)
    worksheet.set_column('K:M', 12)

print("组员考核表已生成完成，路径为:", output_file)
