# -*- coding: utf-8 -*-
r"""
_scan_summary.csv を読んで、440社の品質分布を俯瞰する。
使い方:
  python3 analyze_summary.py out_csv\_scan_summary.csv
"""
import sys, csv
from collections import Counter

path = sys.argv[1] if len(sys.argv) > 1 else "out_csv/_scan_summary.csv"
rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
n = len(rows)
print(f"== 全 {n} 法人 ==\n")


def _int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def dist(label, key, buckets):
    """件数を区間に振り分けて表示。"""
    vals = [_int(r[key]) for r in rows if r["status"] == "OK"]
    print(f"--- {label} 分布 ---")
    total_nonzero = sum(1 for v in vals if v > 0)
    print(f"  非ゼロ法人: {total_nonzero} / {len(vals)}")
    c = Counter()
    for v in vals:
        for lo, hi in buckets:
            if lo <= v <= hi:
                c[(lo, hi)] += 1
                break
    for lo, hi in buckets:
        cnt = c[(lo, hi)]
        if cnt == 0:
            continue
        rng = f"{lo}" if lo == hi else (f"{lo}-{hi}" if hi < 10**9 else f"{lo}+")
        bar = "#" * min(60, cnt)
        print(f"  {rng:>8} : {cnt:>4}  {bar}")
    print()


bk = [(0, 0), (1, 1), (2, 3), (4, 5), (6, 10), (11, 20),
      (21, 50), (51, 100), (101, 10**9)]

# タイプ別NG
for key, label in [("ng_houjin", "NG houjin(タイプ1)"),
                   ("ng_segwari", "NG segwari(タイプ2)"),
                   ("ng_segwari3", "NG segwari3(タイプ3)"),
                   ("ng_kyoten4", "NG kyoten4(タイプ4)"),
                   ("ng_total", "NG 合計"),
                   ("fkpk", "FK/PK problems"),
                   ("held", "held(未収載科目)"),
                   ("crossform_diff", "cross-form 差分")]:
    dist(label, key, bk)

# 重症ワースト20（NG合計）
print("--- NG合計 ワースト20 ---")
worst = sorted([r for r in rows if r["status"] == "OK"],
               key=lambda r: _int(r["ng_total"]), reverse=True)[:20]
print(f"  {'法人番号':<15}{'NG計':>5}{'h':>4}{'s2':>4}{'s3':>4}{'k4':>4}{'FK/PK':>7}{'held':>6}  法人名")
for r in worst:
    print(f"  {r['corp_no']:<15}{r['ng_total']:>5}{r['ng_houjin']:>4}"
          f"{r['ng_segwari']:>4}{r['ng_segwari3']:>4}{r['ng_kyoten4']:>4}"
          f"{r['fkpk']:>7}{r['held']:>6}  {r['corp_name']}")

# FK/PK ワースト20
print("\n--- FK/PK ワースト20 ---")
worst2 = sorted([r for r in rows if r["status"] == "OK"],
                key=lambda r: _int(r["fkpk"]), reverse=True)[:20]
print(f"  {'法人番号':<15}{'FK/PK':>7}{'NG計':>6}{'held':>6}  法人名")
for r in worst2:
    print(f"  {r['corp_no']:<15}{r['fkpk']:>7}{r['ng_total']:>6}{r['held']:>6}  {r['corp_name']}")
