import pandas as pd
import os

# 1. 路径设置
desktop = os.path.join(os.path.expanduser("~"), "Desktop")
input_file = os.path.join(desktop, "20-26数.xlsx")
output_file = os.path.join(desktop, "阶段目标+达成率+加权平均.xlsx")

# 2. 读取数据并修正列名
df = pd.read_excel(input_file)
df = df.rename(columns={
    '催员姓名': 'agent',         # 催员姓名
    '逾期阶段': 'stage',         # 逾期阶段（如M1、M2）
    '当日入催金额': 'in_amount',  # 当日入催金额
    '当日回款金额': 'repay_amount',  # 当日回款金额
    '目标': 'target'            # 阶段目标（核心：保留原目标值）
})

# 3. 按“催员+阶段”分组，计算每个阶段的核心数据（含阶段目标）
stage_group = df.groupby(['agent', 'stage']).agg(
    阶段总入催金额=('in_amount', 'sum'),    # 阶段业务量
    阶段总回款金额=('repay_amount', 'sum'), # 阶段实际回款
    阶段目标=('target', 'mean')            # 显式保留阶段目标（同一阶段取平均）
).reset_index()

# 4. 计算每个阶段的达成率（与阶段目标对应）
stage_group['阶段达成率'] = (stage_group['阶段总回款金额'] / stage_group['阶段总入催金额']) / stage_group['阶段目标']
# 格式化达成率为百分比（可选，更直观）
stage_group['阶段达成率'] = stage_group['阶段达成率'].apply(lambda x: f"{x:.2%}")

# 5. 计算每个催员的总入催金额（用于算阶段权重）
agent_total = stage_group.groupby('agent')['阶段总入催金额'].sum().reset_index()
agent_total.columns = ['agent', '催员总入催金额']

# 6. 合并总金额，计算阶段权重（业务量大的阶段权重高）
stage_with_weight = pd.merge(stage_group, agent_total, on='agent', how='left')
stage_with_weight['阶段权重'] = stage_with_weight['阶段总入催金额'] / stage_with_weight['催员总入催金额']
stage_with_weight['阶段权重'] = stage_with_weight['阶段权重'].apply(lambda x: f"{x:.2%}")  # 格式化权重为百分比

# 7. 计算加权平均达成率（还原数值计算，再格式化）
# 先将“阶段达成率”从百分比转回数值
stage_with_weight['阶段达成率数值'] = stage_with_weight['阶段达成率'].str.strip('%').astype(float) / 100
# 计算加权平均（数值）
agent_weighted_avg = stage_with_weight.groupby('agent').apply(
    lambda x: (x['阶段达成率数值'] * (x['阶段总入催金额'] / x['催员总入催金额'])).sum()
).reset_index()
agent_weighted_avg.columns = ['agent', '加权平均达成率']
agent_weighted_avg['加权平均达成率'] = agent_weighted_avg['加权平均达成率'].apply(lambda x: f"{x:.2%}")  # 格式化为百分比

# 8. 合并最终结果（含阶段目标、达成率、权重、加权平均）
final_result = pd.merge(
    stage_with_weight[['agent', 'stage', '阶段总入催金额', '阶段目标', '阶段达成率', '阶段权重']],
    agent_weighted_avg,
    on='agent',
    how='left'
)

# 9. 保存结果
final_result.to_excel(output_file, index=False)

print(f"✅ 计算完成！文件已保存到桌面：{output_file}")