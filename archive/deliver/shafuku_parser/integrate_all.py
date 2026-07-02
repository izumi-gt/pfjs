# -*- coding: utf-8 -*-
"""
全12様式 統合オーケストレータ（案1: タイプ別 Ingested を束ねて統合DB化）。

方針（本体チャットで合意した案1）:
  - タイプ1(houjin)/2(segwari)/3(segwari3)/4(kyoten4) の各アダプタを実行し、
    それぞれ engine.ingest で Ingested を得る（タイプ3は local 行を ingest 後に注入）。
  - 4タイプの fact / dim を「コードで重複排除しつつ」1つに統合する。
    * fact: 様式コードがタイプ別に異なる（1-1 vs 1-4 等）ため PK衝突しない。全件保持。
    * dim_segment / dim_account_global / dim_form / dim_corp: コードで dedup（同一コードは
      内容一致を確認済みなので1行に集約）。
    * dim_account_local / dim_account_concept: 各タイプが独立採番するため、同一実体に
      別L-/LC-コードが付く（＝マスタ管理上の重複。会計データの重複・矛盾ではない）。
      全件保持し、重複は診断レポートに記録（将来の identity 統一フェーズの証拠）。
  - cross-form 検算（↔拠点別 / ↔区分別）は「タイプ別 CorpData を個別 validate」する限り
    発火しない。統合 fact の値整合は別途 reconcile レポートで実測して残す。

診断レポート（将来フェーズの証拠蓄積。場当たりにしないための実測ログ）:
  - duplicate_local: 同一 (正規化名, 親stem, 拠点/区分) に複数 L- が付いた事例。
  - held_unresolved: master 未収載で fact に載せられなかった科目（タイプ2）。
  - parser_warnings: 値を持つのに global 未解決だった行（タイプ1）。
  - crossform_reconcile: 様式間で同一実体の値が一致するかの実測（1-3↔1-2, 3-3↔3-4 等）。
"""
import os
import csv

from shafuku_db_engine import schema, masters
from shafuku_db_engine.ingest import ingest
from shafuku_db_engine.validate import validate
from shafuku_db_engine.build import global_account_rows, post_checks

from .adapter_houjin import build_corpdata as _bc_houjin
from .adapter_segwari import build_corpdata_segwari as _bc_segwari
from .adapter_segwari3 import build_corpdata as _bc_segwari3
from .adapter_kyoten4 import build_corpdata as _bc_kyoten4
from .naming import normalize
from .run_kyoten4 import _write_csv, _dedup_concepts


# ---- ファイル名 → (タイプ, statement) ----
_FORM_STMT = {
    "1-1": ("houjin", "CF"), "2-1": ("houjin", "PL"), "3-1": ("houjin", "BS"),
    "1-2": ("segwari", "CF"), "2-2": ("segwari", "PL"), "3-2": ("segwari", "BS"),
    "1-3": ("segwari3", "CF"), "2-3": ("segwari3", "PL"), "3-3": ("segwari3", "BS"),
    "1-4": ("kyoten4", "CF"), "2-4": ("kyoten4", "PL"), "3-4": ("kyoten4", "BS"),
}


def _ingest_with_segwari3_locals(corp):
    """segwari3 の corp は local/concept を _segwari3_* に持つ。ingest 後に注入。"""
    ing = ingest(corp)
    inj_locals = list(getattr(corp, "_segwari3_locals", []) or [])
    inj_concepts = list(getattr(corp, "_segwari3_concepts", []) or [])
    have_l = {r[0] for r in ing.locals}
    for r in inj_locals:
        if r[0] not in have_l:
            ing.locals.append(r); have_l.add(r[0])
    have_c = {r[0] for r in ing.concepts}
    for r in inj_concepts:
        if r[0] not in have_c:
            ing.concepts.append(r); have_c.add(r[0])
    return ing


def run_corp_all(pdf_paths, corp_no, corp_name, fiscal_year, outdir,
                 address="", main_business=""):
    """
    pdf_paths: {"1-1": path, "2-1": path, ..., "3-4": path}  存在する様式だけでよい。
    4タイプを実行→個別ingest→統合→CSV/診断出力。
    戻り: summary dict
    """
    os.makedirs(outdir, exist_ok=True)

    # タイプごとに pdf_paths を仕分け
    by_type = {"houjin": {}, "segwari": {}, "segwari3": {}, "kyoten4": {}}
    for form, p in pdf_paths.items():
        if form not in _FORM_STMT:
            continue
        typ, stmt = _FORM_STMT[form]
        by_type[typ][stmt] = p

    ingesteds = []
    per_type = {}
    held_all = []
    warn_all = []
    review_all = []
    corps = {}

    # --- タイプ1 houjin ---
    if by_type["houjin"]:
        c, regs, review, warnings = _bc_houjin(
            by_type["houjin"], corp_no, corp_name, fiscal_year, masters,
            address=address, main_business=main_business)
        corps["houjin"] = c
        ing = ingest(c)
        ingesteds.append(ing); per_type["houjin"] = ing
        warn_all += [("houjin",) + tuple(w) if isinstance(w, (list, tuple)) else ("houjin", w) for w in warnings]
        review_all += [("houjin",) + tuple(r) for r in review]

    # --- タイプ2 segwari ---
    if by_type["segwari"]:
        c, regs, review, held = _bc_segwari(
            by_type["segwari"], corp_no, corp_name, fiscal_year, masters,
            address=address, main_business=main_business)
        corps["segwari"] = c
        ing = ingest(c)
        ingesteds.append(ing); per_type["segwari"] = ing
        held_all += [("segwari",) + tuple(h) for h in held]
        review_all += [("segwari",) + tuple(r) for r in review]

    # --- タイプ3 segwari3 ---
    if by_type["segwari3"]:
        c, regs, review = _bc_segwari3(
            by_type["segwari3"], corp_no, corp_name, fiscal_year, masters,
            address=address, main_business=main_business)
        corps["segwari3"] = c
        ing = _ingest_with_segwari3_locals(c)
        ingesteds.append(ing); per_type["segwari3"] = ing
        review_all += [("segwari3",) + tuple(r) for r in review]

    # --- タイプ4 kyoten4 ---
    if by_type["kyoten4"]:
        c, regs, review = _bc_kyoten4(
            by_type["kyoten4"], corp_no, corp_name, fiscal_year, masters,
            address=address, main_business=main_business)
        corps["kyoten4"] = c
        ing = ingest(c)
        ingesteds.append(ing); per_type["kyoten4"] = ing
        review_all += [("kyoten4",) + tuple(r) for r in review]

    # --- 個別 validate（タイプ別 NG。cross-form は発火しない）---
    per_type_ng = {}
    for typ, c in corps.items():
        per_type_ng[typ] = validate(c)

    # --- 統合（dedup）---
    facts = []
    seg_by_code = {}
    local_by_code = {}
    concept_by_code = {}
    corp_row = None
    for ing in ingesteds:
        facts.extend(ing.fact)
        for r in ing.segments:
            seg_by_code.setdefault(r[0], r)
        for r in ing.locals:
            local_by_code.setdefault(r[0], r)
        for r in _dedup_concepts(ing.concepts):
            concept_by_code.setdefault(r[0], r)
        if corp_row is None and ing.corp_row:
            corp_row = ing.corp_row

    seg_rows = list(seg_by_code.values())
    local_rows = list(local_by_code.values())
    concept_rows = list(concept_by_code.values())

    # --- FK/PK 事後検査（build.post_checks を流用）---
    problems = post_checks(facts, seg_rows, local_rows)

    # --- 診断: 重複 local（同一実体に複数 L-）---
    dup_rows = _diagnose_duplicate_locals(local_rows)

    # --- 診断: cross-form 値突合（実測ログ）---
    recon_rows = _diagnose_crossform(corps)

    # --- CSV 出力 ---
    paths = {}

    def out(name):
        p = os.path.join(outdir, f"{corp_no}_{name}.csv")
        paths[name] = p
        return p

    _write_csv(out("fact_financial"), schema.TABLES["fact_financial"], facts)
    _write_csv(out("dim_corp"), schema.TABLES["dim_corp"], [corp_row] if corp_row else [])
    _write_csv(out("dim_segment"), schema.TABLES["dim_segment"], seg_rows)
    _write_csv(out("dim_account_local"), schema.TABLES["dim_account_local"], local_rows)
    _write_csv(out("dim_account_concept"), schema.TABLES["dim_account_concept"], concept_rows)
    _write_csv(out("dim_account_global"), schema.TABLES["dim_account_global"], global_account_rows())
    _write_csv(out("dim_form"), schema.TABLES["dim_form"], [list(f) for f in schema.FORMS])

    # 診断レポート群
    _write_csv(out("diag_duplicate_local"),
               ["正規化名", "親stem", "セグメント", "重複コード数", "コード一覧"], dup_rows)
    _write_csv(out("diag_held_unresolved"),
               ["タイプ", "科目名", "値ほか"], [list(h) for h in held_all])
    _write_csv(out("diag_parser_warnings"),
               ["タイプ", "内容ほか"], [list(w) for w in warn_all])
    _write_csv(out("diag_crossform_reconcile"),
               ["突合", "対象", "件数", "不一致", "備考"], recon_rows)
    _write_csv(out("review_queue"),
               ["タイプ"] + ["col%d" % i for i in range(max((len(r) for r in review_all), default=1) - 1)],
               [list(r) for r in review_all])

    summary = {
        "corp_no": corp_no,
        "corp_name": corp_name,
        "types_processed": sorted(corps.keys()),
        "fact_rows": len(facts),
        "segments": len(seg_rows),
        "locals": len(local_rows),
        "concepts": len(concept_rows),
        "per_type_ng": {k: len(v) for k, v in per_type_ng.items()},
        "fkpk_problems": len(problems),
        "duplicate_local_groups": len(dup_rows),
        "held": len(held_all),
        "warnings": len(warn_all),
        "crossform_reconcile": recon_rows,
        "csv_paths": paths,
        "_problems_sample": problems[:10],
    }
    return summary


def merge_ingesteds(ingesteds, prefer_corp_row=None):
    """複数 Ingested を1つに統合（dim はコードで dedup、fact は全件、corp_row は最良1件）。
    戻り: 単一の擬似 Ingested（属性 fact/segments/locals/concepts/corp_row を持つ簡易オブジェクト）。
    """
    class _Merged:
        pass
    m = _Merged()
    m.fact = []
    seg = {}
    loc = {}
    con = {}
    corp_row = prefer_corp_row
    for ing in ingesteds:
        m.fact.extend(ing.fact)
        for r in ing.segments:
            seg.setdefault(r[0], r)
        for r in ing.locals:
            loc.setdefault(r[0], r)
        for r in _dedup_concepts(ing.concepts):
            con.setdefault(r[0], r)
        # corp_row は「拠点数が最大＝最も情報量が多い」ものを採用
        if ing.corp_row:
            if corp_row is None:
                corp_row = ing.corp_row
            else:
                try:
                    if (ing.corp_row[4] or 0) > (corp_row[4] or 0):
                        corp_row = ing.corp_row
                except (IndexError, TypeError):
                    pass
    m.segments = list(seg.values())
    m.locals = list(loc.values())
    m.concepts = list(con.values())
    m.corp_row = corp_row
    return m


def build_unified_db(corp_ingested_groups, xlsx_path):
    """複数法人の統合DBを build。
    corp_ingested_groups: [[ing, ing, ...], ...]  法人ごとに Ingested のリスト。
    各法人グループを1 Ingested に統合してから engine.build に渡す
    （dim_corp 等を法人単位で dedup し PK 重複を防ぐ）。
    """
    from shafuku_db_engine.build import build
    merged = [merge_ingesteds(g) for g in corp_ingested_groups]
    return build(merged, xlsx_path)


def _diagnose_duplicate_locals(local_rows):
    """同一実体（正規化名×親stem×セグメント由来）に複数 L- が付いた群を抽出。
    dim_account_local 行: [code, corp, parent_code, name, depth, concept]
    セグメントは local コードに含まれないため、(正規化名, 親stem) で粗くグルーピングし、
    複数コードが付く群を重複候補として報告する（拠点違いも含むため概数の指標）。"""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in local_rows:
        code, corp, parent, name, depth = r[0], r[1], r[2], r[3], r[4]
        key = (normalize(name), parent)
        groups[key].append(code)
    out = []
    for (nm, parent), codes in sorted(groups.items()):
        if len(codes) > 1:
            out.append([nm, parent, "(名×親stemで粗集計)", len(codes),
                        " ".join(sorted(codes))])
    return out


def _diagnose_crossform(corps):
    """様式間で同一実体の値が一致するかを実測してログ化（案2の証拠）。"""
    rows = []

    # (1) BS 3-3 ↔ 3-4: global-stem 値の拠点別一致
    if "segwari3" in corps and "kyoten4" in corps:
        c3, c4 = corps["segwari3"], corps["kyoten4"]
        if "3-3" in c3.bs and "3-4" in c4.bs:
            bs3, bs4 = c3.bs["3-3"], c4.bs["3-4"]
            locs = c3.loc_order
            checked = mism = 0
            for code, tup in bs3.items():
                if not code.startswith("BS-"):
                    continue
                locvals = tup[0]
                for i, loc in enumerate(locs):
                    v3 = locvals[i] if i < len(locvals) else None
                    row4 = bs4.get(loc, {})
                    if code in row4:
                        checked += 1
                        if (v3 or 0) != (row4[code][0] or 0):
                            mism += 1
            rows.append(["BS 3-3↔3-4", "global-stem×拠点", checked, mism,
                         "値一致(コードは別採番)" if mism == 0 else "要確認"])

    # (2) 1-3/2-3/3-3 区分計(ブロック別) ↔ 1-2/2-2/3-2 区分列
    #     ※ segwari3 corp は区分を統合済みのため、ここでは CorpData からは出せない。
    #        代わりに「統合後の事業区分計 == segwari 法人合計列」など取れる範囲を記録。
    if "segwari" in corps and "segwari3" in corps:
        cs, c3 = corps["segwari"], corps["segwari3"]
        for stmt, f2, f3, attr in [("CF", "1-2", "1-3", "cf"),
                                   ("PL", "2-2", "2-3", "pl"),
                                   ("BS", "3-2", "3-3", "bs")]:
            d2 = getattr(cs, attr).get(f2)
            d3 = getattr(c3, attr).get(f3)
            if not d2 or not d3:
                continue
            # 統合後 区分計(=社福+公益) と segwari 合計列(col3) を比較
            checked = mism = 0
            for code, tup in d3.items():
                if code in d2:
                    segtot = tup[3]            # 事業区分計（統合後＝全区分和）
                    gokei2 = d2[code][3]       # segwari の「合計」列(col3)
                    checked += 1
                    if (segtot or 0) != (gokei2 or 0):
                        mism += 1
            rows.append([f"{f3}↔{f2}", "区分計(統合)vs合計列", checked, mism,
                         "一致" if mism == 0 else "差分あり(区分構造の違い)"])

    # (3) houjin 法人合計 ↔ segwari 法人合計列(col5)
    if "houjin" in corps and "segwari" in corps:
        ch, cs = corps["houjin"], corps["segwari"]
        for stmt, f1, f2, attr in [("CF", "1-1", "1-2", "cf"),
                                   ("PL", "2-1", "2-2", "pl"),
                                   ("BS", "3-1", "3-2", "bs")]:
            d1 = getattr(ch, attr).get(f1)
            d2 = getattr(cs, attr).get(f2)
            if not d1 or not d2:
                continue
            # 当期実績の列: CF(1-1)は(予算,決算,差異)で決算=col1、PL/BSは(当年度,前年度,増減)で当年度=col0
            cur_col = 1 if stmt == "CF" else 0
            checked = mism = 0
            for code, vals in d1.items():
                if code in d2:
                    v1 = vals[cur_col] if cur_col < len(vals) else None
                    v2 = d2[code][5]          # segwari 法人合計列(col5)
                    checked += 1
                    if (v1 or 0) != (v2 or 0):
                        mism += 1
            rows.append([f"{f1}↔{f2}", "法人合計(当期実績)", checked, mism,
                         "一致" if mism == 0 else "差分あり"])

    return rows
