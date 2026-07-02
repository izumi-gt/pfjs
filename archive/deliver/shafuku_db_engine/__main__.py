# -*- coding: utf-8 -*-
"""
CLI: 検算してから取り込む。検算で不一致が出たら中止（DBを汚さない）。

例:
  python -m shafuku_db_engine ingest corps.kanagawa --out 決算情報データベース.xlsx
  python -m shafuku_db_engine ingest corps.kanagawa --append db.xlsx --out db.xlsx
  python -m shafuku_db_engine validate corps.kanagawa
"""
import argparse
import importlib
import sys

from .validate import validate
from .ingest import ingest
from .build import build


def _load_corp(modpath):
    # modpath 例: "corps.kanagawa" または "shafuku_db_engine.corps.kanagawa"
    for cand in (modpath, f"shafuku_db_engine.{modpath}"):
        try:
            mod = importlib.import_module(cand)
            return mod.CORP
        except ModuleNotFoundError:
            continue
    raise SystemExit(f"法人モジュールが見つかりません: {modpath}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="shafuku_db_engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("validate", help="検算のみ")
    pv.add_argument("corp")

    pi = sub.add_parser("ingest", help="検算→取り込み→Excel出力")
    pi.add_argument("corp")
    pi.add_argument("--out", required=True)
    pi.add_argument("--append", default=None, help="既存DBに追記")
    pi.add_argument("--force", action="store_true", help="検算不一致でも続行(非推奨)")

    a = p.parse_args(argv)
    corp = _load_corp(a.corp)

    problems = validate(corp)
    if problems:
        print(f"❌ 検算で {len(problems)} 件の不一致:")
        for x in problems[:50]:
            print("  -", x)
        if a.cmd == "validate":
            return 1
        if not getattr(a, "force", False):
            print("取り込みを中止しました（--force で強制可）。")
            return 1
    else:
        print("✅ 検算オールパス（全恒等式・様式間突合・計算書またぎ）")

    if a.cmd == "validate":
        return 0

    ing = ingest(corp)
    post, nfact = build([ing], a.out, append_to=a.append)
    if post:
        print(f"❌ 出力後チェックで {len(post)} 件:")
        for x in post[:50]:
            print("  -", x)
        return 1
    print(f"✅ 出力完了: {a.out}  fact {nfact} 行 / FK・主キー重複 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
