# -*- coding: utf-8 -*-
"""
橋渡しアダプタ（タイプ3 → engine CorpData の 1-3 / 2-3 / 3-3）。

役割（3層分離の「配線」担当）:
  パーサ出力 extract_segwari3(pdf) のブロック列
      [{kubun, colnames, rows:[(name, depth, {col_index: value})]}, ...]
  を、レジストリ registry.Registry でコード解決し、エンジン engine.ingest が受ける
  「拠点別」形 corp.cf/pl/bs["1-3"等] = { code: ([拠点値...], 合計, 内部消去, 事業区分計) }
  へ組み立てる。

設計上の要点（タイプ3固有・タイプ4と異なる点）:
  1) statement 引数が無い。CF/PL/BS の判別は呼び側がファイル種別で決め、Registry へ渡す
     master/statement を選ぶ。
  2) 戻り値は「事業区分（kubun）ごとのブロック」。あしたかは 社会福祉事業区分(8拠点) と
     公益事業区分(3拠点) の2ブロック。拠点はブロック間で互いに素。
     → engine の emit_kyoten は「1様式=1拠点並び＋単一の合計/消去/区分計」を前提とするため、
       両ブロックを「11拠点の単一行」へ統合する。拠点並び＝block順に連結（社福8→公益3）。
       これはタイプ4(拠点明細)の拠点順と一致するので相互突合が効く。
       合計/内部消去/事業区分計 は block ごとの値を「総和」する。
       （各ブロックで Σ拠点=合計・合計-消去=区分計 が成り立つので、総和後も両恒等式が成立。）
  3) colnames の末尾3列が 合計/内部取引消去/事業区分合計(計)。先頭〜末尾-3 が拠点列。
     先頭が本部拠点（法人名融合）の法人は _strip_corp_prefix_for_honbu で剥がす。
  4) depth:
       1-3/2-3(CF/PL) は全 depth0。
       3-3(BS) は depth0(大区分) ＋ depth1(小区分)。
       depth1 は resolve_global で解決できるものは幹コード、できないもの
       （事業区分間/拠点区分間の借入金・貸付金など）は resolve_local で local 採番。
     emit_kyoten は flat（depth概念なし）なので、解決後のコードを同一 dict に flat 格納する。

io（収支区分）は親幹stemコードの master 属性から導出（_build_io_map を流用）。
local の親:
  depth1 が local の場合、親は直近 depth0 の幹stemコード。
  local_codes[(loc, 親stem, name)] = {code, concept, io} を各拠点について登録する
  （emit_kyoten 自体は local_codes を参照しないが、dim_account_concept 生成のため
    ingest の add_local 経路に乗せる必要がある→下記「local登録の方針」参照）。

local登録の方針（重要）:
  タイプ3は emit_kyoten 経由で、ingest は corp.bs["3-3"] の code をそのまま fact に出す。
  ingest は 3-3 に対して add_local を呼ばない（明細様式 1-4/2-4 のみ build_local_for を回す）。
  そのため local コードの dim_account_local / dim_account_concept 行は、このアダプタが
  ingest 後に注入する必要がある。→ build_corpdata は local 行/概念行を別途返し、
  ランナー側（or ここで）Ingested へ足す。本アダプタでは corp に
  `corp._segwari3_locals` / `corp._segwari3_concepts` を持たせて受け渡す。
"""
import re
import pdfplumber

from .extract_segwari3 import extract_segwari3
from .registry import Registry
from . import naming
from .adapter_kyoten4 import _build_io_map, _strip_corp_prefix_for_honbu


_FORM_OF = {"CF": "1-3", "PL": "2-3", "BS": "3-3"}
_FULLFORM = {"CF": "CF-1-3", "PL": "PL-2-3", "BS": "BS-3-3"}


def _statement_master(statement, masters):
    return {"CF": masters.CF_MASTER, "PL": masters.PL_MASTER,
            "BS": masters.BS_MASTER}[statement]


def _convert_statement(blocks, statement, reg, io_map, corp_name, masters):
    """1計算書ぶん（CF/PL/BS）のブロック列を engine の拠点別形へ。
    戻り:
      data        : { code: ([拠点値...11], 合計, 内部消去, 事業区分計) }
      loc_order   : 拠点名の並び（block順連結, 本部拠点名は剥がし済み）
      local_rows  : dim_account_local 追加行 [code, corp, parent, name, depth, concept]
      concept_rows: dim_account_concept 追加行 [concept, stmt, parent, io, name]
    """
    form_full = _FULLFORM[statement]
    # ---- 拠点並びの確定（block順に連結）----
    block_locs = []
    for b in blocks:
        locs = [_strip_corp_prefix_for_honbu(n, corp_name)
                for n in b["colnames"][:-3]]   # 末尾3列(合計/消去/区分計)を除く
        block_locs.append(locs)
    loc_order = [l for locs in block_locs for l in locs]
    loc_index = {l: i for i, l in enumerate(loc_order)}
    nloc_total = len(loc_order)

    # code -> 集計バッファ
    agg = {}     # code -> {"locs": [None]*nloc_total, "total":0/None, "elim":..., "segt":...}
    local_rows = []
    concept_rows = []
    concept_seen = set()
    local_seen = {}     # (loc, parent, name) -> code  （拠点ごとに同一概念→同一実体）

    def ensure_slot(code):
        if code not in agg:
            agg[code] = {"locs": [None] * nloc_total,
                         "total": None, "elim": None, "segt": None}
        return agg[code]

    def add_val(slot, idx, v):
        if v is None:
            return
        if slot["locs"][idx] is None:
            slot["locs"][idx] = v
        else:
            slot["locs"][idx] += v

    def add_agg(slot, key, v):
        if v is None:
            return
        slot[key] = (slot[key] or 0) + v

    for bi, b in enumerate(blocks):
        ncol = len(b["colnames"])
        nloc = ncol - 3
        i_total, i_elim, i_segt = nloc, nloc + 1, nloc + 2
        base_idx = sum(len(block_locs[k]) for k in range(bi))   # この block の拠点が
                                                                # loc_order で始まる位置
        cur_stem = None
        cur_stem_io = ""
        for (name, depth, vals) in b["rows"]:
            if depth == 0:
                code = reg.resolve_global(name)
                if code is None:
                    # 稀: 未知の大区分/計。local 採番（親なし）。
                    ic, cc, _ = reg.resolve_local(name, "", "", form_full, 0)
                    code = ic
                    cur_stem, cur_stem_io = None, ""
                else:
                    cur_stem = code
                    cur_stem_io = io_map.get(code, "")
            else:
                # depth1（BSの小区分）。タイプ4のBSアダプタと同様、全て resolve_local で
                # 親(直近depth0の幹stem)配下の local コードに採番する。
                #   理由: 同名の小区分が複数の中区分に出る（例:「建物」が 基本財産 と
                #   その他の固定資産 の両方、「土地」「定期預金」も同様）。resolve_global は
                #   指紋が「正規化名＋計算書」のみで中区分を区別しないため、これらを同一
                #   コードへ衝突させてしまう。local は親stemで区別できるので衝突しない。
                parent = cur_stem if cur_stem else ""
                io = cur_stem_io
                ic, cc, new_concept = reg.resolve_local(
                    name, parent, io, form_full, 0)
                code = ic
                # local 行/概念行を登録（emit_kyoten は local_codes を参照しないため別途）。
                #   親stem＋名でコードが決まるので、同コードは1行だけ dim に出す。
                if cc and cc not in concept_seen:
                    concept_seen.add(cc)
                    concept_rows.append(
                        [cc, statement, parent, io, name])
                if ic not in local_seen:
                    local_seen[ic] = True
                    local_rows.append([ic, reg.corp_no, parent, name, 1, cc])
            slot = ensure_slot(code)
            # 拠点値
            for j in range(nloc):
                add_val(slot, base_idx + j, vals.get(j))
            # 合計/消去/区分計（block総和）
            add_agg(slot, "total", vals.get(i_total))
            add_agg(slot, "elim", vals.get(i_elim))
            add_agg(slot, "segt", vals.get(i_segt))

    data = {code: (s["locs"], s["total"], s["elim"], s["segt"])
            for code, s in agg.items()}
    return data, loc_order, local_rows, concept_rows


def build_corpdata(pdf_paths, corp_no, corp_name, fiscal_year, masters,
                   address="", main_business="", seg2_order=None):
    """
    pdf_paths: {"CF": path|None, "PL": path|None, "BS": path|None}（1-3/2-3/3-3）
    戻り: (CorpData, registries, review_rows)
    CorpData には _segwari3_locals / _segwari3_concepts を付与（ランナーが Ingested へ注入）。
    """
    from shafuku_db_engine.ingest import CorpData

    io_map = _build_io_map(masters.CF_MASTER, masters.PL_MASTER, masters.BS_MASTER)
    regs = {}
    cf, pl, bs = {}, {}, {}
    kyoten_loc = {}
    all_local_rows = []
    all_concept_rows = []
    loc_order_ref = None

    for stmt in ("CF", "PL", "BS"):
        path = pdf_paths.get(stmt)
        if not path:
            continue
        reg = Registry(_statement_master(stmt, masters), stmt, corp_no)
        regs[stmt] = reg
        with pdfplumber.open(path) as pdf:
            blocks = extract_segwari3(pdf)
        data, loc_order, local_rows, concept_rows = _convert_statement(
            blocks, stmt, reg, io_map, corp_name, masters)
        form = _FORM_OF[stmt]
        full = _FULLFORM[stmt]
        (cf if stmt == "CF" else pl if stmt == "PL" else bs)[form] = data
        kyoten_loc[full] = loc_order
        all_local_rows.extend(local_rows)
        all_concept_rows.extend(concept_rows)
        if loc_order_ref is None:
            loc_order_ref = loc_order

    corp = CorpData(
        corp_no=corp_no, corp_name=corp_name, fiscal_year=fiscal_year,
        address=address, main_business=main_business,
        n_locations=len(loc_order_ref or []), loc_order=loc_order_ref or [],
        seg2_order=seg2_order, cf=cf, pl=pl, bs=bs)
    corp.kyoten_loc = kyoten_loc
    # local/concept 行はアダプタ生成。ingest は 3-3 で add_local を回さないため別途注入する。
    corp._segwari3_locals = all_local_rows
    corp._segwari3_concepts = all_concept_rows

    review_rows = []
    for reg in regs.values():
        for it in reg.review:
            review_rows.append(it.row())

    return corp, regs, review_rows
