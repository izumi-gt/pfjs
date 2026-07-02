# -*- coding: utf-8 -*-
r"""
440社の出力済みCSV(fact_financial / dim_account_local)から、
fact主キー重複を「様式別 × parent種別」で横断集計する。
parent種別: 空(parent='') / 擬似親(@LOC:) / 通常(幹コード or Lコード) / BS集計配下(-T)

再scanは不要。既存の out_csv を読むだけ。
新チャットへの「parent空問題 構造解決」設計材料を作る。

使い方:
  python3 aggregate_pk_dups.py out_csv
出力:
  画面サマリ + pk_dup_breakdown.csv（様式×parent種別×法人数の内訳）
"""
import sys, os, csv, glob
from collections import defaultdict, Counter

out_root = sys.argv[1] if len(sys.argv) > 1 else "out_csv"

# 集計器
# (様式, parent種別) -> 重複キー総数
form_kind_dups = Counter()
# (様式, parent種別) -> set(法人)
form_kind_corps = defaultdict(set)
# 代表科目名（parent種別ごと）
sample_names = defaultdict(Counter)
# 様式別 重複総数
form_dups = Counter()

n_corp = 0
corp_with_dup = set()

for corp_dir in sorted(glob.glob(os.path.join(out_root, "*"))):
    if not os.path.isdir(corp_dir):
        continue
    corp_no = os.path.basename(corp_dir)
    fact_f = glob.glob(os.path.join(corp_dir, "*fact_financial.csv"))
    local_f = glob.glob(os.path.join(corp_dir, "*dim_account_local.csv"))
    if not fact_f:
        continue
    n_corp += 1

    # local: code -> (parent, name, depth)
    loc_info = {}
    if local_f:
        with open(local_f[0], encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f); next(r, None)
            for row in r:
                if len(row) >= 5:
                    loc_info[row[0]] = (row[2], row[3], row[4])  # parent,name,depth
    # global(幹) code -> name も読む（科目コードが幹そのものの重複を識別するため）
    glob_f = glob.glob(os.path.join(corp_dir, "*dim_account_global.csv"))
    glob_name = {}
    if glob_f:
        with open(glob_f[0], encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f); next(r, None)
            for row in r:
                if len(row) >= 5:
                    glob_name[row[0]] = row[4]  # 正規名

    # fact主キー重複を集計
    pk = defaultdict(int)
    with open(fact_f[0], encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f); next(r, None)
        for row in r:
            if len(row) < 6:
                continue
            key = (row[2], row[3], row[4], row[5])  # 様式,科目,区分,metric
            pk[key] += 1

    for (form, code, seg, metric), cnt in pk.items():
        if cnt <= 1:
            continue
        corp_with_dup.add(corp_no)
        if code in loc_info:
            parent, name, depth = loc_info[code]
            if parent == "" or parent is None:
                kind = "parent空"
            elif str(parent).startswith("@LOC:"):
                kind = "擬似親@LOC"
            elif str(parent).endswith("-T"):
                kind = "集計行配下(-T)"
            else:
                kind = "通常parent(local)"
        elif code in glob_name:
            # 科目コードが幹(global)そのもの = 大区分/中区分が直接emitされ重複
            name = glob_name[code]
            kind = "幹コード直emit"
        else:
            name = "?"
            kind = "未登録(要調査)"
        form_kind_dups[(form, kind)] += 1
        form_kind_corps[(form, kind)].add(corp_no)
        form_dups[form] += 1
        sample_names[(form, kind)][name] += 1

print(f"=== 集計対象: {n_corp} 法人 / 重複ありは {len(corp_with_dup)} 法人 ===\n")

print("--- 様式別 重複キー総数 ---")
for form, c in form_dups.most_common():
    print(f"  {form}: {c}")

print("\n--- 様式 × parent種別 内訳（重複キー数 / 法人数）---")
print(f"  {'様式':<8}{'parent種別':<16}{'重複数':>8}{'法人数':>7}  代表科目")
for (form, kind), c in sorted(form_kind_dups.items(), key=lambda x: -x[1]):
    ncorp = len(form_kind_corps[(form, kind)])
    rep = sample_names[(form, kind)].most_common(2)
    reps = ", ".join(f"{n}({m})" for n, m in rep)
    print(f"  {form:<8}{kind:<16}{c:>8}{ncorp:>7}  {reps}")

# CSV出力
with open("pk_dup_breakdown.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["様式", "parent種別", "重複キー数", "法人数", "代表科目Top3"])
    for (form, kind), c in sorted(form_kind_dups.items(), key=lambda x: -x[1]):
        ncorp = len(form_kind_corps[(form, kind)])
        rep = sample_names[(form, kind)].most_common(3)
        reps = " | ".join(f"{n}×{m}" for n, m in rep)
        w.writerow([form, kind, c, ncorp, reps])

print("\n== pk_dup_breakdown.csv を出力 ==")
