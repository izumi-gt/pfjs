# -*- coding: utf-8 -*-
r"""
ingestを実走し、CF-1-4で主キー重複しているfactのLコードを、
locals表から (parent, name) に逆引きして「何が衝突しているか」を確定する。

使い方:
  python3 trace_dup.py data 2025 8240005001615
"""
import sys, os
from collections import Counter, defaultdict
from shafuku_parser.cli_all import scan_tree, _guess_corp_no
from shafuku_parser.integrate_all import (
    _FORM_STMT, _bc_kyoten4, _ingest_with_segwari3_locals)
from shafuku_db_engine.ingest import ingest
from shafuku_db_engine import masters

corp_no_want = sys.argv[3] if len(sys.argv) > 3 else "8240005001615"
root = sys.argv[1] if len(sys.argv) > 1 else "data"

jobs = scan_tree(root)
job = next((j for j in jobs
            if _guess_corp_no(os.path.basename(j["corp_dir"].rstrip("/")),
                              j["corp_name"]) == corp_no_want), None)

by_type = {}
for form, p in job["paths"].items():
    if form in _FORM_STMT:
        typ, stmt = _FORM_STMT[form]
        by_type.setdefault(typ, {})[stmt] = p

# kyoten4だけ ingest
c, *_ = _bc_kyoten4(by_type["kyoten4"], corp_no_want, job["corp_name"],
                    job["year"], masters)
ing = ingest(c)

# locals: code -> (parent, name, depth)
loc_info = {r[0]: (r[2], r[3], r[4]) for r in ing.locals}

# fact主キー重複（CF-1-4）を集計
pk = defaultdict(list)
for f in ing.fact:
    if f[2] != "CF-1-4":
        continue
    key = (f[2], f[3], f[4], f[5])  # 様式,科目,区分,metric
    pk[key].append(f[6])

dups = {k: v for k, v in pk.items() if len(v) > 1}
print(f"=== CF-1-4 主キー重複: {len(dups)} キー ===\n")

# 重複しているLコードを集計
dup_codes = Counter(k[1] for k in dups)
print("--- 重複に絡む科目コード Top20（逆引き）---")
for code, cnt in dup_codes.most_common(20):
    parent, name, depth = loc_info.get(code, ("?", "?", "?"))
    print(f"  {code} x{cnt}  depth{depth} name={name!r} parent={parent!r}")

# 具体例: 1つのキーが複数Lコードを取るのではなく、
# 同じLコードが複数回emitされているかを確認
print("\n--- サンプル: 重複キーの中身（先頭5件）---")
for k, vals in list(dups.items())[:5]:
    code = k[1]
    parent, name, depth = loc_info.get(code, ("?", "?", "?"))
    print(f"  {k}  values={vals}")
    print(f"     → {name!r} (parent={parent!r}, depth{depth})")

# locals自体に同じ(parent,name)で複数コードがあるか、
# 逆に同じコードが複数(parent,name)に対応していないか
print("\n--- locals整合チェック ---")
by_pn = defaultdict(set)
for code, (parent, name, depth) in loc_info.items():
    by_pn[(parent, name)].add(code)
multi = {k: v for k, v in by_pn.items() if len(v) > 1}
print(f"同じ(parent,name)に複数Lコード: {len(multi)} 組")
for (parent, name), codes in list(multi.items())[:10]:
    print(f"  (parent={parent!r}, name={name!r}) -> {sorted(codes)}")
