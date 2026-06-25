# -*- coding: utf-8 -*-
"""
正典ローダ。seiten_master.csv を読み、照合に最適化したインデックスを構築する。

教訓の反映:
- encoding='utf-8-sig', newline='' で読む(BOM対策)。
- 階層レベルセル(L0..L4)は .isdigit() で数値判定してから int 化する
  (数値文字列以外を黙って誤カウントしないため)。
- 正規化名は単独で一意にならない(同名重複: CF27/PL25/BS5種, 最大9箇所)。
  ゆえに「計算書×正規化名 -> 候補レコード(複数)」の1対多インデックスと、
  「親パス -> 子レコード」のスコープインデックスの2本を持つ。
"""
import csv
from collections import defaultdict
from normalize import normalize

# 親パスを構成するレベルのコードセグメント列(L4=小区分の親まで)
_PATH_SEGMENTS = ["L0", "L1", "L2", "L3", "L3.5"]


class SeitenRecord:
    """正典1行の照合用ビュー。"""
    __slots__ = (
        "code", "stmt", "L0", "L1", "L2", "L3", "L35", "L4",
        "区分", "活動部", "大区分", "中区分", "内訳グループ", "小区分",
        "階層種別", "正規名", "norm", "is_amount", "is_total", "is_optional",
    )

    def __init__(self, d):
        self.code = d["code"]
        self.stmt = d["計算書"]
        self.L0 = d["L0"]
        self.L1 = d["L1"]
        self.L2 = d["L2"]
        self.L3 = d["L3"]
        self.L35 = d["L3.5"]
        self.L4 = d["L4"]
        self.区分 = d["区分"]
        self.活動部 = d["活動・部"]
        self.大区分 = d["大区分"]
        self.中区分 = d["中区分"]
        self.内訳グループ = d["内訳グループ"]
        self.小区分 = d["小区分"]
        self.階層種別 = d["階層種別"]
        self.正規名 = d["正規名"]
        self.norm = normalize(d["正規名"])
        self.is_amount = _to_int(d["is_amount"])
        self.is_total = _to_int(d["is_total"])
        self.is_optional = _to_int(d["is_optional"])

    def parent_path(self):
        """この行の親パス(計算書 + L0..L3.5 のコード列)。L4(小区分)を除いた上位。"""
        return (self.stmt,) + tuple(getattr(self, _slot_for(seg)) for seg in _PATH_SEGMENTS)

    def depth(self):
        """この行の最深レベル番号。0=大区分相当でなく、階層種別から決める。"""
        return _DEPTH_BY_KIND.get(self.階層種別, -1)


# 階層種別 -> 深度(浅い順)。様式別の期待深度判定に使う
_DEPTH_BY_KIND = {
    "大区分": 2,
    "中区分": 3,
    "内訳グループ": 4,
    "小区分": 5,
    "集計": 9,  # 集計行は別枠
}


def _slot_for(seg):
    return {"L0": "L0", "L1": "L1", "L2": "L2", "L3": "L3", "L3.5": "L35"}[seg]


def _to_int(v):
    v = (v or "").strip()
    return int(v) if v.isdigit() else 0


class Seiten:
    """正典マスタ全体と照合用インデックス。"""

    def __init__(self, records):
        self.records = records
        # 計算書 × 正規化名 -> 候補レコード(複数ありうる)
        self.by_norm = defaultdict(list)
        # 親パス -> その直下の子レコード
        self.by_parent = defaultdict(list)
        # code -> レコード
        self.by_code = {}
        for r in records:
            self.by_norm[(r.stmt, r.norm)].append(r)
            self.by_code[r.code] = r
            self.by_parent[r.parent_path()].append(r)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        records = [SeitenRecord(d) for d in rows]
        return cls(records)

    def candidates_by_name(self, stmt, norm_name):
        return self.by_norm.get((stmt, norm_name), [])


if __name__ == "__main__":
    s = Seiten.load("seiten_master.csv")
    print("records:", len(s.records))
    print("by_norm keys:", len(s.by_norm))
    print("by_parent keys:", len(s.by_parent))
    print("is_total:", sum(r.is_total for r in s.records))
    print("is_optional:", sum(r.is_optional for r in s.records))
