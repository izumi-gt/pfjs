# -*- coding: utf-8 -*-
"""
タイプ3 無人完走ランナー（adapter_segwari3 → 検算 → 隔離 → CSV出力）。

run_kyoten4 と同じ流れだが、タイプ3固有の事情に対応する:

  (A) local の dim 行注入:
      engine.ingest は明細様式(1-4/2-4)に対してのみ build_local_for を回し add_local する。
      タイプ3(1-3/2-3/3-3)は emit_kyoten 経由で、ingest 中に add_local を呼ばない。
      そのため adapter_segwari3 が生成した local 行/概念行
      (corp._segwari3_locals / corp._segwari3_concepts) を、ingest 後に Ingested へ注入する。

  (B) 隔離の粒度:
      タイプ3は {code:([拠点値...],合計,消去,区分計)} 形で、拠点が全コード横断のため
      「特定拠点だけ除去」は構造上できない。NG が出た様式は様式単位で隔離する
      （あしたかは全 green のため発火しないが、無人完走のため実装しておく）。
"""
import os
import re

from .adapter_segwari3 import build_corpdata
from .registry import REVIEW_HEADER
from .run_kyoten4 import (_write_csv, _global_account_rows, _dedup_concepts)


_NG_FORM_RE = re.compile(r"^(CF|PL|BS)\s+(1-3|2-3|3-3)\b")


def _ng_forms(ng_list):
    """NG文字列群 → 隔離対象の様式 {(stmt, form)} と全体NG群。"""
    forms = set()
    globals_ng = []
    for s in ng_list:
        m = _NG_FORM_RE.match(s)
        if m:
            forms.add((m.group(1), m.group(2)))
        else:
            globals_ng.append(s)
    return forms, globals_ng


def _quarantine_forms(corp, forms):
    """corp から (stmt, form) 様式を丸ごと除去（拠点単位の除去は構造上不可のため）。"""
    import copy
    c = copy.deepcopy(corp)
    quarantined = []
    cont = {"CF": c.cf, "PL": c.pl, "BS": c.bs}
    for stmt, form in forms:
        if form in cont[stmt]:
            quarantined.append((stmt, form))
            del cont[stmt][form]
    return c, quarantined


def run_corp(pdf_paths, corp_no, corp_name, fiscal_year, masters, outdir,
             address="", main_business="", seg2_order=None,
             stop_on_global_ng=True):
    """1法人タイプ3分(1-3/2-3/3-3)を無人完走し CSV を outdir に出力。"""
    from shafuku_db_engine.ingest import ingest
    from shafuku_db_engine.validate import validate
    from shafuku_db_engine import schema

    os.makedirs(outdir, exist_ok=True)

    corp, regs, review = build_corpdata(
        pdf_paths, corp_no, corp_name, fiscal_year, masters,
        address=address, main_business=main_business, seg2_order=seg2_order)

    ng = validate(corp)
    forms, globals_ng = _ng_forms(ng)
    filtered, quarantined = _quarantine_forms(corp, forms)
    blocked = bool(globals_ng) and stop_on_global_ng

    ing = ingest(filtered)

    # ---- (A) local 行/概念行を注入（隔離された様式の分は除外）----
    stmt_of_form = {"1-3": "CF", "2-3": "PL", "3-3": "BS"}
    q_stmts = {stmt_of_form[f] for (_s, f) in quarantined if f in stmt_of_form}
    inj_locals = list(getattr(corp, "_segwari3_locals", []))
    inj_concepts = list(getattr(corp, "_segwari3_concepts", []))
    if q_stmts:
        inj_concepts = [r for r in inj_concepts if r[1] not in q_stmts]
        keep_codes = {r[0] for r in inj_concepts}
        inj_locals = [r for r in inj_locals if (not r[5]) or r[5] in keep_codes]
    have_local = {r[0] for r in ing.locals}
    for r in inj_locals:
        if r[0] not in have_local:
            ing.locals.append(r); have_local.add(r[0])
    have_concept = {r[0] for r in ing.concepts}
    for r in inj_concepts:
        if r[0] not in have_concept:
            ing.concepts.append(r); have_concept.add(r[0])

    # ---- CSV 出力 ----
    base = corp_no
    paths = {}

    def out(name):
        p = os.path.join(outdir, f"{base}_{name}.csv")
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
    _write_csv(out("quarantine"), ["計算書", "様式", "NG理由"],
               _quarantine_rows(quarantined, ng))

    return {
        "corp_no": corp_no,
        "corp_name": corp_name,
        "fact_rows": len(ing.fact),
        "concepts": len(_dedup_concepts(ing.concepts)),
        "locals": len(ing.locals),
        "segments": len(ing.segments),
        "review_items": len(review),
        "ng_total": len(ng),
        "quarantined_forms": quarantined,
        "global_ng": globals_ng,
        "blocked": blocked,
        "csv_paths": paths,
    }


def _quarantine_rows(quarantined, ng_list):
    rows = []
    for (stmt, form) in quarantined:
        reasons = [s for s in ng_list if s.startswith(f"{stmt} {form}")]
        rows.append([stmt, form, " / ".join(reasons) if reasons else ""])
    return rows
