import os
import numpy as np
import pandas as pd
from scipy.optimize import linprog

bd = r'D:\桌面\C题\附件'
od = r'D:\桌面\C题\结果'
os.makedirs(od, exist_ok=True)

h = 1.0 / 6.0
n = 144
pm = 5000.0 * h
lo = 1200.0
hi = 10800.0
ef = 0.90
s0 = 6000.0

a1 = pd.read_excel(os.path.join(bd, '附件1.xlsx'))
pr = a1['电价'].values.astype(float)
ld = a1['小区负载'].values.astype(float)
pv = a1['光伏发电预测功率'].values.astype(float)
el1 = ld * h
eg1 = pv * h

a2 = pd.read_excel(os.path.join(bd, '附件2.xlsx'), sheet_name='小区负载')
a3 = pd.read_excel(os.path.join(bd, '附件2.xlsx'), sheet_name='光伏发电实际功率')
la = a2.iloc[:, 1:].values.astype(float)
pa = a3.iloc[:, 1:].values.astype(float)

a4 = pd.read_excel(os.path.join(bd, '附件3.xlsx'))
a4['日期'] = a4['日期'].ffill()
fs = a4.iloc[:, 2:].values.astype(float).reshape(365, 4, 24)

a5 = pd.read_excel(os.path.join(bd, '附件4.xlsx'))
pc = a5.iloc[:, 1:].values.astype(float)

def eh(w):
    return np.repeat(w, 6)

g0 = np.array([eh(fs[d, 0, :]) for d in range(365)])
gr = np.empty((365, n))
for d in range(365):
    hr = np.array([fs[d, k // 6, k % 6] for k in range(24)])
    gr[d] = eh(hr)

el = la * h
ea = pa * h
e0 = g0 * h
er = gr * h

def sd(eg, el2, pr2, s, eq=False):
    B, C, D, S = 0, n, 2 * n, 3 * n
    nv = 3 * n + (n + 1)
    co = np.zeros(nv)
    co[B:B + n] = pr2
    lb = np.zeros(nv); ub = np.full(nv, np.inf)
    ub[C:C + n] = pm
    ub[D:D + n] = pm
    lb[S:S + n + 1] = lo; ub[S:S + n + 1] = hi
    lb[S] = ub[S] = s
    au, bu, ae, be = [], [], [], []
    for t in range(n):
        r = np.zeros(nv); r[B + t] = -1; r[D + t] = -1; r[C + t] = 1
        au.append(r); bu.append(eg[t] - el2[t])
    for t in range(n):
        r = np.zeros(nv)
        r[S + t + 1] = 1; r[S + t] = -1; r[C + t] = -ef; r[D + t] = 1
        ae.append(r); be.append(0.0)
    if eq:
        r = np.zeros(nv); r[S] = 1; r[S + n] = -1
        ae.append(r); be.append(0.0)
    res = linprog(co, A_ub=np.array(au), b_ub=np.array(bu),
                  A_eq=np.array(ae), b_eq=np.array(be),
                  bounds=list(zip(lb, ub)), method='highs')
    if not res.success:
        raise RuntimeError('LP fail: ' + res.message)
    x = res.x
    return x[B:B + n], x[C:C + n], x[D:D + n], x[S:S + n + 1], res.fun

def st(eg, el2, pr2, s, bf):
    B, C, D, S = 0, n, 2 * n, 3 * n
    E = 4 * n + 1
    nv = 4 * n + (n + 1)
    co = np.zeros(nv)
    co[E:E + n] = 5.0 * pr2
    co[C:C + n] = 1e-6
    lb = np.zeros(nv); ub = np.full(nv, np.inf)
    ub[C:C + n] = pm; ub[D:D + n] = pm
    lb[S:S + n + 1] = lo; ub[S:S + n + 1] = hi
    lb[S] = ub[S] = s
    au, bu, ae, be = [], [], [], []
    for t in range(n):
        r = np.zeros(nv)
        r[D + t] = -1; r[E + t] = -1; r[C + t] = 1
        au.append(r); bu.append(eg[t] + bf[t] - el2[t])
    for t in range(n):
        r = np.zeros(nv)
        r[S + t + 1] = 1; r[S + t] = -1; r[C + t] = -ef; r[D + t] = 1
        ae.append(r); be.append(0.0)
    res = linprog(co, A_ub=np.array(au), b_ub=np.array(bu),
                  A_eq=np.array(ae), b_eq=np.array(be),
                  bounds=list(zip(lb, ub)), method='highs')
    if not res.success:
        raise RuntimeError('RT LP fail: ' + res.message)
    x = res.x
    return x[E:E + n], x[C:C + n], x[D:D + n], x[S:S + n + 1], res.fun

b1, c1, d1, s1, ct1 = sd(eg1, el1, pr, s0, eq=True)
print('P1 buy %.2f cost %.2f' % (b1.sum(), ct1))
print('P1 SOC0 %.2f SOC144 %.2f' % (s1[0], s1[144]))
print('P1 chg %.2f dis %.2f' % (c1.sum(), d1.sum()))

nd = 365
b2 = np.zeros((nd, n)); c2 = np.zeros((nd, n)); d2 = np.zeros((nd, n))
s2 = np.zeros((nd, n + 1))
s = s0
ct2 = 0.0
for d in range(nd):
    b, c, dd, ss, ct = sd(ea[d], el[d], pr, s)
    b2[d] = b; c2[d] = c; d2[d] = dd; s2[d] = ss
    s = ss[n]
    ct2 += ct
print('P2 cost %.2f' % ct2)
print('P2 SOC end %.2f' % s)

bp3 = np.zeros((nd, n)); ba3 = np.zeros((nd, n))
c3 = np.zeros((nd, n)); d3 = np.zeros((nd, n))
e3 = np.zeros((nd, n)); s3 = np.zeros((nd, n + 1))
s = s0
cp = ca = ce = 0.0
for d in range(nd):
    bpa, _, _, _, _ = sd(e0[d], el[d], pr, s)
    baa, _, _, _, _ = sd(er[d], el[d], pr, s)
    mn = np.minimum(bpa, baa)
    ov = np.clip(bpa - baa, 0, None)
    un = np.clip(baa - bpa, 0, None)
    cp += float(np.sum(pr * mn))
    ca += float(np.sum(0.5 * pr * ov + 1.5 * pr * un))
    e, cc, dd, ss, cem = st(ea[d], el[d], pr, s, baa)
    bp3[d] = bpa; ba3[d] = baa; e3[d] = e
    c3[d] = cc; d3[d] = dd; s3[d] = ss
    s = ss[n]
    ce += cem
print('P3 plan %.2f adj %.2f em %.2f total %.2f' % (cp, ca, ce, cp + ca + ce))
print('P3 em energy %.2f' % e3.sum())

b42 = np.zeros((nd, n)); c42 = np.zeros((nd, n)); d42 = np.zeros((nd, n))
s42 = np.zeros((nd, n + 1))
s = s0; ct42 = 0.0
for d in range(nd):
    b, c, dd, ss, ct = sd(ea[d], el[d], pc[d], s)
    b42[d] = b; c42[d] = c; d42[d] = dd; s42[d] = ss
    s = ss[n]; ct42 += ct

bp43 = np.zeros((nd, n)); ba43 = np.zeros((nd, n))
c43 = np.zeros((nd, n)); d43 = np.zeros((nd, n)); e43 = np.zeros((nd, n))
s43 = np.zeros((nd, n + 1))
s = s0; cp4 = ca4 = ce4 = 0.0
for d in range(nd):
    pd = pc[d]
    bpa, _, _, _, _ = sd(e0[d], el[d], pd, s)
    baa, _, _, _, _ = sd(er[d], el[d], pd, s)
    mn = np.minimum(bpa, baa); ov = np.clip(bpa - baa, 0, None); un = np.clip(baa - bpa, 0, None)
    cp4 += float(np.sum(pd * mn))
    ca4 += float(np.sum(0.5 * pd * ov + 1.5 * pd * un))
    e, cc, dd, ss, cem = st(ea[d], el[d], pd, s, baa)
    bp43[d] = bpa; ba43[d] = baa; e43[d] = e
    c43[d] = cc; d43[d] = dd; s43[d] = ss
    s = ss[n]; ce4 += cem
print('P4 4-2 cost %.2f' % ct42)
print('P4 4-3 plan %.2f adj %.2f em %.2f total %.2f' % (cp4, ca4, ce4, cp4 + ca4 + ce4))

np.savez(os.path.join(od, '_data.npz'),
         b1=b1, c1=c1, d1=d1, s1=s1,
         b2=b2, c2=c2, d2=d2, s2=s2,
         bp3=bp3, ba3=ba3, c3=c3, d3=d3, e3=e3, s3=s3,
         b42=b42, c42=c42, d42=d42, s42=s42,
         bp43=bp43, ba43=ba43, c43=c43, d43=d43, e43=e43, s43=s43)
print('DONE')
