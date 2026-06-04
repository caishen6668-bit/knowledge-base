import pandas as pd

# -------------------------
# 0. 读取数据
# -------------------------
file_path = r"C:\Users\Administrator\Desktop\AR25-31数.xlsx"
df = pd.read_excel(file_path)

# -------------------------
# 1. 先过滤：订单催回率 >= 100% 的数据不要
# -------------------------
df["订单催回率"] = pd.to_numeric(df["订单催回率"], errors="coerce")
df = df[df["订单催回率"] < 1]

# -------------------------
# 2. 转换金额催收率、达成率为数值
# -------------------------
df["金额催收率"] = pd.to_numeric(df["金额催收率"], errors="coerce")
df["达成率"] = pd.to_numeric(df["达成率"], errors="coerce")

# -------------------------
# 3. 个人维度统计
# -------------------------
grouped = (
    df.groupby(["客群_喜象", "逾期阶段", "催员姓名", "小组名称"])
    .agg(
        出勤天数=("催收日期(day)", "nunique"),
        金额回收率=("金额催收率", "mean"),
        达成率=("达成率", "mean")
    )
    .reset_index()
)

# -------------------------
# 4. 重命名列
# -------------------------
grouped.rename(columns={
    "逾期阶段": "逾期级别",
    "催员姓名": "员工名称"
}, inplace=True)

# -------------------------
# 4.1 读取花名册，合并在职状态
# -------------------------
roster_path = r"C:\Users\Administrator\Desktop\花名册.xlsx"
roster = pd.read_excel(roster_path)

# 花名册字段为“姓名”和“是否在职”
roster = roster[["姓名", "是否在职"]].drop_duplicates()

# 合并
grouped = pd.merge(
    grouped,
    roster,
    how="left",
    left_on="员工名称",    # 统计表里的员工名称
    right_on="姓名"        # 花名册里的姓名
)

# 用花名册里的“是否在职”填充“在职状态”，默认仍为“在职”
grouped["在职状态"] = grouped["是否在职"].fillna("在职")
grouped.drop(columns=["是否在职", "姓名"], inplace=True)

# -------------------------
# 5. 调整列顺序
# -------------------------
grouped = grouped[[
    "客群_喜象",
    "逾期级别",
    "员工名称",
    "在职状态",
    "小组名称",
    "出勤天数",
    "金额回收率",
    "达成率"
]]

# -------------------------
# 6. 添加小计行（按金额回收率降序）
# -------------------------
def add_subtotals(df):
    subtotal_list = []
    for (cust, stage), group in df.groupby(["客群_喜象", "逾期级别"]):
        # 排序：金额回收率降序
        group_sorted = group.sort_values(by="金额回收率", ascending=False)

        # 小计行
        subtotal = {
            "客群_喜象": cust,
            "逾期级别": stage,
            "员工名称": "小计",
            "在职状态": "",
            "小组名称": "",
            "出勤天数": group_sorted["出勤天数"].sum(),
            "金额回收率": group_sorted["金额回收率"].mean(),
            "达成率": group_sorted["达成率"].mean()
        }
        subtotal_df = pd.DataFrame([subtotal])

        subtotal_list.append(pd.concat([group_sorted, subtotal_df], ignore_index=True))

    return pd.concat(subtotal_list, ignore_index=True)

grouped_with_subtotals = add_subtotals(grouped)

# -------------------------
# 7. 排序
# -------------------------
grouped_with_subtotals.sort_values(by=["客群_喜象", "逾期级别"], inplace=True)

# -------------------------
# 8. 写入 Excel
# -------------------------
output_path = r"C:\Users\Administrator\Desktop\AR排名分析结果.xlsx"
with pd.ExcelWriter(output_path) as writer:
    grouped_with_subtotals.to_excel(writer, sheet_name="个人统计_含小计", index=False)

print("✅ 统计结果已输出到：", output_path)