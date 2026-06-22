# -*- coding: utf-8 -*-
"""
取り込みエンジン。法人の構造化データ(CorpData)を受け取り、
fact 行と、その法人ぶんの dim_segment / dim_account_local 行を生成する。

入力データ形式（corps/*.py の規約）:
  CorpData(
    corp_no, corp_name, fiscal_year, address, main_business, n_locations,
    loc_order,                 # 拠点名のリスト（表示順）
    cf={...}, pl={...}, bs={...}  # 各計算書の様式別データ（後述）
  )

各様式データの形（None=空欄、行は作らない）:
  法人単位 (1-1/2-1/3-1):  code -> (m0, m1, m2)        metric順は schema.METRICS_BY_FORM
  事業区分別 (1-2/2-2/3-2): code -> (社福,公益,収益,合計,内部消去,法人合計)   metric=単一
  拠点別     (1-3/2-3/3-3): code -> ([拠点...], 合計, 内部消去, 事業区分計)   metric=単一
  拠点明細   (1-4/2-4):     {拠点: [行...]}  行=(depth, code_or_name, m0,m1,m2[, parent])
                            depth0=幹stemコード, depth1/2=小区分/明細(local)
  拠点明細   (3-4):         {拠点: {code:(m0,m1,m2)}}   明細なし(大区分のみ)

local の親:
  depth1 行の parent = 幹stemコード
  depth2 行の parent = "親名@拠点名"（同loc内 depth1 の名称）
"""
from . import schema


def _stmt_of_code(code):
    """親幹コードの接頭辞から計算書区分(CF/PL/BS)を判定。"""
    if code.startswith("CF"):
        return "CF"
    if code.startswith("PL"):
        return "PL"
    if code.startswith("BS"):
        return "BS"
    return ""


class CorpData:
    def __init__(self, corp_no, corp_name, fiscal_year,
                 address="", main_business="", n_locations=0,
                 loc_order=None, seg2_order=None,
                 cf=None, pl=None, bs=None):
        self.corp_no = corp_no
        self.corp_name = corp_name
        self.fiscal_year = str(fiscal_year)
        self.address = address
        self.main_business = main_business
        self.n_locations = n_locations
        self.loc_order = loc_order or []
        # 事業区分別6列の並び（既定: 社福/公益/収益/合計/内部消去/法人合計）
        self.seg2_order = seg2_order or ["社会福祉事業", "公益事業", "収益事業",
                                         "合計", "内部取引消去", "法人合計"]
        self.cf = cf or {}   # {"1-1":{...}, "1-2":{...}, "1-3":{...}, "1-4":{...}}
        self.pl = pl or {}
        self.bs = bs or {}
        self.kyoten_loc = {}  # 様式別の拠点並び上書き（既定 loc_order）
        # Option1(registryがコード一元管理): アダプタが事前採番した local コードを注入する。
        #   local_codes: {(loc, parent_code, name): {"code": L-..., "concept": LC-...}}
        # None のときはエンジンが自前で L- を採番する（既存 corps/*.py の挙動を維持）。
        self.local_codes = None


class Ingested:
    """1法人ぶんの生成物。"""
    def __init__(self):
        self.fact = []           # list of dict (schema.TABLES['fact_financial'])
        self.segments = []       # dim_segment rows (list)
        self.locals = []         # dim_account_local rows (list)
        self.concepts = []       # dim_account_concept rows (list)
        self.corp_row = None     # dim_corp row (list)


def ingest(corp: CorpData) -> Ingested:
    out = Ingested()
    CN, FY = corp.corp_no, corp.fiscal_year

    # ---- segments ----
    seg_code_of = {}     # (kind, name) -> code
    def add_seg(kind, name, loc=""):
        key = (kind, name)
        if key in seg_code_of:
            return seg_code_of[key]
        code = schema.seg_code(CN, len(seg_code_of) + 1)
        seg_code_of[key] = code
        out.segments.append([code, CN, kind, name, loc])
        return code

    SEG_ZENTAI = add_seg("法人全体", "法人全体")
    seg2_codes = []
    for nm in corp.seg2_order:
        if nm == "法人合計":
            seg2_codes.append(SEG_ZENTAI)              # 法人合計列 == 法人全体
        elif nm == "合計":
            seg2_codes.append(add_seg("合計", "合計"))
        elif nm == "内部取引消去":
            seg2_codes.append(add_seg("内部取引消去", "内部取引消去"))
        elif nm in ("社会福祉事業", "公益事業", "収益事業"):
            seg2_codes.append(add_seg("事業区分", nm))
        else:
            seg2_codes.append(add_seg("事業区分", nm))
    SEG_GOKEI = add_seg("合計", "合計")
    SEG_ELIM = add_seg("内部取引消去", "内部取引消去")
    SEG_SEGT = add_seg("事業区分合計", "事業区分合計")
    SEG_LOC = {loc: add_seg("拠点", loc, loc) for loc in corp.loc_order}

    # ---- local account codes (親で一意化: (loc,parent_code,name)) ----
    # Option1: corp.local_codes があれば registry採番の L-/LC- を使う。無ければ自前採番。
    injected = corp.local_codes
    local_of = {}        # (loc, parent_code, name) -> code
    concept_seen = set()
    def add_local(loc, parent_code, name, depth):
        key = (loc, parent_code, name)
        if key in local_of:
            return local_of[key]
        concept = ""
        if injected is not None and key in injected:
            code = injected[key]["code"]
            concept = injected[key].get("concept", "")
            if concept and concept not in concept_seen:
                concept_seen.add(concept)
                out.concepts.append([concept, _stmt_of_code(parent_code),
                                     parent_code, injected[key].get("io", ""), name])
        else:
            code = schema.local_code(CN, len(local_of) + 1)
        local_of[key] = code
        out.locals.append([code, CN, parent_code, name, depth, concept])
        return code

    def build_local_for(source):
        # source: {loc: [rows]} 明細様式(1-4/2-4)
        # 採番順は従来どおり「全depth1 → 全depth2」の2パス（既存corps基準線のコード番号を
        # 非破壊で維持するため）。ただし depth2 の親解決は「名前引き(d1_by_name[name])」
        # では同名 depth1 が同一拠点に複数あると衝突するため、文書順に直近の depth1 を
        # 辿る方式に変更する（同名衝突が無い既存corpsでは結果が一致＝基準線非破壊）。
        for loc, rows in source.items():
            # 1パス目: depth1 を文書順に採番。各 depth1 行の位置→コードを記録。
            d1_code_at = {}      # rows内のindex -> depth1のlocalコード
            for idx, row in enumerate(rows):
                if row[0] == 1:
                    name, parent_stem = row[1], row[5]
                    d1_code_at[idx] = add_local(loc, parent_stem, name, 1)
            # 2パス目: depth2 を採番。親は「その行より前の直近 depth1」。
            last_d1_code = None
            for idx, row in enumerate(rows):
                if row[0] == 1:
                    last_d1_code = d1_code_at[idx]
                elif row[0] == 2:
                    add_local(loc, last_d1_code, row[1], 2)

    # 明細様式から local を採番（CF 1-4, PL 2-4）。3-4は明細なし。
    if "1-4" in corp.cf:
        build_local_for(corp.cf["1-4"])
    if "2-4" in corp.pl:
        build_local_for(corp.pl["2-4"])

    # ---- fact emit ----
    def emit(form, code, segc, metric, val):
        if val is None:
            return
        out.fact.append([CN, FY, form, code, segc, metric, val])

    def emit_houjin(form, data):
        metrics = schema.METRICS_BY_FORM[form]
        for code, vals in data.items():
            for m, v in zip(metrics, vals):
                emit(form, code, SEG_ZENTAI, m, v)

    def emit_segwari(form, data):
        m = schema.METRICS_BY_FORM[form][0]
        for code, vals in data.items():
            for segc, v in zip(seg2_codes, vals):
                emit(form, code, segc, m, v)

    def emit_kyoten(form, data):
        m = schema.METRICS_BY_FORM[form][0]
        locs_order = corp.kyoten_loc.get(form, corp.loc_order)
        for code, (locs, total, elim, segtot) in data.items():
            for loc, v in zip(locs_order, locs):
                if loc not in SEG_LOC:
                    SEG_LOC[loc] = add_seg("拠点", loc, loc)
                emit(form, code, SEG_LOC[loc], m, v)
            emit(form, code, SEG_GOKEI, m, total)
            emit(form, code, SEG_ELIM, m, elim)
            emit(form, code, SEG_SEGT, m, segtot)

    def emit_meisai(form, source):
        metrics = schema.METRICS_BY_FORM[form]
        for loc, rows in source.items():
            segc = SEG_LOC[loc]
            # depth2 の親は「文書順で直近の depth1」（同名 depth1 衝突に強い）。
            last_d1_code = None
            for row in rows:
                depth = row[0]
                if depth == 0:
                    code = row[1]
                elif depth == 1:
                    code = local_of[(loc, row[5], row[1])]
                    last_d1_code = code
                else:
                    code = local_of[(loc, last_d1_code, row[1])]
                for m, v in zip(metrics, row[2:5]):
                    emit(form, code, segc, m, v)

    def emit_meisai_flat(form, source):
        # 3-4: {loc: {code:(m0,m1,m2)}} 明細なし
        metrics = schema.METRICS_BY_FORM[form]
        for loc, d in source.items():
            segc = SEG_LOC[loc]
            for code, vals in d.items():
                for m, v in zip(metrics, vals):
                    emit(form, code, segc, m, v)

    # 拠点別様式のloc並び（既定 loc_order）。3-3 が社福区分内拠点のみ等で違う場合に上書き。
    # corp.kyoten_loc = {"CF-1-3":[...], "PL-2-3":[...], "BS-3-3":[...]}

    # CF
    if "1-1" in corp.cf: emit_houjin("CF-1-1", corp.cf["1-1"])
    if "1-2" in corp.cf: emit_segwari("CF-1-2", corp.cf["1-2"])
    if "1-3" in corp.cf: emit_kyoten("CF-1-3", corp.cf["1-3"])
    if "1-4" in corp.cf: emit_meisai("CF-1-4", corp.cf["1-4"])
    # PL
    if "2-1" in corp.pl: emit_houjin("PL-2-1", corp.pl["2-1"])
    if "2-2" in corp.pl: emit_segwari("PL-2-2", corp.pl["2-2"])
    if "2-3" in corp.pl: emit_kyoten("PL-2-3", corp.pl["2-3"])
    if "2-4" in corp.pl: emit_meisai("PL-2-4", corp.pl["2-4"])
    # BS
    if "3-1" in corp.bs: emit_houjin("BS-3-1", corp.bs["3-1"])
    if "3-2" in corp.bs: emit_segwari("BS-3-2", corp.bs["3-2"])
    if "3-3" in corp.bs: emit_kyoten("BS-3-3", corp.bs["3-3"])
    if "3-4" in corp.bs: emit_meisai_flat("BS-3-4", corp.bs["3-4"])

    out.corp_row = [CN, corp.corp_name, corp.address, corp.main_business,
                    corp.n_locations, ""]
    return out
