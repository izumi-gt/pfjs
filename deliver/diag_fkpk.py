# -*- coding: utf-8 -*-
r"""
指定した1法人の FK/PK 問題を全件出力して原因を切り分ける。

使い方（deliver直下で）:
  python3 diag_fkpk.py data 2025 8240005001615

引数:
  <root>      data フォルダ
  <year>      会計年度フォルダ名
  <corp_no>   法人番号（=フォルダ名）
"""
import sys, os
from shafuku_parser.cli_all import scan_tree, _guess_corp_no
from shafuku_parser import integrate_all
from shafuku_db_engine.build import post_checks, global_account_rows
from shafuku_db_engine import schema
from collections import Counter

root = sys.argv[1] if len(sys.argv) > 1 else "data"
year = sys.argv[2] if len(sys.argv) > 2 else None
corp_no_want = sys.argv[3] if len(sys.argv) > 3 else None

jobs = scan_tree(root)
job = None
for j in jobs:
    cno = _guess_corp_no(os.path.basename(j["corp_dir"].rstrip("/")), j["corp_name"])
    if corp_no_want and cno == corp_no_want:
        job = j; corp_no = cno; break
    if corp_no_want is None:
        job = j; corp_no = cno; break

if job is None:
    print(f"法人 {corp_no_want} が見つかりません"); sys.exit(1)

print(f"=== {corp_no} ({job['corp_name']}) FK/PK 全件診断 ===\n")

# 統合せず、タイプごとに ingest して problems を全件出す
import importlib
from shafuku_db_engine.ingest import ingest
from shafuku_parser.integrate_all import (
    _FORM_STMT, _bc_houjin, _bc_segwari, _bc_segwari3, _bc_kyoten4,
    _ingest_with_segwari3_locals,
)
from shafuku_db_engine import masters

by_type = {"houjin": {}, "segwari": {}, "segwari3": {}, "kyoten4": {}}
for form, p in job["paths"].items():
    if form in _FORM_STMT:
        typ, stmt = _FORM_STMT[form]
        by_type[typ][stmt] = p

ingesteds = []
if by_type["houjin"]:
    c, *_ = _bc_houjin(by_type["houjin"], corp_no, job["corp_name"], job["year"], masters)
    ingesteds.append(("houjin", ingest(c)))
if by_type["segwari"]:
    c, *_ = _bc_segwari(by_type["segwari"], corp_no, job["corp_name"], job["year"], masters)
    ingesteds.append(("segwari", ingest(c)))
if by_type["segwari3"]:
    c, *_ = _bc_segwari3(by_type["segwari3"], corp_no, job["corp_name"], job["year"], masters)
    ingesteds.append(("segwari3", _ingest_with_segwari3_locals(c)))
if by_type["kyoten4"]:
    c, *_ = _bc_kyoten4(by_type["kyoten4"], corp_no, job["corp_name"], job["year"], masters)
    ingesteds.append(("kyoten4", ingest(c)))

# 統合
facts, seg_by, loc_by = [], {}, {}
for _, ing in ingesteds:
    facts.extend(ing.fact)
    for r in ing.segments: seg_by.setdefault(r[0], r)
    for r in ing.locals: loc_by.setdefault(r[0], r)
seg_rows = list(seg_by.values())
local_rows = list(loc_by.values())

problems = post_checks(facts, seg_rows, local_rows)
print(f"FK/PK 問題: {len(problems)} 件\n")

# 種類別に分類
kinds = Counter()
for p in problems:
    kind = p.split(":")[0]
    kinds[kind] += 1
print("--- 種類別件数 ---")
for k, c in kinds.most_common():
    print(f"  {k}: {c}")

# --- fact主キー重複を実データで全件＋金額付きで洗い出す ---
pk_idx = list(range(6))  # 法人,年度,様式,科目,区分,metric
groups = {}
for f in facts:
    key = tuple(f[i] for i in pk_idx)
    groups.setdefault(key, []).append(f[6])  # 金額(index6)
dups = {k: v for k, v in groups.items() if len(v) > 1}
print(f"\n--- fact主キー重複: 実数 {len(dups)} キー ---")
same_val = sum(1 for v in dups.values() if len(set(v)) == 1)
diff_val = len(dups) - same_val
print(f"  金額も完全一致(=二重読みの疑い): {same_val} キー")
print(f"  金額が異なる(=別物が衝突): {diff_val} キー")

# 様式別の重複キー数
byform = Counter(k[2] for k in dups)
print("  様式別:")
for form, c in byform.most_common():
    print(f"    {form}: {c}")

print("\n  --- 重複キー 全件（金額リスト付き）---")
for k, vals in sorted(dups.items()):
    flag = "同値" if len(set(vals)) == 1 else "異値"
    print(f"   [{flag}] {k[2]} {k[3]} {k[4]} {k[5]} -> {vals}")

print("\n--- その他のFK問題（主キー重複以外）---")
for p in problems:
    if not p.startswith("fact主キー重複"):
        print(" ", p)
