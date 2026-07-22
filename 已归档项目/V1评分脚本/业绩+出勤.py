import pandas as pd
import os

desktop = os.path.join(os.path.expanduser("~"), "Desktop")

# === 1. 读取已有结果：业绩 + 出勤 ===
perf_file = os.path.join(desktop, "业绩达成率.xlsx")
att_file  = os.path.join(desktop, "出勤得分.xlsx")

perf = pd.read_excel(perf_file)
att  = pd.read_excel(att_file)

# 保证都有 agent 列
perf = perf.rename(columns={'agent': 'agent'})
att  = att.rename(columns={'agent': 'agent'})

# === 2. 从 综合达成率_百分比 还原成数值 ===
# 假设格式类似： "112.35%"
s_rate = perf['综合达成率_百分比'].astype(str).str.strip().str.replace('%', '', regex=False)
perf['综合达成率'] = pd.to_numeric(s_rate, errors='coerce') / 100  # 变成 1.1235 这种

# 去掉没有综合达成率的行
perf = perf.dropna(subset=['综合达成率'])

# === 3. 按综合达成率重新排序 & 计算 rank ===
perf = perf.sort_values('综合达成率', ascending=False).reset_index(drop=True)
perf['rank'] = perf.index + 1

N = len(perf)

# === 4. 按方案3重新计算业绩等级 & 业绩分 ===
# 档位占比：S10%，A15%，B35%，C25%，D15%
nS = round(N * 0.10)
nA = round(N * 0.15)
nB = round(N * 0.35)
nC = round(N * 0.25)
nD = max(0, N - (nS + nA + nB + nC))  # 剩余给 D

def level_and_score(r):
    if r <= nS:
        return 'S', 60
    elif r <= nS + nA:
        return 'A', 50
    elif r <= nS + nA + nB:
        return 'B', 40
    elif r <= nS + nA + nB + nC:
        return 'C', 30
    else:
        return 'D', 20

levels = perf['rank'].apply(level_and_score)
perf['业绩等级'] = levels.apply(lambda x: x[0])
perf['业绩分']   = levels.apply(lambda x: x[1])

# === 5. 合并出勤数据 ===
df = perf.merge(att, on='agent', how='left')

# 没出现在出勤表的，缺勤次数视为 0，出勤分暂时填 0
df['缺勤次数'] = df['缺勤次数'].fillna(0).astype(int)
df['出勤分'] = df['出勤分'].fillna(0)

# === 6. 根据缺勤次数封顶业绩等级 ===
def cap_level(row):
    lvl = row['业绩等级']
    n_abs = row['缺勤次数']
    
    # 0 次缺勤：不限制
    if n_abs == 0:
        return lvl
    # 1 次缺勤：最高 A
    elif n_abs == 1:
        if lvl == 'S':
            return 'A'
        else:
            return lvl
    # 2 次及以上：最高 B
    else:
        if lvl in ['S', 'A']:
            return 'B'
        else:
            return lvl

df['业绩等级_出勤封顶后'] = df.apply(cap_level, axis=1)

# 根据新等级映射新业绩分
score_map = {
    'S': 60,
    'A': 50,
    'B': 40,
    'C': 30,
    'D': 20
}
df['业绩分_出勤封顶后'] = df['业绩等级_出勤封顶后'].map(score_map)

# === 7. 再次用百分比形式输出综合达成率（方便看） ===
df['综合达成率_百分比'] = (df['综合达成率'] * 100).round(2).astype(str) + '%'

# === 8. 输出结果 ===
result_cols = [
    'agent',
    '综合达成率_百分比',
    'rank',
    '业绩等级',             # 出勤封顶前
    '业绩分',               # 出勤封顶前
    '缺勤次数',
    '出勤分',
    '业绩等级_出勤封顶后',   # 出勤封顶后
    '业绩分_出勤封顶后'      # 出勤封顶后
]
result = df[result_cols]

out_file = os.path.join(desktop, "业绩+出勤_含出勤封顶.xlsx")
result.to_excel(out_file, index=False)

print("✅ 已根据缺勤次数封顶业绩等级，结果保存到：", out_file)
print(result.head())