"""(A)(B)恒等式 最終版: 内部取引(事業区分間/拠点区分間)を、
収入=支出のnet0であることを確認した上で、収入計/支出計から明示的に控除して比較。
"""
import reader_1_1_cf as r1, reader_1_2_cf as r2, reader_1_3_cf as r3
from collections import defaultdict

# (A) 1-1 vs 1-2法人合計: 事業区分間(法人全体では常にnet0のはず)
NAIBU_TORIHIKI_A = [
    ('CF-05-01-008-000-000','CF-05-03-007-000-000'),  # 長期借入金収入/長期貸付金支出
    ('CF-05-01-012-000-000','CF-05-03-011-000-000'),  # 長期貸付金回収収入/長期借入金返済支出
    ('CF-05-01-016-000-000','CF-05-03-015-000-000'),  # 繰入金収入/繰入金支出
]
SHUNYUKEI = 'CF-05-02-000-000-000'   # その他の活動収入計(7)
SHISHUTSUKEI = 'CF-05-04-000-000-000'  # その他の活動支出計(8)

# (B) 1-3事業区分合計 vs 1-2合計: 拠点区分間(事業区分内では常にnet0のはず)
NAIBU_TORIHIKI_B = [
    ('CF-05-01-009-000-000','CF-05-03-008-000-000'),
    ('CF-05-01-013-000-000','CF-05-03-012-000-000'),
    ('CF-05-01-017-000-000','CF-05-03-016-000-000'),
]

def gather(p11, p12, p13):
    lr, rows, matched = r1.process_pdf(p11)
    s11 = defaultdict(int)
    for x in lr:
        if x['列名']=='決算' and x['code']:
            v = r1.parse_amount(x['金額']) if isinstance(x['金額'], str) else x['金額']
            s11[x['code']] += (v or 0)

    res12 = r2.process_pdf(p12)
    s12_hojin = defaultdict(int); s12_goukei = defaultdict(int)
    for x in res12:
        if x['code']:
            v = x['amounts'].get('法人合計'); v = r2.parse_amount(v) if isinstance(v,str) else v
            s12_hojin[x['code']] += (v or 0)
            v2 = x['amounts'].get('合計'); v2 = r2.parse_amount(v2) if isinstance(v2,str) else v2
            s12_goukei[x['code']] += (v2 or 0)

    rows13, cats = r3.process_pdf(p13)
    s13 = defaultdict(int)
    for x in rows13:
        if x['列種別']=='事業区分合計' and x['code']:
            s13[x['code']] += (x['金額'] or 0)

    return dict(s11), dict(s12_hojin), dict(s12_goukei), dict(s13)

def verify(sA, sB, naibu_list, label):
    """sAの収入計/支出計から内部取引を控除し、sBと比較。それ以外は厳格一致。"""
    net_ok = True
    shunyu_total = 0; shishutsu_total = 0
    for shunyu_c, shishutsu_c in naibu_list:
        a, b = sA.get(shunyu_c,0), sA.get(shishutsu_c,0)
        if a != b:
            net_ok = False
            print(f'    ★内部取引が収入≠支出: {shunyu_c}={a} {shishutsu_c}={b}')
        shunyu_total += a; shishutsu_total += b

    adj_shunyukei = sA.get(SHUNYUKEI,0) - shunyu_total
    adj_shishutsukei = sA.get(SHISHUTSUKEI,0) - shishutsu_total
    b_shunyu = sB.get(SHUNYUKEI,0); b_shishutsu = sB.get(SHISHUTSUKEI,0)
    ok_shunyu = adj_shunyukei == b_shunyu
    ok_shishutsu = adj_shishutsukei == b_shishutsu

    exclude = set()
    for shunyu_c, shishutsu_c in naibu_list:
        exclude.add(shunyu_c); exclude.add(shishutsu_c)
    exclude.add(SHUNYUKEI); exclude.add(SHISHUTSUKEI)

    codes_union = sorted((set(sA) | set(sB)) - exclude)
    ng_other = [c for c in codes_union if sA.get(c,0) != sB.get(c,0)]

    tag1 = 'OK' if net_ok else 'NG'
    tag2 = 'OK' if ok_shunyu else 'NG'
    tag3 = 'OK' if ok_shishutsu else 'NG'
    print(f'  [{label}] 内部取引net0:{tag1}(収入{shunyu_total}/支出{shishutsu_total})  控除後収入計:{tag2}  控除後支出計:{tag3}  他{len(codes_union)}件のNG={len(ng_other)}')
    if ng_other: print(f'      NG codes: {ng_other}')

    return (0 if net_ok else 1) + (0 if ok_shunyu else 1) + (0 if ok_shishutsu else 1) + len(ng_other)

corps = [
    ('華野','hanano_1-1.pdf','hanano_1-2.pdf','hanano_1-3.pdf'),
    ('広島','hiroshima_1-1.pdf','hiroshima_1-2.pdf','hiroshima_1-3.pdf'),
    ('あと会','atokai_1-1.pdf','atokai_1-2.pdf','atokai_1-3.pdf'),
    ('エフアイジイ','fig_1-1.pdf','fig_1-2.pdf','fig_1-3.pdf'),
]

all_ok = True
for corp,p11,p12,p13 in corps:
    print(f'========== {corp} ==========')
    s11,s12h,s12g,s13 = gather(p11,p12,p13)
    ng_a = verify(s11, s12h, NAIBU_TORIHIKI_A, '(A)1-1 vs 1-2法人合計')
    ng_b = verify(s13, s12g, NAIBU_TORIHIKI_B, '(B)1-3事業区分合計 vs 1-2合計')
    if ng_a or ng_b: all_ok = False
    print()

print('全法人・(A)(B)ともNG=0:', all_ok)
