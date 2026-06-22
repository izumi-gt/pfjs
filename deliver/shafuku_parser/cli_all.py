# -*- coding: utf-8 -*-
"""
全12様式 一括取り込みCLI（ローカル運用のメインコマンド）。

走査対象: <root>/<会計年度>/<法人>/ 配下の PDF。
ファイル名規約:  <年度>_<N-M>_<様式名>_<法人名>.pdf
  例: 2025_1-1_財務諸表_資金収支計算書_社会福祉法人あしたか太陽の丘.pdf
  N-M で様式判定（1-1〜3-4 の12様式）。注記ファイル（'注記' を含む）は skip。
  12様式が揃っていなくてよい（揃っている様式だけ取り込む＝無人完走）。

法人番号: ディレクトリ名が13桁ならそれを採用。無ければ --corp-no か、
  ファイル名/法人名から推定（暫定）。実運用では法人マスタ突合に差し替える。

使い方:
  # フォルダ一括（推奨）
  python -m shafuku_parser.cli_all scan <root> --out <outdir>

  # 1法人ぶんを直接指定（様式は任意個）
  python -m shafuku_parser.cli_all one --year 2024 \\
      --corp-no 8080105000129 --corp-name "社会福祉法人あしたか太陽の丘" \\
      --pdf 1-1=a.pdf --pdf 2-1=b.pdf ... --out <outdir>

  # 全法人を1つの統合DB(xlsx)へ
  python -m shafuku_parser.cli_all build-db <root> --out <outdir> --xlsx 決算情報データベース.xlsx
"""
import os
import re
import csv
import argparse

from . import integrate_all

_VALID_FORMS = set(integrate_all._FORM_STMT.keys())  # 1-1..3-4
_NM_RE = re.compile(r"_(\d-\d)_")


def _classify(fname):
    """ファイル名 → (form|None, is_chuki)。"""
    if "注記" in fname:
        return None, True
    m = _NM_RE.search(fname)
    if not m:
        # 末尾や先頭の N-M も拾う保険
        m2 = re.search(r"(?<![0-9])(\d-\d)(?![0-9])", fname)
        if not m2:
            return None, False
        nm = m2.group(1)
    else:
        nm = m.group(1)
    return (nm if nm in _VALID_FORMS else None), False


def _year_from_name(fname, default=""):
    m = re.match(r"^(\d{4})_", fname)
    return m.group(1) if m else default


def _guess_corp_no(corp_dirname, corp_name):
    if re.fullmatch(r"\d{13}", corp_dirname):
        return corp_dirname
    m = re.search(r"\d{13}", corp_name or "")
    if m:
        return m.group(0)
    import hashlib
    return "X" + hashlib.sha1((corp_name or corp_dirname).encode()).hexdigest()[:12]


def scan_tree(root):
    """root/<年度>/<法人>/ を舐め、法人ごとに {form: path} を返す。"""
    jobs = []
    if not os.path.isdir(root):
        raise SystemExit(f"root が存在しません: {root}")
    for year in sorted(os.listdir(root)):
        ydir = os.path.join(root, year)
        if not os.path.isdir(ydir):
            continue
        for corp in sorted(os.listdir(ydir)):
            cdir = os.path.join(ydir, corp)
            if not os.path.isdir(cdir):
                continue
            paths, skipped, yr = {}, [], year
            for fn in sorted(os.listdir(cdir)):
                if not fn.lower().endswith(".pdf"):
                    continue
                form, is_chuki = _classify(fn)
                if is_chuki:
                    skipped.append((fn, "注記")); continue
                if form is None:
                    skipped.append((fn, "様式判定不可")); continue
                paths[form] = os.path.join(cdir, fn)
                yr = _year_from_name(fn, year)
            if paths:
                jobs.append({"year": yr, "corp_dir": cdir, "corp_name": corp,
                             "paths": paths, "skipped": skipped})
    return jobs


def _print_summary(s):
    print(f"   取込様式: {sorted(s['types_processed'])}")
    print(f"   fact={s['fact_rows']} segment={s['segments']} "
          f"local={s['locals']} concept={s['concepts']}")
    print(f"   NG(タイプ別)={s['per_type_ng']} | FK/PK={s['fkpk_problems']} "
          f"| 重複local群={s['duplicate_local_groups']} | held={s['held']}")
    bad = [r for r in s["crossform_reconcile"] if r[3]]
    if bad:
        print("   cross-form 差分:")
        for r in bad:
            print(f"     {r[0]} {r[1]} 不一致{r[3]}/{r[2]} … {r[4]}")
    print(f"   CSV出力先: {os.path.dirname(s['csv_paths']['fact_financial'])}")


def cmd_scan(args):
    jobs = scan_tree(args.root)
    if not jobs:
        print("対象PDFが見つかりませんでした。"); return
    print(f"== {len(jobs)} 法人を検出 ==")
    summary_rows = []
    for job in jobs:
        corp_no = args.corp_no or _guess_corp_no(
            os.path.basename(job["corp_dir"].rstrip("/")), job["corp_name"])
        outdir = os.path.join(args.out, corp_no)
        print(f"\n--- {job['corp_name']} (corp_no={corp_no}, year={job['year']}) ---")
        for fn, why in job["skipped"]:
            print(f"   skip: {fn} ({why})")
        try:
            s = integrate_all.run_corp_all(
                job["paths"], corp_no, job["corp_name"], job["year"], outdir)
        except Exception as e:
            print(f"   ERROR: {type(e).__name__}: {e}")
            summary_rows.append({
                "corp_no": corp_no, "corp_name": job["corp_name"],
                "year": job["year"], "status": "ERROR",
                "error": f"{type(e).__name__}: {e}",
                "types_processed": "", "fact_rows": "",
                "ng_houjin": "", "ng_segwari": "", "ng_segwari3": "", "ng_kyoten4": "",
                "ng_total": "", "fkpk": "", "dup_local": "", "held": "",
                "warnings": "", "crossform_diff": "",
            })
            continue
        _print_summary(s)
        ng = s["per_type_ng"]
        crossform_diff = sum(1 for r in s["crossform_reconcile"] if r[3])
        summary_rows.append({
            "corp_no": s["corp_no"], "corp_name": s["corp_name"],
            "year": job["year"], "status": "OK", "error": "",
            "types_processed": "|".join(s["types_processed"]),
            "fact_rows": s["fact_rows"],
            "ng_houjin": ng.get("houjin", ""),
            "ng_segwari": ng.get("segwari", ""),
            "ng_segwari3": ng.get("segwari3", ""),
            "ng_kyoten4": ng.get("kyoten4", ""),
            "ng_total": sum(ng.values()),
            "fkpk": s["fkpk_problems"],
            "dup_local": s["duplicate_local_groups"],
            "held": s["held"], "warnings": s["warnings"],
            "crossform_diff": crossform_diff,
        })
    print("\n== 全法人完了 ==")

    summary_path = args.summary or os.path.join(args.out, "_scan_summary.csv")
    cols = ["corp_no", "corp_name", "year", "status",
            "types_processed", "fact_rows",
            "ng_houjin", "ng_segwari", "ng_segwari3", "ng_kyoten4", "ng_total",
            "fkpk", "dup_local", "held", "warnings", "crossform_diff", "error"]
    with open(summary_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in summary_rows:
            w.writerow(r)
    print(f"== 集計サマリー: {summary_path} ({len(summary_rows)}法人) ==")
    n_err = sum(1 for r in summary_rows if r["status"] == "ERROR")
    n_ng = sum(1 for r in summary_rows if r["status"] == "OK" and r["ng_total"])
    n_fkpk = sum(1 for r in summary_rows if r["status"] == "OK" and r["fkpk"])
    print(f"   ERROR={n_err} / NG有={n_ng} / FK・PK有={n_fkpk}")


def cmd_one(args):
    paths = {}
    for kv in (args.pdf or []):
        if "=" not in kv:
            raise SystemExit(f"--pdf は form=path 形式: {kv}")
        form, p = kv.split("=", 1)
        paths[form] = p
    if not paths:
        raise SystemExit("--pdf form=path を1つ以上指定してください")
    s = integrate_all.run_corp_all(
        paths, args.corp_no, args.corp_name, args.year, args.out)
    _print_summary(s)


def cmd_build_db(args):
    """root配下の全法人を取り込み、1つの統合DB(xlsx)を作る。
    神奈川の手書きcorps（engine同梱）も --include-kanagawa で混ぜられる。"""
    from shafuku_db_engine.ingest import ingest
    jobs = scan_tree(args.root)
    groups = []
    labels = []

    if args.include_kanagawa:
        try:
            from shafuku_db_engine.corps.kanagawa import CORP
            groups.append([ingest(CORP)]); labels.append("神奈川(手書き)")
        except Exception as e:
            print("神奈川corps取込スキップ:", e)

    for job in jobs:
        corp_no = args.corp_no or _guess_corp_no(
            os.path.basename(job["corp_dir"].rstrip("/")), job["corp_name"])
        ings = _ingest_all_types(job["paths"], corp_no, job["corp_name"], job["year"])
        if ings:
            groups.append(ings); labels.append(job["corp_name"])

    if not groups:
        print("取込対象なし。"); return
    problems, nfact = integrate_all.build_unified_db(groups, args.xlsx)
    print(f"== 統合DB: {len(groups)}法人 / 総fact={nfact} / FK・PK problems={len(problems)} ==")
    for lb in labels:
        print("   含む法人:", lb)
    for p in problems[:10]:
        print("   problem:", p)
    print("   出力:", args.xlsx)


def _ingest_all_types(paths, corp_no, corp_name, year):
    """1法人の {form:path} を4タイプに振り分け ingest し、Ingested のリストで返す。"""
    from shafuku_db_engine import masters
    from shafuku_db_engine.ingest import ingest
    by_type = {"houjin": {}, "segwari": {}, "segwari3": {}, "kyoten4": {}}
    for form, p in paths.items():
        if form in integrate_all._FORM_STMT:
            typ, stmt = integrate_all._FORM_STMT[form]
            by_type[typ][stmt] = p
    out = []
    if by_type["houjin"]:
        from shafuku_parser.adapter_houjin import build_corpdata as bc
        c, *_ = bc(by_type["houjin"], corp_no, corp_name, year, masters)
        out.append(ingest(c))
    if by_type["segwari"]:
        from shafuku_parser.adapter_segwari import build_corpdata_segwari as bc
        c, *_ = bc(by_type["segwari"], corp_no, corp_name, year, masters)
        out.append(ingest(c))
    if by_type["segwari3"]:
        from shafuku_parser.adapter_segwari3 import build_corpdata as bc
        c, *_ = bc(by_type["segwari3"], corp_no, corp_name, year, masters)
        out.append(integrate_all._ingest_with_segwari3_locals(c))
    if by_type["kyoten4"]:
        from shafuku_parser.adapter_kyoten4 import build_corpdata as bc
        c, *_ = bc(by_type["kyoten4"], corp_no, corp_name, year, masters)
        out.append(ingest(c))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="shafuku_parser.cli_all",
                                description="社福 決算 全12様式 一括取り込みCLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="root/<年度>/<法人>/ を走査し法人ごとにCSV出力")
    ps.add_argument("root")
    ps.add_argument("--out", required=True, help="CSV出力先ルート")
    ps.add_argument("--corp-no", default=None, help="法人番号を強制指定(任意)")
    ps.add_argument("--summary", default=None,
                    help="全法人サマリーCSVの出力先(既定: <out>/_scan_summary.csv)")
    ps.set_defaults(func=cmd_scan)

    po = sub.add_parser("one", help="1法人を直接指定して取り込み")
    po.add_argument("--pdf", action="append", help="form=path 形式 (例 1-1=a.pdf)。複数可")
    po.add_argument("--corp-no", required=True)
    po.add_argument("--corp-name", required=True)
    po.add_argument("--year", required=True)
    po.add_argument("--out", required=True)
    po.set_defaults(func=cmd_one)

    pb = sub.add_parser("build-db", help="root配下の全法人を1つの統合DB(xlsx)に")
    pb.add_argument("root")
    pb.add_argument("--out", default=".", help="(未使用・予約)")
    pb.add_argument("--xlsx", required=True, help="出力xlsxパス")
    pb.add_argument("--corp-no", default=None)
    pb.add_argument("--include-kanagawa", action="store_true",
                    help="engine同梱の神奈川(手書き)も統合DBに含める")
    pb.set_defaults(func=cmd_build_db)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
