import math

# === INPUT: Daily due cases ===
due = {
    '分期新客': [240, 271, 262, 254, 239, 244, 260],
    '分期老客': [182, 246, 195, 205, 205, 225, 229],
    '单期新客': [65, 72, 85, 115, 117, 128, 143],
    '单期老客': [73, 98, 100, 88, 88, 103, 98],
}

avg = {k: sum(v)/len(v) for k, v in due.items()}
print('=== 未来7天日均到期 ===')
for k, v in avg.items():
    print(f'  {k}: {v:.1f}')

# === Recovery rates by stage and customer type ===
# pre_d2 and d2 use generic new/old; d0-d2 use segment-specific
pre_d2_rate = {'新客': 0.15, '老客': 0.19}
d2_rate = {'新客': 0.0659, '老客': 0.0969}
d1_ai_rate = {'新客': 0.1773, '老客': 0.2472}
d1_man_rate = {'新客': 0, '老客': 0}
d0_rate = {'分期新客': 0.365, '分期老客': 0.5692, '单期新客': 0.3667, '单期老客': 0.5879}
d1_rate = {'分期新客': 0.062, '分期老客': 0.1539, '单期新客': 0.0632, '单期老客': 0.1447}
s1_rate = {'分期新客': 0.0251, '分期老客': 0.0398, '单期新客': 0.0229, '单期老客': 0.0155}
s2_rate = {'分期新客': 0.0154, '分期老客': 0.0035, '单期新客': 0.0065, '单期老客': 0.0076}

AI_ratio = 1.0
MAN_ratio = 0.0
second_alloc_new = 0.2
second_alloc_old = 0.3
cap = {'D-1': 40, 'D0': 30, 'D1': 30, 'S1': 80, 'S2': 100}

# === WATERFALL per segment ===
segments = ['分期新客', '分期老客', '单期新客', '单期老客']
results = {}

for seg in segments:
    cust = '新客' if '新客' in seg else '老客'
    a = avg[seg]

    pre_out = a * (1 - pre_d2_rate[cust])
    d2_out = pre_out * (1 - d2_rate[cust])
    d1_ai_out = d2_out * AI_ratio * (1 - d1_ai_rate[cust])
    d1_man_out = d2_out * MAN_ratio * (1 - d1_man_rate[cust])
    d0_out = (d1_ai_out + d1_man_out) * (1 - d0_rate[seg])
    d1_out = d0_out * (1 - d1_rate[seg])
    s1_out = d1_out * (1 - s1_rate[seg])
    s2_out = s1_out * (1 - s2_rate[seg])

    results[seg] = {
        'avg': a, 'pre_out': pre_out, 'd2_out': d2_out,
        'd1_ai_out': d1_ai_out, 'd1_man_out': d1_man_out,
        'd0_out': d0_out, 'd1_out': d1_out,
        's1_out': s1_out, 's2_out': s2_out,
    }

# === STAFFING ===
print('\n' + '='*70)
print('各阶段人力预估')
print('='*70)

fn = results['分期新客']
fo = results['分期老客']
dn = results['单期新客']
do = results['单期老客']

def print_stage(label, inflow, cap_val):
    need = max(1, math.ceil(inflow / cap_val)) if inflow > 0 else 0
    est = math.ceil(need / 7 + need) if need > 0 else 0
    print(f'  {label}: 预估入催={inflow:.0f}案  需要={need}人  预估(含缓冲)={est}人  人均={cap_val}案/人')

print('\n【新客组别】')
d1_new_in = fn['d2_out'] * MAN_ratio + dn['d2_out'] * MAN_ratio
print_stage('D-1', d1_new_in, cap['D-1'])
d0_new_in = (fn['d1_ai_out'] + fn['d1_man_out'] + dn['d1_ai_out'] + dn['d1_man_out']) * (1 - second_alloc_new)
print_stage('D0 ', d0_new_in, cap['D0'])
d1_new_total = fn['d0_out'] + dn['d0_out']
print_stage('D1 ', d1_new_total, cap['D1'])

print('\n【老客组别】')
d1_old_in = fo['d2_out'] * MAN_ratio + do['d2_out'] * MAN_ratio
print_stage('D-1', d1_old_in, cap['D-1'])
d0_old_in = (fo['d1_ai_out'] + fo['d1_man_out'] + do['d1_ai_out'] + do['d1_man_out']) * (1 - second_alloc_old)
print_stage('D0 ', d0_old_in, cap['D0'])
d1_old_total = fo['d0_out'] + do['d0_out']
print_stage('D1 ', d1_old_total, cap['D1'])

print('\n【S1/S2 全量】')
s1_in = (fn['d1_out'] + fo['d1_out'] + dn['d1_out'] + do['d1_out']) * 2
print_stage('S1 ', s1_in, cap['S1'])
s2_in = (fn['s1_out'] + fo['s1_out'] + dn['s1_out'] + do['s1_out']) * 3
print_stage('S2 ', s2_in, cap['S2'])

# === EMPLOYEE SUMMARY ===
print('\n' + '='*70)
print('当前在职员工分布')
print('='*70)
employees = {
    ('新客', 'D0'): ['Edith Perez', 'Sandra Contreras', 'Karime Resendiz', 'Luz Lopez',
                      'Jovanna Santoyo', 'Gabriela Jimenez', 'Perla Sanchez',
                      'Jorge Reyes', 'Karlos Alejandro'],
    ('新客', 'D1'): ['Edith Perez', 'Juan Ortega', 'Juan Guzman',
                      'Victor Lerma', 'Uriel Bautista'],
    ('新客', 'S1'): ['Julian Garcia'],
    ('新客', 'S2'): ['David Osorio'],
    ('老客', 'D0'): ['Yelitza Ruiz', 'Viviana Amaro', 'Jessica Robles', 'Jazmin Rufino',
                      'Francisco Moncada', 'Lourdes Nunez', 'Laura Bautista'],
    ('老客', 'D1'): ['Miguel Ayala', 'Maria de los Angeles', 'Jose Cruz', 'Abraham Miranda'],
    ('老客', 'S1'): ['Miguel Ayala', 'Brandom Molina', 'Adriana Pacheco',
                      'Adolfo Monzon', 'Jose Cruz'],
    ('老客', 'S2'): ['Owen Angeles', 'Jonathan Martinez', 'Dante Hernandez', 'Adolfo Monzon'],
}

for (cust, stage), names in employees.items():
    print(f'  {cust} {stage}: {len(names)}人 - {", ".join(names[:4])}{"..." if len(names)>4 else ""}')
