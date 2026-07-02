# -*- coding: utf-8 -*-
r"""
指定法人を全タイプ統合でingestし、FK/PK問題を様式別・種類別に分類。
FK/PK=21 の残存社が、どのタイプ/様式で何の違反を出しているか特定する。

使い方:
  python3 diag_fkpk_all.py data 2025 2240005004994
"""
import sys, os
from collections import Counter, defaultdict
from shafuku_parser.cli_all import scan_tree, _guess_corp_no
from shafuku_parser.integrate_all import (
    _FORM_STMT, _bc_houjin, _bc_segwari, _bc_segwari3, _bc_kyoten4,
    _ingest_with_segwari3_locals)
from shafuku_db_engine.ingest import ingest
from shafuku_db_engine.build import post_checks
from shafuku_db_engine import masters

corp_no_want = sys.argv[3] if len(sys.argv) > 3 else "2240005004994"
root = sys.argv[1] if len(sys.argv) > 1 else "data"

jobs = scan_tree(root)
job = next((j for j in jobs
            if _guess_corp_no(os.path.basename(j["corp_dir"].rstrip("/")),
                              j["corp_name"]) == corp_no_want), None)
if job is None:
    print(f"法人 {corp_no_want} が見つかりません"); sys.exit(1)

by_type = {}
for form, p in job["paths"].items():
    if form in _FORM_STMT:
        typ, stmt = _FORM_STMT[form]
        by_type.setdefault(typ, {})[stmt] = p

ingesteds = []
if by_type.get("houjin"):
    c, *_ = _bc_houjin(by_type["houjin"], corp_no_want, job["corp_name"], job["year"], masters)
    ingesteds.append(("houjin", ingest(c)))
if by_type.get("segwari"):
    c, *_ = _bc_segwari(by_type["segwari"], corp_no_want, job["corp_name"], job["year"], masters)
    ingesteds.append(("segwari", ingest(c)))
if by_type.get("segwari3"):
    c, *_ = _bc_segwari3(by_type["segwari3"], corp_no_want, job["corp_name"], job["year"], masters)
    ingesteds.append(("segwari3", _ingest_with_segwari3_locals(c)))
if by_type.get("kyoten4"):
    c, *_ = _bc_kyoten4(by_type["kyoten4"], corp_no_want, job["corp_name"], job["year"], masters)
    ingesteds.append(("kyoten4", ingest(c)))

# 統合
facts, seg_by, loc_by = [], {}, {}
for _, ing in ingesteds:
    facts.extend(ing.fact)
    for r in ing.segments: seg_by.setdefault(r[0], r)
    for r in ing.locals: loc_by.setdefault(r[0], r)
seg_rows = list(seg_by.values())
local_rows = list(loc_by.values())
loc_info = {r[0]: (r[2], r[3], r[4]) for r in local_rows}

problems = post_checks(facts, seg_rows, local_rows)
print(f"=== {corp_no_want} 全タイプ統合 FK/PK: {len(problems)} 件 ===\n")

kinds = Counter(p.split(":")[0] for p in problems)
print("--- 種類別 ---")
for k, c in kinds.most_common():
    print(f"  {k}: {c}")

# 主キー重複の実数（打ち切りなし）を様式別に
pk = defaultdict(list)
for f in facts:
    pk[(f[2], f[3], f[4], f[5])].append(f[6])
dups = {k: v for k, v in pk.items() if len(v) > 1}
print(f"\n--- fact主キー重複: 実数 {len(dups)} キー（様式別）---")
byform = Counter(k[0] for k in dups)
for form, c in byform.most_common():
    print(f"  {form}: {c}")

# 重複Lコードの逆引きTop10
print("\n--- 重複に絡む科目コード Top10 ---")
dupcodes = Counter(k[1] for k in dups)
for code, cnt in dupcodes.most_common(10):
    parent, name, depth = loc_info.get(code, ("?", "?", "?"))
    print(f"  {code} x{cnt}  depth{depth} name={name!r} parent={parent!r}")

# 主キー重複以外
print("\n--- 主キー重複以外のFK問題 ---")
for p in problems:
    if not p.startswith("fact主キー重複"):
        print(" ", p)
