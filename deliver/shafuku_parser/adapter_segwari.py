# -*- coding: utf-8 -*-
"""
橋渡しアダプタ（タイプ2 = 事業区分別内訳表 → engine CorpData の 1-2 / 2-2 / 3-2）。

役割（3層分離の「配線」担当・タイプ4アダプタと同じ思想）:
  パーサ出力 extract_segwari(pdf, statement) の同形出力
      [(name, {col_index: value}), ...]   col0..5 = 社福/公益/収益/合計/内部消去/法人合計
  を、レジストリ registry.Registry でコード解決し、
  エンジン engine.ingest.CorpData が受ける形へ組み立てる。

  CorpData 形（ingest.py 準拠）:
    corp.cf["1-2"] / corp.pl["2-2"] / corp.bs["3-2"]
        = { code: (社福,公益,収益,合計,内部消去,法人合計) }
    emit_segwari が seg2_codes（corp.seg2_order順）に1値ずつ出す。

タイプ4との違い（重要）:
  - タイプ2は depth 階層を持たない（大区分・中区分・計が同一平面に並ぶフラット様式）。
    各行は基本的に「自分自身の幹stemコード」を持つ（例: 現金預金=BS-A-CUR-010）。
    → depth0/1/2 の親子組み立ては不要。resolve_global で1対1にコード化するだけ。
  - 未知科目（masterに無い行。例: 事業区分間貸付金/借入金）は resolve_local で
    レビューキューへ積むが、**fact には入れない**。理由: engine の emit_segwari は
    type-2 の local を dim_account_local に materialize しないため、provisional な
    L- コードを指す fact を作ると FK 破壊になる。未知科目は held に退避して呼び側へ返し、
    人＋Claude のバッチレビューで master 採番後に再取り込みする（無人完走は止めない）。

io（収支区分）は親 or 自身の幹stemコードの master 属性から導出（_build_io_map 流用）。

戻り値: build_corpdata_segwari(...) -> (CorpData, regs, review_rows, held_rows)
  held_rows: [(form_full, name, 6tuple, L-code, LC-code), ...]  未解決行（要レビュー）
"""
import pdfplumber

from .extract_segwari import extract_segwari
from .registry import Registry
# タイプ4アダプタの共通ヘルパを再利用（重複実装を避ける）
from .adapter_kyoten4 import _build_io_map


_FORM = {"CF": "1-2", "PL": "2-2", "BS": "3-2"}
_FULLFORM = {"CF": "CF-1-2", "PL": "PL-2-2", "BS": "BS-3-2"}


def _statement_master(statement, masters):
    if statement == "CF":
        return masters.CF_MASTER
    if statement == "PL":
        return masters.PL_MASTER
    return masters.BS_MASTER


def _six(assign):
    """{col_index: value} を 6列タプル (社福,公益,収益,合計,内部消去,法人合計) へ。
    欠損列は None（engine の V() が 0 扱い）。"""
    return tuple(assign.get(i, None) for i in range(6))


def _convert(rows, statement, reg, io_map, form_full):
    """タイプ2のフラット行を {code: 6tuple} と held(未解決) に変換。

    既知科目（resolve_global成功）のみ fact 用の data に入れる。
    未知科目は resolve_local でレビューキューに積むが fact には入れず held に退避。
    """
    data = {}
    held = []                # 未解決行: (form, name, 6tuple, L-, LC-)
    cur_parent = ""          # 直近に解決できた幹stem（未知科目の親候補）
    cur_io = ""
    for (name, assign) in rows:
        m = _six(assign)
        stem = reg.resolve_global(name)
        if stem is not None:
            data[stem] = m
            cur_parent = stem
            cur_io = io_map.get(stem, "")
        else:
            ic, cc, _ = reg.resolve_local(
                name, cur_parent, cur_io, form_full, page=0)
            held.append((form_full, name, m, ic, cc))
    return data, held


def build_corpdata_segwari(pdf_paths, corp_no, corp_name, fiscal_year, masters,
                           address="", main_business="", seg2_order=None,
                           loc_order=None, base_corp=None):
    """タイプ2（1-2/2-2/3-2）分の CorpData を組む。

    pdf_paths: {"CF": path|None, "PL": path|None, "BS": path|None}
    base_corp: 既存 CorpData があれば、その cf/pl/bs に追記する（他タイプと統合する用途）。
    戻り: (CorpData, regs, review_rows, held_rows)
    """
    from shafuku_db_engine.ingest import CorpData

    io_map = _build_io_map(masters.CF_MASTER, masters.PL_MASTER, masters.BS_MASTER)
    regs = {}
    held_rows = []

    if base_corp is not None:
        corp = base_corp
        if corp.local_codes is None:
            corp.local_codes = {}
    else:
        corp = CorpData(
            corp_no=corp_no, corp_name=corp_name, fiscal_year=fiscal_year,
            address=address, main_business=main_business,
            n_locations=len(loc_order or []), loc_order=loc_order or [],
            seg2_order=seg2_order)
        corp.local_codes = {}

    for stmt in ("CF", "PL", "BS"):
        path = pdf_paths.get(stmt)
        if not path:
            continue
        reg = Registry(_statement_master(stmt, masters), stmt, corp_no)
        regs[stmt] = reg
        with pdfplumber.open(path) as pdf:
            rows = extract_segwari(pdf, stmt)
        data, held = _convert(rows, stmt, reg, io_map, _FULLFORM[stmt])
        target = {"CF": corp.cf, "PL": corp.pl, "BS": corp.bs}[stmt]
        target[_FORM[stmt]] = data
        held_rows.extend(held)

    review_rows = []
    for stmt, reg in regs.items():
        for it in reg.review:
            review_rows.append(it.row())

    return corp, regs, review_rows, held_rows
