# -*- coding: utf-8 -*-
"""
タイプ1 法人単位 無人完走ランナー（アダプタ → 検算 → 隔離 → CSV出力）。

run_kyoten4 の CSV ヘルパを流用し、タイプ1向けに薄く実装する。
タイプ1は拠点・事業区分が無く、NG が出たら当該様式（1-1/2-1/3-1）単位で隔離する。
"""
import os
import re

from .adapter_houjin import build_corpdata
from .registry import REVIEW_HEADER
from .run_kyoten4 import _write_csv, _global_account_rows, _dedup_concepts

# NGタグ "CF 1-1[...] 理由" / "PL 2-1 ..." / "BS 3-1 ..." から様式コードを拾う
_NG_FORM_RE = re.compile(r"\b(1-1|2-1|3-1)\b")


def _ng_forms(ng_list):
    forms = set()
    others = []
    for s in ng_list:
        m = _NG_FORM_RE.search(s)
        if m:
            forms.add(m.group(1))
        else:
            others.append(s)
    return forms, others


def run_corp(pdf_paths, corp_no, corp_name, fiscal_year, masters, outdir,
             address="", main_business=""):
    from shafuku_db_engine.ingest import ingest
    from shafuku_db_engine.validate import validate
    from shafuku_db_engine import schema

    os.makedirs(outdir, exist_ok=True)

    corp, regs, review, warnings = build_corpdata(
        pdf_paths, corp_no, corp_name, fiscal_year, masters,
        address=address, main_business=main_business)

    ng = validate(corp)
    ng_forms, other_ng = _ng_forms(ng)

    # 隔離: NG の出た様式を CorpData から落とす（全体は止めない）
    quarantined = []
    for form in list(ng_forms):
        for container in (corp.cf, corp.pl, corp.bs):
            if form in container:
                quarantined.append(form)
                del container[form]

    ing = ingest(corp)

    base = corp_no
    paths = {}

    def out(name):
        p = os.path.join(outdir, f"{base}_houjin_{name}.csv")
        paths[name] = p
        return p

    _write_csv(out("fact_financial"), schema.TABLES["fact_financial"], ing.fact)
    _write_csv(out("dim_corp"), schema.TABLES["dim_corp"], [ing.corp_row])
    _write_csv(out("dim_segment"), schema.TABLES["dim_segment"], ing.segments)
    _write_csv(out("dim_account_local"), schema.TABLES["dim_account_local"], ing.locals)
    _write_csv(out("dim_account_concept"), schema.TABLES["dim_account_concept"],
               _dedup_concepts(ing.concepts))
    _write_csv(out("dim_account_global"), schema.TABLES["dim_account_global"],
               _global_account_rows(masters))
    _write_csv(out("dim_form"), schema.TABLES["dim_form"], [list(f) for f in schema.FORMS])
    _write_csv(out("review_queue"), REVIEW_HEADER, review)
    _write_csv(out("quarantine"), ["様式", "NG理由"],
               [[f, " / ".join(s for s in ng if f in s)] for f in quarantined])
    # パーサ警告（名前破損疑い等）も残す
    _write_csv(out("parser_warnings"), ["様式", "元名称", "値"],
               [[w[0], w[1], str(w[2])] for w in warnings])

    return {
        "corp_no": corp_no,
        "corp_name": corp_name,
        "fact_rows": len(ing.fact),
        "concepts": len(_dedup_concepts(ing.concepts)),
        "review_items": len(review),
        "ng_total": len(ng),
        "quarantined_forms": quarantined,
        "other_ng": other_ng,
        "warnings": warnings,
        "csv_paths": paths,
    }
