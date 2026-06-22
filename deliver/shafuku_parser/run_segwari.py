# -*- coding: utf-8 -*-
"""
タイプ2 無人完走ランナー（事業区分別内訳表 → 検算 → CSV出力）。

フロー（タイプ4ランナーと同じ思想）:
  1. adapter_segwari.build_corpdata_segwari で CorpData を組む。
  2. engine.validate で全恒等式検算（区分和=合計 / 合計-消去=法人合計 / 各区分の縦計・連鎖・残高）。
  3. NG が出た様式は隔離して全体は止めない（無人完走）。タイプ2は1様式=1法人単位の表なので
     スライス粒度は「様式まるごと」。NG様式は CorpData から外し quarantine に記録。
  4. engine.ingest で fact/dim を生成。
  5. CSV 出力（タイプ4ランナーのヘルパを流用）: fact + dim各種 + review_queue + quarantine + held。
"""
import os
import re

from .adapter_segwari import build_corpdata_segwari
from .registry import REVIEW_HEADER
from . import run_kyoten4 as _r4   # CSV/ヘルパ流用

_NG_RE = re.compile(r"^(CF|PL|BS)\s+(1-2|2-2|3-2)")


def _ng_forms(ng_list):
    forms = set()
    others = []
    for s in ng_list:
        m = _NG_RE.match(s)
        if m:
            forms.add((m.group(1), m.group(2)))
        else:
            others.append(s)
    return forms, others


def run_corp_segwari(pdf_paths, corp_no, corp_name, fiscal_year, masters, outdir,
                     address="", main_business="", seg2_order=None, loc_order=None):
    from shafuku_db_engine.ingest import ingest
    from shafuku_db_engine.validate import validate
    from shafuku_db_engine import schema

    os.makedirs(outdir, exist_ok=True)

    corp, regs, review, held = build_corpdata_segwari(
        pdf_paths, corp_no, corp_name, fiscal_year, masters,
        address=address, main_business=main_business,
        seg2_order=seg2_order, loc_order=loc_order)

    ng = validate(corp)
    ng_forms, others = _ng_forms(ng)

    quarantined = []
    form_key = {"CF": "1-2", "PL": "2-2", "BS": "3-2"}
    for stmt, fk in form_key.items():
        if (stmt, fk) in ng_forms:
            container = {"CF": corp.cf, "PL": corp.pl, "BS": corp.bs}[stmt]
            if fk in container:
                del container[fk]
                quarantined.append((fk, [s for s in ng if s.startswith(f"{stmt} {fk}")]))

    ing = ingest(corp)

    base = corp_no
    paths = {}

    def out(name):
        p = os.path.join(outdir, f"{base}_{name}.csv")
        paths[name] = p
        return p

    _r4._write_csv(out("fact_financial"), schema.TABLES["fact_financial"], ing.fact)
    _r4._write_csv(out("dim_corp"), schema.TABLES["dim_corp"], [ing.corp_row])
    _r4._write_csv(out("dim_segment"), schema.TABLES["dim_segment"], ing.segments)
    _r4._write_csv(out("dim_account_local"), schema.TABLES["dim_account_local"], ing.locals)
    _r4._write_csv(out("dim_account_concept"), schema.TABLES["dim_account_concept"],
                   _r4._dedup_concepts(ing.concepts))
    _r4._write_csv(out("dim_account_global"), schema.TABLES["dim_account_global"],
                   _r4._global_account_rows(masters))
    _r4._write_csv(out("dim_form"), schema.TABLES["dim_form"], [list(f) for f in schema.FORMS])
    _r4._write_csv(out("review_queue"), REVIEW_HEADER, review)
    _r4._write_csv(out("quarantine"), ["様式", "NG理由"],
                   [[fk, " / ".join(rs)] for fk, rs in quarantined])
    # held: 未知科目（fact非載録・要レビュー）。値が非ゼロなら人手で master 採番のこと。
    _r4._write_csv(out("held_unresolved"),
                   ["様式", "科目名", "社福", "公益", "収益", "合計", "内部消去", "法人合計",
                    "暫定実体コード", "暫定概念コード"],
                   [[f, nm, *[("" if v is None else v) for v in m], ic, cc]
                    for (f, nm, m, ic, cc) in held])

    return {
        "corp_no": corp_no, "corp_name": corp_name,
        "fact_rows": len(ing.fact),
        "concepts": len(_r4._dedup_concepts(ing.concepts)),
        "locals": len(ing.locals), "segments": len(ing.segments),
        "review_items": len(review), "held": len(held),
        "ng_total": len(ng), "quarantined": quarantined, "other_ng": others,
        "csv_paths": paths,
    }
