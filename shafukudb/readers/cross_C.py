"""(C)突合: Σ(1-4全拠点決算B) == Σ_事業区分(1-3合計列)。
1-4側 = reader_1_4のHIT科目(決算B) + anchor_readerのアンカー6行。reader本体は無改変。"""
import reader_1_4_cf as r4
import reader_1_3_cf as r3
import anchor_reader as ar
from collections import defaultdict

def side_1_4(pdf14):
    s = defaultdict(int); name = {}
    rows = r4.process_pdf(pdf14)
    for x in rows:
        if x['status']=='HIT' and x['code']:
            v = r4.parse_amount(x['決算B'])
            if v is not None:
                s[x['code']] += v; name[x['code']] = x['name']
    for a in ar.read_anchor_rows(pdf14):   # アンカー6行(罫線グリッド由来)
        if a['決算B'] is not None:
            s[a['code']] += a['決算B']; name.setdefault(a['code'], a['name'])
    return s, name

def side_1_3(pdf13):
    s = defaultdict(int); name = {}
    rows, cats = r3.process_pdf(pdf13)
    for x in rows:
        if x['列種別']=='合計' and x['status']=='HIT' and x['code']:
            s[x['code']] += (x['金額'] or 0); name[x['code']] = x['name']
    return s, name, cats

def run(corp, pdf14, pdf13):
    s14, n14 = side_1_4(pdf14)
    s13, n13, cats = side_1_3(pdf13)
    codes = sorted(s13.keys())
    ok=[]; ng=[]; zero_absent=[]; nonzero_absent=[]
    for c in codes:
        v13 = s13[c]; v14 = s14.get(c)
        if v14 is None:
            (zero_absent if v13==0 else nonzero_absent).append((c, n13[c], v13))
        elif v13==v14:
            ok.append(c)
        else:
            ng.append((c, n13[c], v13, v14, v13-v14))
    # 1-4にあるが1-3合計に無い(非ゼロ)科目 = 内訳表に出ない明細。参考表示
    only14 = [(c, n14.get(c,''), s14[c]) for c in s14 if c not in s13 and s14[c]!=0]
    print(f'========== {corp} (C)突合 ==========')
    print(f'事業区分(1-3): {list(cats.keys())}')
    print(f'1-3合計列 code数={len(codes)} / 1-4 code数(HIT+anchor,金額あり)={len([k for k in s14 if s14[k]!=0])}')
    print(f'[一致OK]={len(ok)}  [不一致NG]={len(ng)}  [1-3非ゼロだが1-4欠落]={len(nonzero_absent)}  [両者ゼロ相当]={len(zero_absent)}')
    if ng:
        print('--- ★NG(code, name, 1-3合計, Σ1-4, 差) ---')
        for x in ng: print(f'   {x[0]} {x[1][:22]} 1-3={x[2]:,} 1-4={x[3]:,} 差={x[4]:,}')
    if nonzero_absent:
        print('--- ★1-3非ゼロだが1-4に欠落 ---')
        for x in nonzero_absent: print(f'   {x[0]} {x[1][:22]} 1-3={x[2]:,}')
    print(f'(参考)1-4のみ非ゼロ(内訳表に出ない明細) {len(only14)}件')
    resid = len(ng)+len(nonzero_absent)
    print(f'>>> {corp} (C) 未解決NG = {resid}')
    return resid

if __name__ == '__main__':
    run('華野福祉会', 'hanano_1-4.pdf', 'hanano_1-3.pdf')
