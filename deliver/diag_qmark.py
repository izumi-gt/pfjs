# -*- coding: utf-8 -*-
r"""
pk重複で name='?' と出た正体を切り分ける。
'?' は集計時 dim_account_local にコードが無かった印。
 → factに乗るが local未登録のコードか / localにあるが名前が空か を確認。

使い方:
  python3 diag_qmark.py out_csv 3240005007286
  （第2引数省略時は重複ありの全社をざっと走査して件数だけ出す）
"""
import sys, os, csv, glob
from collections import defaultdict, Counter

out_root = sys.argv[1] if len(sys.argv) > 1 else "out_csv"
target = sys.argv[2] if len(sys.argv) > 2 else None

def analyze(corp_dir):
    corp_no = os.path.basename(corp_dir)
    fact_f = glob.glob(os.path.join(corp_dir, "*fact_financial.csv"))
    local_f = glob.glob(os.path.join(corp_dir, "*dim_account_local.csv"))
    glob_f = glob.glob(os.path.join(corp_dir, "*dim_account_global.csv"))
    if not fact_f:
        return None
    local_codes = {}
    local_emptyname = set()
    if local_f:
        with open(local_f[0], encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f); next(r, None)
            for row in r:
                if len(row) >= 4:
                    local_codes[row[0]] = row[3]
                    if row[3] == "" or row[3] is None:
                        local_emptyname.add(row[0])
    global_codes = set()
    if glob_f:
        with open(glob_f[0], encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f); next(r, None)
            for row in r:
                if row:
                    global_codes.add(row[0])

    # fact主キー重複のうち、科目コードがlocalにもglobalにも無いもの
    pk = defaultdict(int)
    with open(fact_f[0], encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f); next(r, None)
        for row in r:
            if len(row) < 6: continue
            pk[(row[2], row[3], row[4], row[5])] += 1
    dup_codes = Counter()
    for (form, code, seg, metric), cnt in pk.items():
        if cnt > 1:
            dup_codes[code] += 1
    not_in_local = [c for c in dup_codes if c not in local_codes and c not in global_codes]
    in_local_empty = [c for c in dup_codes if c in local_emptyname]
    return corp_no, len(dup_codes), not_in_local, in_local_empty, local_codes

if target:
    corp_dir = None
    for d in glob.glob(os.path.join(out_root, "*")):
        if os.path.basename(d) == target:
            corp_dir = d; break
    res = analyze(corp_dir)
    corp_no, ndup, not_in_local, in_local_empty, local_codes = res
    print(f"=== {corp_no} ===")
    print(f"重複科目コード種類数: {ndup}")
    print(f"factにあるがlocal/global未登録のコード: {len(not_in_local)}")
    for c in not_in_local[:20]:
        print(f"   未登録: {c}")
    print(f"localにあるが名前が空のコード: {len(in_local_empty)}")
    for c in in_local_empty[:20]:
        print(f"   空名: {c} (parent/name要確認)")
else:
    total_notreg = 0
    total_empty = 0
    worst = []
    for d in sorted(glob.glob(os.path.join(out_root, "*"))):
        if not os.path.isdir(d): continue
        res = analyze(d)
        if not res: continue
        corp_no, ndup, not_in_local, in_local_empty, _ = res
        total_notreg += len(not_in_local)
        total_empty += len(in_local_empty)
        if not_in_local or in_local_empty:
            worst.append((corp_no, len(not_in_local), len(in_local_empty)))
    print(f"=== 全社サマリ ===")
    print(f"未登録コード総数: {total_notreg}")
    print(f"空名コード総数: {total_empty}")
    print("\n--- 該当社 Top20（未登録, 空名）---")
    for corp_no, a, b in sorted(worst, key=lambda x: -(x[1]+x[2]))[:20]:
        print(f"  {corp_no}: 未登録={a} 空名={b}")
