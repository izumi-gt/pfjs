# -*- coding: utf-8 -*-
"""
様式タイプ1: 法人単位（1-1 / 2-1 / 3-1）。
- 1-1/2-1: 単一ブロック。列=(予算,決算,差異) または (当年度,前年度,増減)。
- 3-1   : 貸借対照表。左ブロック(資産)と右ブロック(負債・純資産)が横並び。
          各ブロックに 当年度末/前年度末/増減。中央 x で左右に分割して2回処理する。

戻り値: [(name, {col_index: value}), ...]  （上から出現順）
列の意味(metric)は呼び出し側が様式から決める（schema.METRICS_BY_FORM）。
"""
from . import layout


def _parse_block(rows, edges, label_xmax, label_xmin=0.0):
    """1ブロック分。edges=値列の右端アンカー。label_xmax=ラベルとみなす中心xの上限。"""
    out = []
    first_edge = min(edges)
    for r in rows:
        lab, val = [], []
        for w in r["words"]:
            cx = (w["x0"] + w["x1"]) / 2
            if label_xmin <= cx < min(first_edge - 25, label_xmax):
                lab.append(w)
            elif cx >= first_edge - 25 and cx <= max(edges) + 30:
                val.append(w)
        lw = layout.margin_strip(lab)
        if not lw:
            continue
        name = "".join(w["text"] for w in lw)
        if _skip(name):
            continue
        assign = layout.assign_columns(val, edges)
        if not val:
            # 値ゼロ行（全列空）も科目としては存在。空dictで返す。
            out.append((name, {}))
        elif assign:
            out.append((name, assign))
    return out


def _skip(name):
    bad = ("勘定科目", "様式", "令和", "（自）", "（至）", "単位：円", "1/1", "1/")
    return any(b in name for b in bad) or name.strip() == ""


def _strip_vertical_margin_words(words):
    """左端の縦書き区分/収支ラベル列（'増''の''事''業'… の単字）を除去する。
    これらは独自の top を持つ単字 word で、値数字の top と近接して group_rows で
    値行を奪い、科目（例: 事務費）から金額が外れる原因になる（PL/CF の 1-1/2-1 で発生）。

    判定（座標のみ・過学習回避）:
      - 実科目名の左端 = 「2文字以上のラベル語」の x0 の最頻値 body_x0 を実測。
      - x0 < body_x0 - 6 の帯に居る *単一文字* word を縦書きマージンとみなし除去。
        （左寄せの計・差額行は2文字以上＝body帯か、長語なので誤除去しない。）
    """
    from collections import Counter

    def is_numlike(t):
        return layout.is_num(t)

    label_words = [w for w in words if not is_numlike(w["text"])]
    multi = [w for w in label_words if len(w["text"].strip()) >= 2]
    if not multi:
        return words
    body_x0 = Counter(round(w["x0"]) for w in multi).most_common(1)[0][0]
    cut = body_x0 - 6
    kept = []
    for w in words:
        t = w["text"].strip()
        if (not is_numlike(t)) and len(t) == 1 and w["x0"] < cut:
            continue  # 縦書きマージンの単字 → 除去
        kept.append(w)
    return kept


def extract_houjin(pdf, statement):
    """
    statement: 'CF'/'PL'/'BS'。
    CF/PL: 単一ブロック。BS: 左右2ブロックを結合して返す。
    """
    pg = pdf.pages[0]
    words = pg.extract_words(use_text_flow=False, keep_blank_chars=False)
    words = _strip_vertical_margin_words(words)
    rows = layout.group_rows(words)

    if statement != "BS":
        cols = layout.detect_value_columns(words)
        if not cols:
            raise ValueError("法人単位: 値列ヘッダが検出できません")
        _, edges = cols
        return _parse_block(rows, edges, label_xmax=min(edges) - 20)

    # ---- BS: 左右2ブロック ----
    # ヘッダ "当年度末/前年度末/増減" が左右に2組出る。x で2グループに分ける。
    hdrs = [w for w in words if w["text"] in ("当年度末", "前年度末", "増減")]
    hdrs.sort(key=lambda w: w["x0"])
    if len(hdrs) < 6:
        raise ValueError("BS 3-1: 左右ブロックのヘッダが揃いません")
    left_edges = [w["x1"] for w in hdrs[:3]]
    right_edges = [w["x1"] for w in hdrs[3:6]]
    # 右ブロックのラベル列開始x を実測する。右ヘッダ(値列)より左にあり、
    # かつ左ブロック値列(max left edge)より右にあるラベル語の最小x0。
    right_label_x0s = [w["x0"] for w in words
                       if left_edges[-1] < w["x0"] < min(right_edges) - 40
                       and not layout.is_num(w["text"])]
    if right_label_x0s:
        right_label_start = min(right_label_x0s)
        split_x = (left_edges[-1] + right_label_start) / 2
    else:
        split_x = (left_edges[-1] + min(w["x0"] for w in hdrs[3:6])) / 2

    left_rows, right_rows = [], []
    for r in rows:
        lw = [w for w in r["words"] if w["x0"] < split_x]
        rw = [w for w in r["words"] if w["x0"] >= split_x]
        if lw:
            left_rows.append({"top": r["top"], "words": lw})
        if rw:
            right_rows.append({"top": r["top"], "words": rw})

    left = _parse_block(left_rows, left_edges, label_xmax=min(left_edges) - 20)
    right = _parse_block(right_rows, right_edges,
                         label_xmin=split_x, label_xmax=min(right_edges) - 20)
    return left + right
