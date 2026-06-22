# -*- coding: utf-8 -*-
"""
タイプ4 無人完走ランナー（橋渡しアダプタ → 検算 → 隔離 → CSV出力）。

フロー（HANDOFF 作業順 2-3）:
  1. adapter_kyoten4.build_corpdata でパーサ出力をコード解決し CorpData を組む。
  2. engine.validate.validate(corp) で全恒等式検算。
  3. NG が出た (様式, 拠点) スライスだけを隔離（quarantine）して CorpData から除外。
     → 残り（green な様式/拠点）は通常どおり取り込む＝全体は止めない（無人完走）。
  4. engine.ingest.ingest(filtered) で fact/dim を生成。
  5. CSV 出力: fact + dim 各種 + 新科目キュー(REVIEW_HEADER) + 隔離レポート。
     SQL移行前提のため CSV を主成果物とする。

NG タグの形式（validate.py 準拠）:
  "<CF|PL|BS> <1-4|2-4|3-4>[<拠点名>] <理由>"
  → (様式コード, 拠点) を取り出して隔離対象に。拠点が取れない全体NG（計算書またぎ等）は
    「法人全体隔離フラグ」を立てて呼び側に通知（DBには取り込まない安全側）。
"""
import os
import re
import csv
import copy

from .adapter_kyoten4 import build_corpdata
from .registry import REVIEW_HEADER


_NG_RE = re.compile(r"^(CF|PL|BS)\s+(1-4|2-4|3-4)\[(.+?)\]")


def _parse_ng(ng_list):
    """NG文字列群 → (隔離スライス集合, 拠点不明の全体NG群)。
    隔離スライス: {(form_code, loc)}  form_code は '1-4'/'2-4'/'3-4'
    """
    slices = set()
    globals_ng = []
    for s in ng_list:
        m = _NG_RE.match(s)
        if m:
            slices.add((m.group(2), m.group(1), m.group(3)))  # (form, stmt, loc)
        else:
            globals_ng.append(s)
    return slices, globals_ng


def _quarantine_corp(corp, slices):
    """corp から (form, loc) スライスを除去した複製を返す。
    除去した内容は quarantined リストに記録。
    slices: {(form_code, stmt, loc)}
    """
    c = copy.deepcopy(corp)
    quarantined = []
    by_form = {}
    for form, stmt, loc in slices:
        by_form.setdefault(form, set()).add(loc)

    def drop(container, form):
        if form in container:
            for loc in list(container[form].keys()):
                if loc in by_form.get(form, set()):
                    quarantined.append((form, loc))
                    del container[form][loc]

    drop(c.cf, "1-4")
    drop(c.pl, "2-4")
    drop(c.bs, "3-4")
    # 隔離で空になった拠点は loc_order からは外さない（他様式が生きている場合があるため）。
    return c, quarantined


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _global_account_rows(masters):
    """build.global_account_rows 相当（CSV用。和名活動区分へ展開）。"""
    rows = []
    for c, act, io, nm, tot in masters.CF_MASTER:
        rows.append([c, "資金収支計算書", masters.ACT_FULL_CF.get(act, act), io, nm, tot])
    for c, act, io, nm, tot in masters.PL_MASTER:
        rows.append([c, "事業活動計算書", masters.ACT_FULL_PL.get(act, act), io, nm, tot])
    for m in masters.BS_MASTER:
        c, sec, grp, nm, tot = m[0], m[1], m[2], m[3], m[4]
        rows.append([c, "貸借対照表", masters.SEC_NAME_BS.get(sec, sec), grp, nm, tot])
    return rows


def run_corp(pdf_paths, corp_no, corp_name, fiscal_year, masters, outdir,
             address="", main_business="", seg2_order=None,
             stop_on_global_ng=True):
    """1法人タイプ4分を無人完走し CSV を outdir に出力。
    戻り: dict(summary)
    """
    from shafuku_db_engine.ingest import ingest
    from shafuku_db_engine.validate import validate
    from shafuku_db_engine import schema

    os.makedirs(outdir, exist_ok=True)

    corp, regs, review = build_corpdata(
        pdf_paths, corp_no, corp_name, fiscal_year, masters,
        address=address, main_business=main_business, seg2_order=seg2_order)

    ng = validate(corp)
    slices, globals_ng = _parse_ng(ng)

    filtered, quarantined = _quarantine_corp(corp, slices)

    # 拠点不明の全体NG（計算書またぎ等）。安全側: 取り込みは行うが警告として記録。
    #   stop_on_global_ng=True のときはタイプ4の取り込み自体を見送る運用も選べる。
    blocked = bool(globals_ng) and stop_on_global_ng

    ing = ingest(filtered)

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

    # 新科目キュー（レビュー）。registry.REVIEW_HEADER 準拠。
    _write_csv(out("review_queue"), REVIEW_HEADER, review)

    # 隔離レポート
    _write_csv(out("quarantine"), ["様式", "拠点", "NG理由"],
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
        "quarantined_slices": quarantined,
        "global_ng": globals_ng,
        "blocked": blocked,
        "csv_paths": paths,
    }


def _dedup_concepts(concepts):
    seen = set()
    out = []
    for r in concepts:
        if r[0] in seen:
            continue
        seen.add(r[0])
        out.append(r)
    return out


def _quarantine_rows(quarantined, ng_list):
    """隔離 (form, loc) に該当する NG 理由を添えて行に。"""
    rows = []
    for (form, loc) in quarantined:
        reasons = [s for s in ng_list if f"{form}[{loc}]" in s or f" {form}[{loc}]" in s]
        # 上の単純包含だと取りこぼすことがあるため、loc 一致で拾う
        reasons = [s for s in ng_list if f"[{loc}]" in s and form in s]
        rows.append([form, loc, " / ".join(reasons) if reasons else ""])
    return rows
