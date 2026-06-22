# -*- coding: utf-8 -*-
"""
橋渡しアダプタ（タイプ4 → engine CorpData の 1-4 / 2-4 / 3-4）。

役割（3層分離の「配線」担当）:
  パーサ出力 extract_kyoten4(pdf, statement) の同形行
      [(name, depth, {col_index: value}), ...] を、
  レジストリ registry.Registry でコードへ解決し、
  エンジン engine.ingest.CorpData が受ける形へ組み立てる。

  - depth0 → registry.resolve_global(name) で幹stemコード（既知前提）。
             未知（=新大区分/計）なら新科目候補としてレビュー（resolve_local 経由）。
  - depth1 → registry.resolve_local(name, 親幹stem, io, form, page)
             概念コード LC- ＋ 実体コード L-{法人}-NNNN を採番。
             engine 側へは行 (1, name, m0,m1,m2, 親幹stem) を渡し、
             local_codes[(loc, 親幹stem, name)] = {code,concept,io} を登録。
  - depth2 → registry.resolve_local(name, 親=直近depth1の概念? いや実体? )
             engine の親キーは (loc, 親小区分の実体L-, name)。
             行 (2, name, m0,m1,m2, "親depth1名@拠点") を渡す。
             local_codes は親=depth1実体L- でキー登録。

io（収支区分）の導出:
  resolve_local の指紋に必要。親幹stemコードの master 属性から引く。
    CF: 収入/支出（差額/残高の配下に小区分は出ない）
    PL: 収益/費用
    BS: 資産/負債/純資産（master の ASSET/LIAB/NET から和名へ）

戻り値: build_corpdata(...) -> (CorpData, {"CF":reg, "PL":reg, "BS":reg})
  レビューキューは各 Registry.review に蓄積（呼び側で集約）。
"""
import re
import pdfplumber

from .extract_kyoten4 import extract_kyoten4
from .registry import Registry
from . import naming


# ---- 幹stemコード -> io（収支区分）の対応表を masters から構築 ----
def _build_io_map(cf_master, pl_master, bs_master):
    io = {}
    for row in cf_master:
        io[row[0]] = row[2]          # 収入/支出/差額/残高
    for row in pl_master:
        io[row[0]] = row[2]          # 収益/費用/差額
    sec = {"ASSET": "資産", "LIAB": "負債", "NET": "純資産"}
    for row in bs_master:
        io[row[0]] = sec.get(row[1], row[1])
    return io


def _strip_corp_prefix_for_honbu(kyoten, corp_name):
    """本部拠点名の法人名融合を整える。
    パーサは「社会福祉法人○○本部拠点」から「社会福祉法人」のみ除去した値を返す
    ことがある（前チャットの宿題）。法人名を剥がして '本部拠点'/'本部' に整える。
    法人名が含まれない通常拠点はそのまま返す。
    """
    name = kyoten
    # 「社会福祉法人」接頭が残っていれば除去
    name = re.sub(r"^社会福祉法人", "", name).strip()
    # 法人名（corp_name から「社会福祉法人」を除いた中核）を剥がす
    core = re.sub(r"^社会福祉法人", "", corp_name or "").strip()
    if core and name.startswith(core):
        rest = name[len(core):].strip()
        # 「○○本部拠点」「○○本部」→ rest が '本部拠点'/'本部' 等
        if rest:
            name = rest
    return name


def _statement_master(statement, masters):
    if statement == "CF":
        return masters.CF_MASTER
    if statement == "PL":
        return masters.PL_MASTER
    return masters.BS_MASTER


def _form_code(statement):
    return {"CF": "1-4", "PL": "2-4", "BS": "3-4"}[statement]


def _full_form(statement):
    return {"CF": "CF-1-4", "PL": "PL-2-4", "BS": "BS-3-4"}[statement]


def _emit_metrics(assign, n_metrics):
    """{col_index: value} を engine が読む metric順タプルへ。
    欠損列は None（engine は None 行を作らない）。
    CF/PL/BS とも値列は3（colnames に対応）。"""
    return tuple(assign.get(i, None) for i in range(n_metrics))


def _convert_meisai(blocks, statement, reg, io_map, corp_name, form_full, page_hint):
    """CF(1-4)/PL(2-4) 明細様式の変換。
    戻り:
      data   : {拠点: [行...]}  行=(depth, code|name, m0,m1,m2[, parent])
      locals : {(loc, parent_code, name): {"code","concept","io"}}
    """
    data = {}
    local_codes = {}
    loc_order = []
    for blk in blocks:
        loc = _strip_corp_prefix_for_honbu(blk["kyoten"], corp_name)
        loc_order.append(loc)
        rows_out = []
        cur_stem = None          # 直近 depth0 の幹stemコード
        cur_stem_io = ""         # その io
        d1_local = {}            # depth1 名 -> 実体L-コード（depth2 の親解決用）
        d1_name_last = None      # 直近 depth1 名（depth2 の @拠点 親表記用）
        for (name, depth, assign) in blk["rows"]:
            m = _emit_metrics(assign, 3)
            if depth == 0:
                stem = reg.resolve_global(name)
                if stem is None:
                    # 未知の大区分/計。マスタ未収載の事業別大区分（例 助成金事業収入,
                    # 法人固有の事業名収入 等）。拠点別の擬似親コードで resolve_local し、
                    # 「その拠点固有の local 大区分」として採番する。
                    # 重要: parent に拠点識別子 @LOC:{loc} を渡すことで、
                    #   (1) fingerprint が拠点別になり全拠点で1コードに潰れる主キー重複を防止
                    #   (2) 採番した L コードを cur_stem に据え、配下 depth1/2 の親に使う
                    #       → parent 空による FKlocal親違反も発生しない
                    pseudo_parent = f"@LOC:{loc}"
                    ic, cc, _ = reg.resolve_local(
                        name, pseudo_parent, "", form_full, page_hint)
                    rows_out.append((1, name, m[0], m[1], m[2], pseudo_parent))
                    local_codes[(loc, pseudo_parent, name)] = {
                        "code": ic, "concept": cc, "io": ""}
                    # この未知大区分を以降の子の親（depth1/2 の幹相当）にする
                    cur_stem, cur_stem_io = ic, ""
                    d1_local = {}
                    d1_name_last = None
                    continue
                cur_stem = stem
                cur_stem_io = io_map.get(stem, "")
                rows_out.append((0, stem, m[0], m[1], m[2]))
                d1_local = {}
                d1_name_last = None
            elif depth == 1:
                parent = cur_stem if cur_stem else ""
                io = cur_stem_io
                ic, cc, _ = reg.resolve_local(
                    name, parent, io, form_full, page_hint)
                rows_out.append((1, name, m[0], m[1], m[2], parent))
                local_codes[(loc, parent, name)] = {
                    "code": ic, "concept": cc, "io": io}
                d1_local[name] = ic
                d1_name_last = name
            else:  # depth == 2
                # 親は直近 depth1。親が無い（depthずれ）場合は cur_stem に逃がす。
                if d1_name_last is None:
                    parent = cur_stem if cur_stem else ""
                    io = cur_stem_io
                    ic, cc, _ = reg.resolve_local(
                        name, parent, io, form_full, page_hint)
                    rows_out.append((1, name, m[0], m[1], m[2], parent))
                    local_codes[(loc, parent, name)] = {
                        "code": ic, "concept": cc, "io": io}
                    d1_local[name] = ic
                    d1_name_last = name
                    continue
                parent_local = d1_local[d1_name_last]
                io = cur_stem_io
                ic, cc, _ = reg.resolve_local(
                    name, parent_local, io, form_full, page_hint)
                rows_out.append((2, name, m[0], m[1], m[2],
                                 f"{d1_name_last}@{loc}"))
                local_codes[(loc, parent_local, name)] = {
                    "code": ic, "concept": cc, "io": io}
        data[loc] = rows_out
    return data, local_codes, loc_order


def _convert_bs(blocks, reg, io_map, corp_name, form_full, page_hint):
    """BS(3-4) 明細様式（明細なし: 大区分のみ）の変換。
    engine 形: {拠点: {code:(m0,m1,m2)}}。rows を全てコード化。
      depth0 → resolve_global で幹stem。
      depth1 → resolve_local で local（概念/実体）。
    BSは「明細なし(大区分のみ)」と engine README にあるが、実際の 3-4 には
    現金預金等の depth1 が並ぶ。これらも registry でコード化して同一 dict に格納する。
    """
    data = {}
    local_codes = {}
    loc_order = []
    for blk in blocks:
        loc = _strip_corp_prefix_for_honbu(blk["kyoten"], corp_name)
        loc_order.append(loc)
        d = {}
        cur_stem = None
        cur_stem_io = ""
        for (name, depth, assign) in blk["rows"]:
            m = _emit_metrics(assign, 3)
            if depth == 0:
                stem = reg.resolve_global(name)
                if stem is None:
                    # 稀: 未知大区分。local へ退避（親なし）。
                    ic, cc, _ = reg.resolve_local(
                        name, "", "", form_full, page_hint)
                    d[ic] = m
                    local_codes[(loc, "", name)] = {
                        "code": ic, "concept": cc, "io": ""}
                    cur_stem, cur_stem_io = None, ""
                    continue
                cur_stem = stem
                cur_stem_io = io_map.get(stem, "")
                d[stem] = m
            else:
                parent = cur_stem if cur_stem else ""
                io = cur_stem_io
                ic, cc, _ = reg.resolve_local(
                    name, parent, io, form_full, page_hint)
                d[ic] = m
                local_codes[(loc, parent, name)] = {
                    "code": ic, "concept": cc, "io": io}
        data[loc] = d
    return data, local_codes, loc_order


def build_corpdata(pdf_paths, corp_no, corp_name, fiscal_year, masters,
                   address="", main_business="", seg2_order=None):
    """
    pdf_paths: {"CF": path|None, "PL": path|None, "BS": path|None}
               存在する様式だけ処理する（無人完走前提）。
    masters  : shafuku_db_engine.masters モジュール
    戻り: (CorpData, registries dict, review_rows)
    """
    from shafuku_db_engine.ingest import CorpData

    io_map = _build_io_map(masters.CF_MASTER, masters.PL_MASTER, masters.BS_MASTER)
    regs = {}
    cf = {}
    pl = {}
    bs = {}
    all_local = {}
    loc_order = None

    for stmt in ("CF", "PL", "BS"):
        path = pdf_paths.get(stmt)
        if not path:
            continue
        reg = Registry(_statement_master(stmt, masters), stmt, corp_no)
        regs[stmt] = reg
        with pdfplumber.open(path) as pdf:
            blocks = extract_kyoten4(pdf, stmt)
        form_full = _full_form(stmt)
        if stmt == "BS":
            data, lcodes, order = _convert_bs(
                blocks, reg, io_map, corp_name, form_full, page_hint=0)
            bs[_form_code(stmt)] = data
        else:
            data, lcodes, order = _convert_meisai(
                blocks, stmt, reg, io_map, corp_name, form_full, page_hint=0)
            (cf if stmt == "CF" else pl)[_form_code(stmt)] = data
        all_local.update(lcodes)
        if loc_order is None:
            loc_order = order

    corp = CorpData(
        corp_no=corp_no, corp_name=corp_name, fiscal_year=fiscal_year,
        address=address, main_business=main_business,
        n_locations=len(loc_order or []), loc_order=loc_order or [],
        seg2_order=seg2_order, cf=cf, pl=pl, bs=bs)
    corp.local_codes = all_local

    review_rows = []
    for stmt, reg in regs.items():
        for it in reg.review:
            review_rows.append(it.row())

    return corp, regs, review_rows
