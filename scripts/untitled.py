import pandas as pd

# === 1. 读取源文件 ===
df = pd.read_excel(r"C:\Users\你的用户名\Desktop\9.8-9.10组员回收.xlsx")

# === 2. 数据清洗 ===
# 转换百分比字段
for col in ["催回率", "达成率"]:
    if col in df.columns:
        df[col] = df[col].astype(str).str.replace("%", "", regex=False).astype(float) / 100

# === 3. 剔除订单回收率>=100%的记录 ===
if "订单回收率" in df.columns:
    df = df[df["订单回收率"] < 1]

# === 4. 添加目标列（示例：默认目标35%，你可以改成读取表里的字段）===
df["目标"] = 0.35

# === 5. 格式调整 ===
df = df.rename(columns={
    "逾期级别": "逾期阶段",
    "员工名称": "员工姓名",
    "小组名称": "小组名称",
    "在职状态": "在职状态",
    "出勤天数": "出勤天数",
    "入催单量": "入催单量",
    "实还单量": "实还单量",
    "分案金额": "分案金额总计",
    "还款金额": "总还款金额"
})

# === 6. 各阶段小计（只统计：入催单量 实还单量 分案金额总计 总还款金额 金额催回率）===
stage_summary = df.groupby("逾期阶段").agg({
    "入催单量": "sum",
    "实还单量": "sum",
    "分案金额总计": "sum",
    "总还款金额": "sum"
}).reset_index()

stage_summary["金额催回率"] = stage_summary["总还款金额"] / stage_summary["分案金额总计"]

stage_summary["员工姓名"] = stage_summary["逾期阶段"] + "小计"
stage_summary["客群"] = "all"
stage_summary["目标"] = None
stage_summary["小组名称"] = None
stage_summary["在职状态"] = None
stage_summary["出勤天数"] = None
stage_summary["达成率"] = None

# === 7. 拼接原数据和小计 ===
result = pd.concat([df, stage_summary], ignore_index=True, sort=False)

# === 8. 百分比格式化 ===
for col in ["金额催回率", "达成率", "目标"]:
    if col in result.columns:
        result[col] = result[col].apply(lambda x: f"{x:.2%}" if pd.notnull(x) else "")

# === 9. 调整列顺序 ===
result = result[[
    "逾期阶段", "员工姓名", "小组名称", "在职状态", "出勤天数", "客群", "目标",
    "分案金额总计", "总还款金额", "金额催回率", "达成率", "入催单量", "实还单量"
]]

# === 10. 保存结果 ===
result.to_excel(r"C:\Users\你的用户名\Desktop\组员考核数据表.xlsx", index=False)
print("已生成：组员考核数据表.xlsx")
