# -*- coding: utf-8 -*-
"""
橋渡しアダプタ（タイプ1 法人単位 → engine CorpData の 1-1 / 2-1 / 3-1）。

パーサ出力 extract_houjin(pdf, statement) の同形出力
    [(name, {col_index: value}), ...]    （上から出現順・depth情報なし）
を、レジストリ registry.Registry でコード解決し、エンジンが受ける形
    corp.cf["1-1"] / corp.pl["2-1"] / corp.bs["3-1"] = { code: (m0, m1, m2) }
へ組み立てる。emit_houjin が SEG_ZENTAI(法人全体) に出す。

タイプ1の特性（タイプ4との違い）:
  - 法人単位の集約表。拠点・事業区分の概念は無い。
  - 1-1/2-1 は階層が無く、全行が幹stem（大区分・小区分・計・差額）に1:1対応。
    → resolve_global だけで足りる（resolve_local / local_codes は不要）。
  - 3-1(BS) は基本財産/その他の固定資産で「土地・建物・定期預金・貸倒引当金・
    その他の固定資産」が**同名で複数回**出る。registry の global_by_norm は
    名前単独キーなので同名は最後の1つに潰れる（タイプ4の拠点別BSは大区分のみで
    この問題に当たらなかった）。
    → BS のみ「中区分セクション文脈つき」で解決する _BSSectionResolver を用意。
      共有の registry/masters には手を入れず、アダプタ内で BS_MASTER を読んで
      (中区分, 正規化名) -> code の表を構築し、行を文書順に走査して現在の中区分を
      追跡しながら同名を弁別する。

ノイズ行の扱い:
  パーサは様式タイトル「法人単位…計算書」や、縦書き区分ラベルの取りこぼし1文字
  （'動' 'に' 'よ' 'サ' 'ビ' 等）、純資産下の '0' などを {} もしくは値つきで返すことがある。
  これらは幹stemに解決しないので **値の有無に関わらずスキップ**する（レビューキューにも積まない）。
  ただし「値を持つのに解決しない行」は、本来の科目名が壊れている可能性があるので
  warnings に記録して呼び側へ返す（無人完走は止めない）。
"""
from . import naming


def _build_io_map(masters):
    """幹stemコード -> io（収支区分）。adapter_kyoten4 と同一の作り。"""
    io = {}
    for row in masters.CF_MASTER:
        io[row[0]] = row[2]
    for row in masters.PL_MASTER:
        io[row[0]] = row[2]
    sec = {"ASSET": "資産", "LIAB": "負債", "NET": "純資産"}
    for row in masters.BS_MASTER:
        io[row[0]] = sec.get(row[1], row[1])
    return io


class _BSSectionResolver:
    """BS_MASTER を中区分セクション文脈つきで解決する。
    BS行: (code, 区分ASSET/LIAB/NET, 中区分, 正規名, is_total, indent)
    同名（土地/建物/定期預金/貸倒引当金/その他の固定資産）を「現在の中区分」で弁別する。
    """

    def __init__(self, bs_master):
        self.rows = list(bs_master)
        # (中区分, 正規化名) -> [codes...]（同一中区分内でも その他の固定資産 のように
        #   ヘッダ(indent小)と明細(indent大)で重複しうるので indent 昇順で保持）
        self.by_sec = {}
        # 正規化名 -> [codes...]（全体で一意かどうかの判定用）
        self.by_norm_all = {}
        # 中区分ヘッダ名（正規化）-> その中区分名 への対応（セクション遷移検出用）
        self.section_of_header = {}
        for code, ku, chu, nm, tot, indent in self.rows:
            n = naming.normalize(nm)
            self.by_sec.setdefault((chu, n), []).append((indent, code))
            self.by_norm_all.setdefault(n, []).append(code)
            # ヘッダ行（正規名 == 中区分）はセクション遷移のトリガ
            if nm == chu:
                self.section_of_header.setdefault(n, chu)
        for k in self.by_sec:
            self.by_sec[k].sort()  # indent 昇順

    def resolve_iter(self, names_in_order):
        """文書順の名前列を受け、各行のコード（or None）を yield する。
        現在の中区分を追跡しながら同名を弁別する。
        ※ その他の固定資産: ヘッダ(indent1)→明細(indent2)の順で1回ずつ出るので、
          セクション内で同名ヘッダが出たら「ヘッダ消費済み」とし、次の同名は明細を返す。
        """
        cur_chu = None
        consumed = {}  # (chu, norm) -> 何個目まで消費したか
        entered = set()  # 既に入った中区分（ヘッダ二重消費の防止）
        out = []
        for raw in names_in_order:
            n = naming.normalize(raw)
            new_sec = self.section_of_header.get(n)
            code = None
            if new_sec is not None and new_sec not in entered:
                # この行は中区分ヘッダ。ヘッダ行のコード（正規名==中区分の行）を当てる。
                #   その中区分内で indent 最小（=ヘッダ）の候補を取る。
                hdr_cands = self.by_sec.get((new_sec, n), [])
                if hdr_cands:
                    code = hdr_cands[0][1]
                    # ヘッダ枠を消費済みにして、以降の同名は明細へ回す
                    consumed[(new_sec, n)] = 1
                entered.add(new_sec)
                cur_chu = new_sec
            else:
                # 通常行（または既に入った中区分のヘッダ名＝明細）。現中区分で弁別。
                code = self._pick(cur_chu, n, consumed)
                if code is None:
                    allc = self.by_norm_all.get(n)
                    if allc and len(allc) == 1:
                        code = allc[0]
            out.append(code)
        return out

    def _pick(self, chu, norm, consumed):
        if chu is None:
            return None
        key = (chu, norm)
        cand = self.by_sec.get(key)
        if not cand:
            return None
        i = consumed.get(key, 0)
        if i >= len(cand):
            # それ以上同名が無ければ末尾を返す（明細の取りこぼし回避）
            return cand[-1][1]
        code = cand[i][1]
        consumed[key] = i + 1
        return code


def _convert_houjin_flat(rows, stmt, reg):
    """1-1 / 2-1（階層なし・全行 global）を {code:(m0,m1,m2)} に。
    戻り: (data, warnings)
    """
    data = {}
    warnings = []
    for (name, vals) in rows:
        code = reg.resolve_global(name)
        if code is None:
            if vals:  # 値を持つのに解決しない＝名前破損の可能性
                warnings.append((stmt, name, dict(vals)))
            continue
        m = tuple(vals.get(i) for i in range(3))
        # None だらけ（空行）でも code は立てる。emit 側で None はスキップされる。
        data[code] = m
    return data, warnings


def _convert_bs(rows, reg, bs_master):
    """3-1(BS)。中区分セクション文脈つきで解決して {code:(m0,m1,m2)} に。"""
    resolver = _BSSectionResolver(bs_master)
    names = [name for (name, vals) in rows]
    codes = resolver.resolve_iter(names)
    data = {}
    warnings = []
    for (name, vals), code in zip(rows, codes):
        if code is None:
            if vals:
                warnings.append(("BS", name, dict(vals)))
            continue
        m = tuple(vals.get(i) for i in range(3))
        data[code] = m
    return data, warnings


def build_corpdata(pdf_paths, corp_no, corp_name, fiscal_year, masters,
                   address="", main_business="", seg2_order=None):
    """
    pdf_paths: {"CF": path|None, "PL": path|None, "BS": path|None}
               （タイプ1の 1-1/2-1/3-1 に対応。存在する様式だけ処理）
    戻り: (CorpData, registries dict, review_rows, warnings)
      review_rows は通常空（タイプ1は local 採番が無いため）。
    """
    import pdfplumber
    from shafuku_db_engine.ingest import CorpData
    from shafuku_parser.extract_houjin import extract_houjin

    regs = {}
    cf, pl, bs = {}, {}, {}
    warnings = []

    def master_of(stmt):
        return {"CF": masters.CF_MASTER, "PL": masters.PL_MASTER,
                "BS": masters.BS_MASTER}[stmt]

    for stmt, key in (("CF", "1-1"), ("PL", "2-1"), ("BS", "3-1")):
        path = pdf_paths.get(stmt)
        if not path:
            continue
        reg = Registry(master_of(stmt), stmt, corp_no)
        regs[stmt] = reg
        with pdfplumber.open(path) as pdf:
            rows = extract_houjin(pdf, stmt)
        if stmt == "BS":
            data, w = _convert_bs(rows, reg, masters.BS_MASTER)
            bs[key] = data
        else:
            data, w = _convert_houjin_flat(rows, stmt, reg)
            (cf if stmt == "CF" else pl)[key] = data
        warnings.extend(w)

    corp = CorpData(
        corp_no=corp_no, corp_name=corp_name, fiscal_year=fiscal_year,
        address=address, main_business=main_business,
        n_locations=0, loc_order=[], seg2_order=seg2_order,
        cf=cf, pl=pl, bs=bs)
    corp.local_codes = {}  # タイプ1は local 無し。空dictで「事前採番済み(空)」を明示。

    review_rows = []
    for reg in regs.values():
        for it in reg.review:
            review_rows.append(it.row())

    return corp, regs, review_rows, warnings


# Registry を local import すると循環の恐れがあるためモジュール先頭ではなく明示import
from .registry import Registry  # noqa: E402
