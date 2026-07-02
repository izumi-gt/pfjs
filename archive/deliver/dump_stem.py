# -*- coding: utf-8 -*-
r"""
タイプ4 CFをアダプタに通し、local_codes の採番を実際に確認。
水道光熱費支出 等が、事業費支出/事務費支出で別Lコードになっているか。
"""
import sys, os
import pdfplumber
from shafuku_parser.cli_all import scan_tree, _guess_corp_no
from shafuku_parser.extract_kyoten4 import extract_kyoten4
from shafuku_parser import adapter_kyoten4
from shafuku_db_engine import masters

corp_no_want = sys.argv[3] if len(sys.argv) > 3 else "8240005001615"
root = sys.argv[1] if len(sys.argv) > 1 else "data"

jobs = scan_tree(root)
job = next((j for j in jobs
            if _guess_corp_no(os.path.basename(j["corp_dir"].rstrip("/")),
                              j["corp_name"]) == corp_no_want), None)
pdf_path = job["paths"].get("1-4")

# extract → 先頭拠点のrowsを直接確認
with pdfplumber.open(pdf_path) as pdf:
    blocks = extract_kyoten4(pdf, "CF")

b0 = blocks[0]
print(f"=== 先頭拠点 [{b0['kyoten']}] 水道光熱費支出 周辺の生rows ===\n")
cur_d0 = None
for i, (name, depth, assign) in enumerate(b0["rows"]):
    if depth == 0:
        cur_d0 = name
    if name in ("水道光熱費支出", "事業費支出", "事務費支出", "雑支出",
                "人件費支出", "その他の支出"):
        print(f"  row{i:>3} depth{depth} D0={cur_d0!r:20} name={name!r}")

# --- アダプタを通して local_codes の採番を確認 ---
print("\n=== アダプタ通過後の採番（水道光熱費支出/雑支出）===\n")
from shafuku_parser.registry import Registry
from shafuku_parser import naming

# CF幹マスタでRegistry構築（adapterと同じ手順）
reg = Registry(adapter_kyoten4._statement_master("CF", masters), "CF", corp_no_want)
io_map = adapter_kyoten4._build_io_map(
    masters.CF_MASTER, masters.PL_MASTER, masters.BS_MASTER)

cur_stem, cur_stem_io = None, ""
for (name, depth, assign) in b0["rows"]:
    if depth == 0:
        cur_stem = reg.resolve_global(name)
        cur_stem_io = io_map.get(cur_stem, "")
        continue
    if name in ("水道光熱費支出", "雑支出") and depth == 1:
        parent = cur_stem if cur_stem else ""
        n = naming.normalize(name)
        fp = naming.concept_fingerprint(n, parent, cur_stem_io)
        ic, cc, _ = reg.resolve_local(name, parent, cur_stem_io, "CF-1-4", "p")
        print(f"  {name}  親stem={parent}  io={cur_stem_io!r}")
        print(f"     fp={fp}")
        print(f"     concept={cc}  instance(L)={ic}\n")

