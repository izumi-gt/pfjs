# -*- coding: utf-8 -*-
"""
科目名の正規化と「指紋(fingerprint)」生成。名寄せの土台。

- 幹(global)指紋: 正規化名 + 計算書区分      （法人をまたいで同一）
- 枝概念(local concept)指紋: 正規化名 + 親幹コード + 収入支出区分 （法人をまたいで同一）
- 枝実体(local instance): 法人別。fact が参照する L-{法人}-NNNN。概念コードを1本持つ。

表記ゆれをここで吸収しないと名寄せが効かないため、正規化を強めにかける。
"""
import re
import unicodedata


def normalize(name):
    """科目名を正規化キーへ。全半角空白除去・付番除去・記号統一・旧字統一など。"""
    s = name or ""
    # 全角->半角の互換正規化（数字・英字・記号）
    s = unicodedata.normalize("NFKC", s)
    # 空白除去（全角空白含む）
    s = s.replace("\u3000", "").replace(" ", "").replace("\t", "")
    # 算式注記の除去:  「＝（３）＋（６）」等の末尾算式。最初の = 以降を落とす。
    #   （タイプ4 拠点明細では連番がレイアウト上で名前外に落ち「＝（）＋（）」の
    #     ように空括弧＋算式記号だけが残るため、突合前にここで除去する。）
    s = re.sub(r"[=＝].*$", "", s)
    # 連番・条項括弧の除去:  （１）(1) （一） 等（NFKC後は半角数字）。
    s = re.sub(r"[（(][0-9０-９一二三四五六七八九十]+[）)]", "", s)
    # 空括弧の除去:  （） ()  （連番がレイアウトで落ちて空括弧だけ残った場合）
    s = re.sub(r"[（(]\s*[）)]", "", s)
    # 末尾に残った算式記号の除去:  「当期末支払資金残高＋」等（＝を伴わない (11)＋(12) 形）。
    s = re.sub(r"[＋+－−\-＝=]+$", "", s)
    # 中黒・ハイフン類の統一
    s = s.replace("・", "").replace("ー", "ー")
    s = s.replace("−", "-").replace("―", "-").replace("─", "-")
    # 旧字・異体字の代表的な統一（必要に応じて拡張）
    table = {"齋": "斎", "髙": "高", "﨑": "崎", "渕": "淵"}
    for a, b in table.items():
        s = s.replace(a, b)
    return s.strip()


def global_fingerprint(norm_name, statement):
    """幹科目の指紋。statement: 'CF'/'PL'/'BS'。"""
    return f"G|{statement}|{norm_name}"


def concept_fingerprint(norm_name, parent_global_code, io):
    """
    枝概念の指紋（全国共通で名寄せ）。
    norm_name: 正規化名 / parent_global_code: 親幹コード / io: 収入・支出・収益・費用・資産・負債・純資産 等
    """
    return f"C|{parent_global_code}|{io}|{norm_name}"
