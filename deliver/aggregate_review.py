# -*- coding: utf-8 -*-
r"""
全法人の review_queue.csv と diag_held_unresolved.csv を横断集計し、
標準科目マスタに追加すべき候補を「出現法人数」順に出す。

ゴール: 2万社規模のDB化に向け、2社製マスタを実態に合わせて育てる材料を作る。

使い方（deliver直下で）:
  python3 aggregate_review.py out_csv

出力:
  - 画面: 大区分(parent空)候補 / 小科目候補 を法人数順
  - master_candidates.csv: 全候補を機械可読で（後でmasters.py反映用）
"""
import sys, os, csv, glob
from collections import defaultdict, Counter

out_root = sys.argv[1] if len(sys.argv) > 1 else "out_csv"

# (計算書, 正規化キー, 親コード, 収支区分, kind) -> set(法人番号)
review_corps = defaultdict(set)
review_rawnames = defaultdict(Counter)   # 同一キーの元科目名バリエーション
held_corps = defaultdict(set)
held_rawnames = defaultdict(Counter)

n_corp_dirs = 0
for corp_dir in sorted(glob.glob(os.path.join(out_root, "*"))):
    if not os.path.isdir(corp_dir):
        continue
    corp_no = os.path.basename(corp_dir)
    n_corp_dirs += 1

    # review_queue（実ファイル名は <法人番号>_review_queue.csv）
    rq_list = glob.glob(os.path.join(corp_dir, "*review_queue.csv"))
    rq = rq_list[0] if rq_list else None
    if rq:
        with open(rq, encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            header = next(r, None)
            for row in r:
                if len(row) < 11:
                    continue
                # cli_all は先頭に「タイプ」列を足して書く場合がある→末尾11列で吸収
                tail = row[-11:]
                kind, code, name, norm, parent, io, stmt, cno, form, page, raw = tail
                key = (stmt, norm, parent, io, kind)
                review_corps[key].add(corp_no)
                review_rawnames[key][raw] += 1

    # held（実ファイル名は <法人番号>_diag_held_unresolved.csv）
    hd_list = glob.glob(os.path.join(corp_dir, "*diag_held_unresolved.csv"))
    hd = hd_list[0] if hd_list else None
    if hd:
        with open(hd, encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            header = next(r, None)
            for row in r:
                if len(row) < 2:
                    continue
                typ = row[0]
                name = row[1] if len(row) > 1 else ""
                rest = "|".join(row[2:])
                key = (typ, name)
                held_corps[key].add(corp_no)
                held_rawnames[key][rest] += 1

print(f"=== 集計対象法人ディレクトリ: {n_corp_dirs} ===\n")

# --- 大区分候補（parent が空 = depth0大区分がマスタ未収載）---
big = [(k, corps) for k, corps in review_corps.items() if k[2] == "" and k[4] == "concept"]
big.sort(key=lambda kc: len(kc[1]), reverse=True)
print(f"--- 【最優先】大区分(parent空)候補: {len(big)} 種 ---")
print("  これは depth0 の大区分がマスタに無く落ちたサイン。主キー重複の主因。\n")
print(f"  {'法人数':>5}  {'計算書':<4}{'収支':<6} 正規化キー / 代表元名")
for (stmt, norm, parent, io, kind), corps in big[:60]:
    rep = review_rawnames[(stmt, norm, parent, io, kind)].most_common(1)
    rep = rep[0][0] if rep else norm
    print(f"  {len(corps):>5}  {stmt:<4}{io:<6} {norm}   ({rep})")

# --- 小科目候補（parentあり）---
small = [(k, corps) for k, corps in review_corps.items() if k[2] != "" and k[4] == "concept"]
small.sort(key=lambda kc: len(kc[1]), reverse=True)
print(f"\n--- 小科目候補(parentあり): {len(small)} 種（上位30）---")
print(f"  {'法人数':>5}  {'計算書':<4}{'収支':<6} 正規化キー  親={'親コード'}")
for (stmt, norm, parent, io, kind), corps in small[:30]:
    print(f"  {len(corps):>5}  {stmt:<4}{io:<6} {norm}  親={parent}")

# --- held（master未収載で取込できなかった＝値ありは要対応）---
held_sorted = sorted(held_corps.items(), key=lambda kc: len(kc[1]), reverse=True)
print(f"\n--- held(取込不可)科目: {len(held_sorted)} 種（上位30）---")
print(f"  {'法人数':>5}  タイプ / 科目名")
for (typ, name), corps in held_sorted[:30]:
    print(f"  {len(corps):>5}  {typ} / {name}")

# --- 機械可読CSV ---
with open("master_candidates.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["分類", "法人数", "計算書", "収支区分", "正規化キー",
                "親コード", "種別", "代表元科目名"])
    for (stmt, norm, parent, io, kind), corps in big:
        rep = review_rawnames[(stmt, norm, parent, io, kind)].most_common(1)
        rep = rep[0][0] if rep else norm
        w.writerow(["大区分候補", len(corps), stmt, io, norm, parent, kind, rep])
    for (stmt, norm, parent, io, kind), corps in small:
        rep = review_rawnames[(stmt, norm, parent, io, kind)].most_common(1)
        rep = rep[0][0] if rep else norm
        w.writerow(["小科目候補", len(corps), stmt, io, norm, parent, kind, rep])
    for (typ, name), corps in held_sorted:
        w.writerow(["held", len(corps), "", "", name, "", typ, name])

print("\n== master_candidates.csv を出力しました（masters.py反映の元データ）==")
