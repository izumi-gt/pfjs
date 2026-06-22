# -*- coding: utf-8 -*-
r"""
指定法人のタイプ4(1-4 CF / 2-4 PL)の抽出結果をダンプし、
拠点内で同じ (depth, name, parent) が複数回出ているかを可視化する。

使い方（deliver直下で）:
  python3 dump_kyoten4.py data 2025 8240005001615 CF
  python3 dump_kyoten4.py data 2025 8240005001615 PL
"""
import sys, os
from collections import Counter
import pdfplumber
from shafuku_parser.cli_all import scan_tree, _guess_corp_no
from shafuku_parser.extract_kyoten4 import extract_kyoten4

root = sys.argv[1] if len(sys.argv) > 1 else "data"
year = sys.argv[2] if len(sys.argv) > 2 else None
corp_no_want = sys.argv[3] if len(sys.argv) > 3 else None
stmt = sys.argv[4] if len(sys.argv) > 4 else "CF"   # CF / PL / BS

form_of = {"CF": "1-4", "PL": "2-4", "BS": "3-4"}
form = form_of[stmt]

jobs = scan_tree(root)
job = None
for j in jobs:
    cno = _guess_corp_no(os.path.basename(j["corp_dir"].rstrip("/")), j["corp_name"])
    if cno == corp_no_want:
        job = j; break
if job is None:
    print(f"法人 {corp_no_want} が見つかりません"); sys.exit(1)

pdf_path = job["paths"].get(form)
if not pdf_path:
    print(f"様式 {form} のPDFが見つかりません。paths={list(job['paths'])}"); sys.exit(1)

print(f"=== {corp_no_want} {stmt}({form}) 抽出ダンプ ===")
print(f"PDF: {pdf_path}\n")

with pdfplumber.open(pdf_path) as pdf:
    blocks = extract_kyoten4(pdf, stmt)
print(f"拠点数(block数): {len(blocks)}")
loc_names = [b["kyoten"] for b in blocks]
print(f"拠点名一覧: {loc_names}\n")

# 拠点名の重複（同じ拠点が2回抽出されていないか）
loc_dup = [k for k, c in Counter(loc_names).items() if c > 1]
if loc_dup:
    print(f"★ 拠点名が重複しています: {loc_dup}")
    print("  → 同じ拠点を2回読んでいる可能性が高い（block重複）\n")
else:
    print("拠点名の重複なし（block単位では重複していない）\n")

# 各拠点内で同じ (name, depth) が複数回出ているか
print("--- 拠点内での科目名重複 ---")
for b in blocks:
    rows = b["rows"]
    names = [(r[0], r[1]) for r in rows]   # (name, depth)
    dup = [k for k, c in Counter(names).items() if c > 1]
    if dup:
        print(f"\n[{b['kyoten']}] 行数={len(rows)} 重複科目={len(dup)}")
        for (nm, dp) in dup[:30]:
            # その科目の全出現を行内容つきで表示
            occ = [r for r in rows if r[0] == nm and r[1] == dp]
            print(f"  ◆ depth{dp} '{nm}' x{len(occ)}")
            for r in occ:
                vals = r[2]
                print(f"      values={vals}")

print("\n--- 先頭拠点の全行（レイアウト確認用・最大60行）---")
b0 = blocks[0]
print(f"[{b0['kyoten']}] colnames={b0['colnames']}")
for i, r in enumerate(b0["rows"][:60]):
    print(f"  {i:>3} depth{r[1]} {r[0]!r} {r[2]}")
