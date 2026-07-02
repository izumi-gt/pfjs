# -*- coding: utf-8 -*-
r"""
タイプ4(CF/PL)の拠点内重複を「集計だけ」出す。全文は出さない。
同名科目が、どの depth で・拠点あたり何回・直近のdepth0(大区分)が何か を要約。

使い方:
  python3 dump_kyoten4_summary.py data 2025 8240005001615 CF
"""
import sys, os
from collections import Counter, defaultdict
import pdfplumber
from shafuku_parser.cli_all import scan_tree, _guess_corp_no
from shafuku_parser.extract_kyoten4 import extract_kyoten4

root = sys.argv[1] if len(sys.argv) > 1 else "data"
year = sys.argv[2] if len(sys.argv) > 2 else None
corp_no_want = sys.argv[3] if len(sys.argv) > 3 else None
stmt = sys.argv[4] if len(sys.argv) > 4 else "CF"
form = {"CF": "1-4", "PL": "2-4", "BS": "3-4"}[stmt]

jobs = scan_tree(root)
job = next((j for j in jobs
            if _guess_corp_no(os.path.basename(j["corp_dir"].rstrip("/")),
                              j["corp_name"]) == corp_no_want), None)
if job is None:
    print(f"法人 {corp_no_want} が見つかりません"); sys.exit(1)
pdf_path = job["paths"].get(form)

with pdfplumber.open(pdf_path) as pdf:
    blocks = extract_kyoten4(pdf, stmt)

print(f"=== {corp_no_want} {stmt}({form}) 重複要約 ===")
print(f"拠点数: {len(blocks)}\n")

# 1拠点目について、同名科目が「直近depth0(大区分)」ごとにどう出るか
b0 = blocks[0]
print(f"--- 先頭拠点 [{b0['kyoten']}] の depth0(大区分)一覧 ---")
d0_names = [r[0] for r in b0["rows"] if r[1] == 0]
for nm in d0_names:
    print(f"  D0: {nm}")

print(f"\n--- 先頭拠点で (depth, name) が複数回出る科目（直近D0付き）---")
# 各行に直近のdepth0を付与
annotated = []
cur_d0 = None
for r in b0["rows"]:
    if r[1] == 0:
        cur_d0 = r[0]
    annotated.append((r[1], r[0], cur_d0, r[2]))

key_count = Counter((d, nm) for (d, nm, d0, v) in annotated)
dups = [(d, nm) for (d, nm), c in key_count.items() if c > 1]
print(f"重複する (depth,name): {len(dups)} 種\n")
for (d, nm) in dups[:40]:
    occ = [(d0, v) for (dd, n, d0, v) in annotated if dd == d and n == nm]
    d0s = [d0 for d0, v in occ]
    same_d0 = len(set(d0s)) == 1
    tag = "★同一D0内で重複" if same_d0 else "別D0にまたがる"
    print(f"  depth{d} '{nm}' x{len(occ)} [{tag}]")
    for d0, v in occ:
        print(f"      D0={d0}  values={v}")

# 全拠点での総括: 1拠点あたり平均何件の重複が出るか
print("\n--- 全拠点総括 ---")
tot_dup = 0
for b in blocks:
    kc = Counter((r[1], r[0]) for r in b["rows"])
    tot_dup += sum(1 for k, c in kc.items() if c > 1)
print(f"全拠点の重複(depth,name)合計: {tot_dup}")
print(f"1拠点あたり平均: {tot_dup/len(blocks):.1f}")
