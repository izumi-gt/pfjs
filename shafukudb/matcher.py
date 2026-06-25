# -*- coding: utf-8 -*-
"""
照合エンジン。実PDF抽出行を正典コードへ対応づける。

責務(これだけ):
- 入力: ExtractedLine(計算書, 様式, 生の名前, 親スコープ, 金額)
- 出力: MatchResult(ヒット code / 未知行退避)
責務でないもの: PDF抽出(罫線・x座標), 算式検算(恒等式)。これらは照合層の外。

照合の優先順位(設計合意済み):
  1) 親スコープが確定していれば、そのスコープの子の中で正規化名一致を探す(本筋)。
  2) スコープで一意化できない/様式が浅く親スコープが取れない場合は、
     計算書×正規化名の候補全体から探す。一意なら採用。
  3) 候補が複数残れば、止めずに「要曖昧解消」として退避。

未知行退避の理由分類(設計合意済みの4区分):
  - NO_NAME    : 正典に該当名なし(法人独自表記の疑い)
  - AMBIGUOUS  : 複数候補で一意化できず(要曖昧解消)
  - STRUCTURE  : 様式深度的に異常(空であるべき行に値がある等)
  - MISEXTRACT : 罫線・空行起因の誤抽出の疑い
"""
from normalize import normalize

NO_NAME = "NO_NAME"
AMBIGUOUS = "AMBIGUOUS"
STRUCTURE = "STRUCTURE"
MISEXTRACT = "MISEXTRACT"

# 様式コード -> 期待される最深階層(深度番号; seiten._DEPTH_BY_KIND と整合)
#   華野1社で実証できた5様式を起点に登録。未登録様式は None(深度チェックを保留)。
#   2=大区分 3=中区分 5=小区分。後続3社で他様式・他事業を見て拡張する。
FORM_MAX_DEPTH = {
    "1-1": 2,   # 法人単位CF: 大区分のみ
    "1-3": 2,   # 事業区分資金収支内訳表: 大区分
    "1-4": 5,   # 拠点区分CF: 小区分まで
    "3-1": 3,   # 法人単位BS: 中区分まで
    "3-2": 3,
    "3-3": 3,
    "3-4": 5,   # 拠点区分BS: 小区分まで
}


class ExtractedLine:
    """実PDFから抽出された1行。"""
    __slots__ = ("stmt", "form", "raw_name", "parent_path", "amount")

    def __init__(self, stmt, form, raw_name, parent_path=None, amount=None):
        self.stmt = stmt            # 'CF'/'PL'/'BS'
        self.form = form            # '1-1' 等
        self.raw_name = raw_name    # 抽出された生の科目名
        # 親スコープ: (計算書, L0,L1,L2,L3,L3.5) のコード列。取れなければ None
        self.parent_path = parent_path
        self.amount = amount

    @property
    def norm(self):
        return normalize(self.raw_name)


class MatchResult:
    __slots__ = ("line", "code", "record", "reason", "candidates")

    def __init__(self, line, code=None, record=None, reason=None, candidates=None):
        self.line = line
        self.code = code              # ヒット時の正典code
        self.record = record          # ヒット時の SeitenRecord
        self.reason = reason          # 退避時の理由(上記4区分)
        self.candidates = candidates or []

    @property
    def matched(self):
        return self.code is not None


class Matcher:
    def __init__(self, seiten):
        self.seiten = seiten

    def match_line(self, line):
        nm = line.norm
        if not nm:
            return MatchResult(line, reason=MISEXTRACT)

        # --- 優先順位1: 親スコープ内で一致を探す ---
        if line.parent_path is not None:
            kids = self.seiten.by_parent.get(line.parent_path, [])
            hits = [r for r in kids if r.norm == nm]
            if len(hits) == 1:
                return MatchResult(line, code=hits[0].code, record=hits[0])
            if len(hits) >= 2:
                # 同一スコープ内に同名が複数(通常起きない。起きたら曖昧として退避)
                return MatchResult(line, reason=AMBIGUOUS, candidates=hits)

        # --- 優先順位2: 計算書×正規化名の候補全体 ---
        cands = self.seiten.candidates_by_name(line.stmt, nm)
        if len(cands) == 0:
            return MatchResult(line, reason=NO_NAME)
        if len(cands) == 1:
            return MatchResult(line, code=cands[0].code, record=cands[0])

        # --- 優先順位3: 複数候補。親スコープで絞れなかった -> 要曖昧解消 ---
        return MatchResult(line, reason=AMBIGUOUS, candidates=cands)

    def match_all(self, lines):
        return [self.match_line(ln) for ln in lines]
