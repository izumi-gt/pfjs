# -*- coding: utf-8 -*-
"""
フォルダ走査CLI（タイプ4 無人取り込み）。

走査対象: <root>/<会計年度>/<法人>/ 配下の PDF。
ファイル名規約（本データ）:  <年度>_<N-M>_<様式名>_<法人名>.pdf
  例: 2025_1-4_拠点区分資金収支計算書_社会福祉法人あしたか太陽の丘.pdf
  N-M で様式判定（1-4=CF / 2-4=PL / 3-4=BS のタイプ4のみ本CLIの対象）。
  注記ファイル（'注記' を含む）は skip。タイプ1〜3（1-1〜3-3）は後続CLIの担当のため
  本CLIでは skip（警告ログのみ）。

法人番号は本来 dim_corp の台帳から引くが、ここでは簡易にディレクトリ名 or 
--corp-no 指定 or ファイル名から推定する。実運用では法人マスタ突合に差し替える。

使い方:
  python -m shafuku_parser.cli_kyoten4 scan <root> --out <outdir>
  python -m shafuku_parser.cli_kyoten4 one --cf a.pdf --pl b.pdf --bs c.pdf \\
      --corp-no 8080105000129 --corp-name "社会福祉法人..." --year 2024 --out <outdir>
"""
import os
import re
import sys
import argparse

from . import run_kyoten4

# N-M → statement
_FORM2STMT = {"1-4": "CF", "2-4": "PL", "3-4": "BS"}
_NM_RE = re.compile(r"(\d-\d)")


def _classify(fname):
    """ファイル名から (statement|None, is_chuki, nm|None)。"""
    if "注記" in fname:
        return None, True, None
    m = _NM_RE.search(fname)
    if not m:
        return None, False, None
    nm = m.group(1)
    return _FORM2STMT.get(nm), False, nm


def _year_from_name(fname):
    m = re.match(r"^(\d{4})_", fname)
    return m.group(1) if m else ""


def scan_tree(root):
    """root/<年度>/<法人>/ を舐め、法人ごとに {CF,PL,BS}->path を返す。
    戻り: list of dict(year, corp_dir, corp_name, paths={CF,PL,BS}, skipped=[...])
    """
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
            paths = {}
            skipped = []
            yr = year
            for fn in sorted(os.listdir(cdir)):
                if not fn.lower().endswith(".pdf"):
                    continue
                stmt, is_chuki, nm = _classify(fn)
                if is_chuki:
                    skipped.append((fn, "注記"))
                    continue
                if stmt is None:
                    skipped.append((fn, f"対象外様式({nm})"))
                    continue
                paths[stmt] = os.path.join(cdir, fn)
                yr = _year_from_name(fn) or year
            if paths:
                jobs.append({"year": yr, "corp_dir": cdir, "corp_name": corp,
                             "paths": paths, "skipped": skipped})
    return jobs


def _guess_corp_no(corp_name, corp_dir):
    """ディレクトリ名が法人番号(13桁)ならそれを、なければ法人名のハッシュ簡易IDを返す。
    実運用では法人マスタ突合に差し替える前提のプレースホルダ。"""
    base = os.path.basename(corp_dir.rstrip("/"))
    if re.fullmatch(r"\d{13}", base):
        return base
    m = re.search(r"\d{13}", corp_name)
    if m:
        return m.group(0)
    # フォールバック: 名前から決定的な擬似ID
    import hashlib
    h = hashlib.sha1(corp_name.encode("utf-8")).hexdigest()[:13]
    return f"X{h[:12]}"


def cmd_scan(args):
    from shafuku_db_engine import masters
    jobs = scan_tree(args.root)
    if not jobs:
        print("対象PDFが見つかりませんでした。")
        return
    print(f"== {len(jobs)} 法人を検出 ==")
    summaries = []
    for job in jobs:
        corp_no = args.corp_no or _guess_corp_no(job["corp_name"], job["corp_dir"])
        outdir = os.path.join(args.out, corp_no)
        print(f"\n--- {job['corp_name']} (corp_no={corp_no}, year={job['year']}) ---")
        for fn, why in job["skipped"]:
            print(f"   skip: {fn} ({why})")
        s = run_kyoten4.run_corp(
            job["paths"], corp_no, job["corp_name"], job["year"],
            masters, outdir)
        summaries.append(s)
        _print_summary(s)
    print("\n== 全法人完了 ==")
    tot_fact = sum(s["fact_rows"] for s in summaries)
    tot_ng = sum(s["ng_total"] for s in summaries)
    print(f"合計 fact={tot_fact} / NG={tot_ng} / 法人={len(summaries)}")


def cmd_one(args):
    from shafuku_db_engine import masters
    paths = {}
    if args.cf:
        paths["CF"] = args.cf
    if args.pl:
        paths["PL"] = args.pl
    if args.bs:
        paths["BS"] = args.bs
    if not paths:
        raise SystemExit("--cf/--pl/--bs のいずれかを指定してください")
    s = run_kyoten4.run_corp(paths, args.corp_no, args.corp_name, args.year,
                             masters, args.out)
    _print_summary(s)


def _print_summary(s):
    print(f"   fact={s['fact_rows']} concepts={s['concepts']} "
          f"locals={s['locals']} review={s['review_items']} NG={s['ng_total']}")
    if s["quarantined_slices"]:
        print(f"   隔離: {s['quarantined_slices']}")
    if s["global_ng"]:
        print(f"   全体NG: {s['global_ng']}")
    print(f"   CSV出力先: {os.path.dirname(s['csv_paths']['fact_financial'])}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="shafuku_parser.cli_kyoten4")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="root/<年度>/<法人>/ を走査して一括取り込み")
    ps.add_argument("root")
    ps.add_argument("--out", required=True)
    ps.add_argument("--corp-no", default=None, help="全法人共通の法人番号を強制指定（任意）")
    ps.set_defaults(func=cmd_scan)

    po = sub.add_parser("one", help="1法人ぶんを直接指定して取り込み")
    po.add_argument("--cf")
    po.add_argument("--pl")
    po.add_argument("--bs")
    po.add_argument("--corp-no", required=True)
    po.add_argument("--corp-name", required=True)
    po.add_argument("--year", required=True)
    po.add_argument("--out", required=True)
    po.set_defaults(func=cmd_one)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
