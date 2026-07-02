# -*- coding: utf-8 -*-
"""
科目レジストリ。パース行を「コード」に解決する中核。

- 幹(global)マスタは shafuku_db_engine.masters から読み込む（全国共通・固定）。
- 既知 → そのコードを返す。
- 未知 → 止まらない。親幹の配下に概念コード(LC-)を自動採番し、
  法人別実体コード(L-)を採番して、両方を「新科目候補」としてレビューキューへ。

この設計により取り込みは無人で完走し、人は後でキューをバッチでレビューする。
"""
from . import naming


class ReviewItem:
    def __init__(self, kind, code, name, norm, parent, io, statement,
                 corp_no, form, page, raw):
        self.kind = kind            # "concept" / "instance"
        self.code = code
        self.name = name
        self.norm = norm
        self.parent = parent        # 親幹コード or 親概念コード
        self.io = io
        self.statement = statement
        self.corp_no = corp_no
        self.form = form
        self.page = page
        self.raw = raw              # 元科目名（生）

    def row(self):
        return [self.kind, self.code, self.name, self.norm, self.parent,
                self.io, self.statement, self.corp_no, self.form, self.page, self.raw]


REVIEW_HEADER = ["種別", "コード", "正規名", "正規化キー", "親コード",
                 "収支区分", "計算書", "法人番号", "様式", "ページ", "元科目名"]


class Registry:
    def __init__(self, global_master, statement, corp_no):
        """
        global_master: [(code, ..., 正規名, ...)] その計算書の幹リスト
        statement: 'CF'/'PL'/'BS'
        """
        self.statement = statement
        self.corp_no = corp_no
        # 幹: 正規化名 -> code
        self.global_by_norm = {}
        self.global_codes = set()
        for row in global_master:
            # 正規名の位置: CF/PL は row[3]、BS も row[3]（BS行は6要素 (code,区分,中区分,正規名,is_total,indent)）。
            code, name = row[0], row[3]
            self.global_by_norm[naming.normalize(name)] = code
            self.global_codes.add(code)
        # 概念(LC): fingerprint -> code
        self.concept_by_fp = {}
        self._concept_seq = 0
        # 実体(L): (concept_code) -> code（法人内で概念ごとに1実体）
        self.instance_by_concept = {}
        self._instance_seq = 0
        # レビューキュー
        self.review = []

    # ---- 幹の解決（既知のみ。未知の大区分/計は通常起きないが、起きたら新科目候補へ）----
    def resolve_global(self, name):
        n = naming.normalize(name)
        return self.global_by_norm.get(n)

    # ---- 枝（小区分/明細）の解決: 未知なら自動採番＋キュー ----
    def resolve_local(self, name, parent_global_code, io, form, page):
        n = naming.normalize(name)
        fp = naming.concept_fingerprint(n, parent_global_code, io)
        # 概念コード
        if fp in self.concept_by_fp:
            cc = self.concept_by_fp[fp]
            new_concept = False
        else:
            self._concept_seq += 1
            cc = f"LC-{self._concept_seq:05d}"
            self.concept_by_fp[fp] = cc
            new_concept = True
            self.review.append(ReviewItem(
                "concept", cc, name, n, parent_global_code, io,
                self.statement, self.corp_no, form, page, name))
        # 実体コード（この法人で概念ごとに1つ）
        if cc in self.instance_by_concept:
            ic = self.instance_by_concept[cc]
        else:
            self._instance_seq += 1
            ic = f"L-{self.corp_no}-{self._instance_seq:04d}"
            self.instance_by_concept[cc] = ic
            self.review.append(ReviewItem(
                "instance", ic, name, n, cc, io,
                self.statement, self.corp_no, form, page, name))
        return ic, cc, new_concept
