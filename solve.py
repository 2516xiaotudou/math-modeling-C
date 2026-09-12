# -*- coding: utf-8 -*-
"""
2026 高教社杯 C题  微网与外部电网电力调控策略  —— 求解器
问题1~4 全解，输出 result1~result4-2/4-3.xlsx
"""
import os
import numpy as np
import pandas as pd
from scipy.optimize import linprog

BASE = r'D:\桌面\C题\附件'
OUTDIR = r'D:\桌面\C题\结果'
os.makedirs(OUTDIR, exist_ok=True)

DT = 1.0 / 6.0          # 10分钟 = 1/6 小时
N = 144                 # 每天时段数
PMAX = 5000.0 * DT      # 每时段最大充/放能量 kWh = 833.333
SOC_MIN = 1200.0
SOC_MAX = 10800.0
ETA = 0.90              # 充电效率
SOC0_INIT = 6000.0      # 2025-1-1 0:00 储电量

# ---------------------------------------------------------------- 数据加载
df1 = pd.read_excel(os.path.join(BASE, '附件1.xlsx'))
p1 = df1['电价'].values.astype(float)                       # 144 单日电价
L1 = df1['小区负载'].values.astype(float)                    # 144 单日负载
G1 = df1['光伏发电预测功率'].values.astype(float)            # 144 单日光伏预测
E_L1 = L1 * DT
E_G1 = G1 * DT

df2l = pd.read_excel(os.path.join(BASE, '附件2.xlsx'), sheet_name='小区负载')
df2p = pd.read_excel(os.path.join(BASE, '附件2.xlsx'), sheet_name='光伏发电实际功率')
load_all = df2l.iloc[:, 1:].values.astype(float)            # (365,144) 实际负载 kW
pv_actual_all = df2p.iloc[:, 1:].values.astype(float)       # (365,144) 实际光伏 kW

df3 = pd.read_excel(os.path.join(BASE, '附件3.xlsx'))
df3['日期'] = df3['日期'].ffill()
fcst = df3.iloc[:, 2:].values.astype(float).reshape(365, 4, 24)   # (365,4,24) 小时级预报

df4 = pd.read_excel(os.path.join(BASE, '附件4.xlsx'))
price_all = df4.iloc[:, 1:].values.astype(float)            # (365,144) 波动电价

def expand_hourly(h24):
    """24小时级 -> 144个10分钟级(每小时内6段取同值)"""
    return np.repeat(h24, 6)

# 每天的光伏预报(0:00发布的整日预报)展开到144
G0_all = np.array([expand_hourly(fcst[d, 0, :]) for d in range(365)])          # (365,144)
# 每天"滚动最新预报": 0-6点用0点预报,6-12用6点,12-18用12点,18-24用18点
G_roll_all = np.empty((365, N))
for d in range(365):
    hourly = np.array([fcst[d, h // 6, h % 6] for h in range(24)])
    G_roll_all[d] = expand_hourly(hourly)

E_L_all = load_all * DT
E_G_actual_all = pv_actual_all * DT
E_G0_all = G0_all * DT
E_G_roll_all = G_roll_all * DT

# ---------------------------------------------------------------- LP 求解器
def solve_day(E_G, E_L, price, soc0, soc0_free_equal=False):
    """
    单日LP。变量: b[144]购电, c[144]充电, d[144]放电, SOC[145]。
    soc0_free_equal=True 表示 SOC0==SOC144 自由(问题1)；否则 SOC0 固定为 soc0。
    返回 b,c,d,SOC(数组) 和 目标值。
    """
    B, C, D, S = 0, N, 2 * N, 3 * N
    nv = 3 * N + (N + 1)
    cobj = np.zeros(nv)
    cobj[B:B + N] = price                     # min sum price*b

    lb = np.zeros(nv); ub = np.full(nv, np.inf)
    ub[C:C + N] = PMAX
    ub[D:D + N] = PMAX
    lb[S:S + N + 1] = SOC_MIN; ub[S:S + N + 1] = SOC_MAX
    if not soc0_free_equal:
        lb[S] = ub[S] = soc0

    A_ub, b_ub, A_eq, b_eq = [], [], [], []
    # 功率平衡: E_G + b + d - c >= E_L  ->  -b -d +c <= E_G - E_L
    for t in range(N):
        row = np.zeros(nv); row[B + t] = -1; row[D + t] = -1; row[C + t] = 1
        A_ub.append(row); b_ub.append(E_G[t] - E_L[t])
    # 储能动态: SOC[t+1] - SOC[t] - eta*c[t] + d[t] = 0
    for t in range(N):
        row = np.zeros(nv)
        row[S + t + 1] = 1; row[S + t] = -1; row[C + t] = -ETA; row[D + t] = 1
        A_eq.append(row); b_eq.append(0.0)
    if soc0_free_equal:
        row = np.zeros(nv); row[S] = 1; row[S + N] = -1
        A_eq.append(row); b_eq.append(0.0)

    res = linprog(cobj, A_ub=np.array(A_ub), b_ub=np.array(b_ub),
                  A_eq=np.array(A_eq), b_eq=np.array(b_eq),
                  bounds=list(zip(lb, ub)), method='highs')
    if not res.success:
        raise RuntimeError('LP失败: ' + res.message)
    x = res.x
    return x[B:B + N], x[C:C + N], x[D:D + N], x[S:S + N + 1], res.fun

def solve_day_real_time(E_G_actual, E_L, price, soc0, b_fixed):
    """实时段: 购电 b 已锁定为 b_fixed，实际光伏下用储能+紧急购电(5倍)补缺口。
    返回 e(紧急购电), c, d, SOC, 紧急费用。"""
    B, C, D, S = 0, N, 2 * N, 3 * N
    E = 4 * N + 1                  # 紧急购电变量 e[144] (避开 SOC[144] 占用的索引 4N)
    nv = 4 * N + (N + 1)
    cobj = np.zeros(nv)
    cobj[E:E + N] = 5.0 * price     # min sum 5*price*e
    cobj[C:C + N] = 1e-6           # 微小充电惩罚, 打破退化使充放电量唯一

    lb = np.zeros(nv); ub = np.full(nv, np.inf)
    ub[C:C + N] = PMAX; ub[D:D + N] = PMAX
    lb[S:S + N + 1] = SOC_MIN; ub[S:S + N + 1] = SOC_MAX
    lb[S] = ub[S] = soc0

    A_ub, b_ub, A_eq, b_eq = [], [], [], []
    # 平衡: E_G_actual + b_fixed + d + e - c >= E_L  -> -d -e +c <= E_G_actual + b_fixed - E_L
    for t in range(N):
        row = np.zeros(nv)
        row[D + t] = -1; row[E + t] = -1; row[C + t] = 1
        A_ub.append(row); b_ub.append(E_G_actual[t] + b_fixed[t] - E_L[t])
    for t in range(N):
        row = np.zeros(nv)
        row[S + t + 1] = 1; row[S + t] = -1; row[C + t] = -ETA; row[D + t] = 1
        A_eq.append(row); b_eq.append(0.0)
    res = linprog(cobj, A_ub=np.array(A_ub), b_ub=np.array(b_ub),
                  A_eq=np.array(A_eq), b_eq=np.array(b_eq),
                  bounds=list(zip(lb, ub)), method='highs')
    if not res.success:
        raise RuntimeError('实时LP失败: ' + res.message)
    x = res.x
    return x[E:E + N], x[C:C + N], x[D:D + N], x[S:S + N + 1], res.fun

# ---------------------------------------------------------------- 问题1
b1, c1, d1, SOC1, cost1 = solve_day(E_G1, E_L1, p1, 0.0, soc0_free_equal=True)
print('===== 问题1 =====')
print(f'全天购电量 {b1.sum():.2f} kWh, 全天购电费 {cost1:.2f} 元')
print(f'SOC0 {SOC1[0]:.2f}, SOC144 {SOC1[144]:.2f}')
print(f'总充电 {c1.sum():.2f} kWh, 总放电 {d1.sum():.2f} kWh')

# ---------------------------------------------------------------- 问题2
NDAYS = 365
b2_all = np.zeros((NDAYS, N)); c2_all = np.zeros((NDAYS, N)); d2_all = np.zeros((NDAYS, N))
SOC2_traj = np.zeros((NDAYS, N + 1))
soc = SOC0_INIT
cost2_total = 0.0
for d in range(NDAYS):
    b, c, dd, SOC, cost = solve_day(E_G_actual_all[d], E_L_all[d], p1, soc)
    b2_all[d] = b; c2_all[d] = c; d2_all[d] = dd; SOC2_traj[d] = SOC
    soc = SOC[N]
    cost2_total += cost
print('===== 问题2 =====')
print(f'全年(计划)购电费 {cost2_total:.2f} 元')
print(f'年终SOC {soc:.2f} kWh, 全年紧急购电=0')

# ---------------------------------------------------------------- 问题3
b_plan3 = np.zeros((NDAYS, N)); b_adj3 = np.zeros((NDAYS, N))
c3_all = np.zeros((NDAYS, N)); d3_all = np.zeros((NDAYS, N))
e3_all = np.zeros((NDAYS, N)); SOC3_traj = np.zeros((NDAYS, N + 1))
soc = SOC0_INIT
cost_plan3 = cost_adj3 = cost_emerg3 = 0.0
for d in range(NDAYS):
    p_day = p1
    # 0:00 计划(用0点预报)
    bp, _, _, _, _ = solve_day(E_G0_all[d], E_L_all[d], p_day, soc)
    # 调整(用滚动最新预报)
    ba, _, _, _, _ = solve_day(E_G_roll_all[d], E_L_all[d], p_day, soc)
    # 结算: min取正常价 + 少买50% + 多买1.5倍
    mn = np.minimum(bp, ba)
    over = np.clip(bp - ba, 0, None)     # 计划>调整(违约)
    under = np.clip(ba - bp, 0, None)    # 调整>计划(1.5倍)
    cost_plan3 += float(np.sum(p_day * mn))
    cost_adj3 += float(np.sum(0.5 * p_day * over + 1.5 * p_day * under))
    # 实时: 实际光伏下补缺口(5倍)
    e, cc, dd, SOC, c_emerg = solve_day_real_time(E_G_actual_all[d], E_L_all[d], p_day, soc, ba)
    b_plan3[d] = bp; b_adj3[d] = ba; e3_all[d] = e
    c3_all[d] = cc; d3_all[d] = dd; SOC3_traj[d] = SOC
    soc = SOC[N]
    cost_emerg3 += c_emerg
cost3_total = cost_plan3 + cost_adj3 + cost_emerg3
print('===== 问题3 =====')
print(f'计划购电费 {cost_plan3:.2f}, 调整费用 {cost_adj3:.2f}, 紧急 {cost_emerg3:.2f}, 合计 {cost3_total:.2f}')
print(f'全年紧急购电量 {e3_all.sum():.2f} kWh')

# ---------------------------------------------------------------- 问题4 (波动电价)
# 4-2: 对应问题2
b42_all = np.zeros((NDAYS, N)); c42_all = np.zeros((NDAYS, N)); d42_all = np.zeros((NDAYS, N))
SOC42 = np.zeros((NDAYS, N + 1))
soc = SOC0_INIT; cost42 = 0.0
for d in range(NDAYS):
    b, c, dd, SOC, cost = solve_day(E_G_actual_all[d], E_L_all[d], price_all[d], soc)
    b42_all[d] = b; c42_all[d] = c; d42_all[d] = dd; SOC42[d] = SOC
    soc = SOC[N]; cost42 += cost
# 4-3: 对应问题3
b_plan43 = np.zeros((NDAYS, N)); b_adj43 = np.zeros((NDAYS, N))
c43_all = np.zeros((NDAYS, N)); d43_all = np.zeros((NDAYS, N)); e43_all = np.zeros((NDAYS, N))
SOC43 = np.zeros((NDAYS, N + 1))
soc = SOC0_INIT; cp43 = ca43 = ce43 = 0.0
for d in range(NDAYS):
    p_day = price_all[d]
    bp, _, _, _, _ = solve_day(E_G0_all[d], E_L_all[d], p_day, soc)
    ba, _, _, _, _ = solve_day(E_G_roll_all[d], E_L_all[d], p_day, soc)
    mn = np.minimum(bp, ba); over = np.clip(bp - ba, 0, None); under = np.clip(ba - bp, 0, None)
    cp43 += float(np.sum(p_day * mn))
    ca43 += float(np.sum(0.5 * p_day * over + 1.5 * p_day * under))
    e, cc, dd, SOC, c_emerg = solve_day_real_time(E_G_actual_all[d], E_L_all[d], p_day, soc, ba)
    b_plan43[d] = bp; b_adj43[d] = ba; e43_all[d] = e
    c43_all[d] = cc; d43_all[d] = dd; SOC43[d] = SOC
    soc = SOC[N]; ce43 += c_emerg
print('===== 问题4 =====')
print(f'4-2(波动价·问题2)全年购电费 {cost42:.2f} 元')
print(f'4-3(波动价·问题3) 计划 {cp43:.2f} + 调整 {ca43:.2f} + 紧急 {ce43:.2f} = {cp43+ca43+ce43:.2f}')

# 保存中间结果供后续写文件使用
np.savez(os.path.join(OUTDIR, '_data.npz'),
         b1=b1, c1=c1, d1=d1, SOC1=SOC1,
         b2_all=b2_all, c2_all=c2_all, d2_all=d2_all, SOC2_traj=SOC2_traj,
         b_plan3=b_plan3, b_adj3=b_adj3, c3_all=c3_all, d3_all=d3_all, e3_all=e3_all, SOC3_traj=SOC3_traj,
         b42_all=b42_all, c42_all=c42_all, d42_all=d42_all, SOC42=SOC42,
         b_plan43=b_plan43, b_adj43=b_adj43, c43_all=c43_all, d43_all=d43_all, e43_all=e43_all, SOC43=SOC43)
print('=== 求解完成，中间数据已保存 ===')
