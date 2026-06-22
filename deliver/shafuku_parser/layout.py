# -*- coding: utf-8 -*-
"""
レイアウト検出のコア。座標ベース＋動的列検出＋改ページ対応。

設計の要点（このチャットで合意した内容）:
 - 列境界は「ページ」でなく「様式」単位で確定する。ヘッダ語(予算/決算など)が
   出現したページで右端アンカーを測り、ヘッダの無い後続ページへ引き継ぐ。
   ヘッダ再出現時は前回との差を検知してアラート（レイアウト変動の検出）。
 - 拠点/区分の境界はページ番号でなく「見出し行」をアンカーに動的分割。
 - 行の束ねは全ページ連結ストリームで行い、改ページ分断に強くする。
 - 値は右寄せなので、各数値の右端x1を列アンカー(右端)に最近傍で割り当てる。
"""
import re

NUM_RE = re.compile(r"^-?[0-9,]+$")


def to_int(t):
    t = t.replace(",", "").replace("△", "-").replace("▲", "-").replace("−", "-")
    return int(t) if re.fullmatch(r"-?\d+", t) else None


def is_num(t):
    return bool(NUM_RE.match(t.replace("△", "-").replace("▲", "-").replace("−", "-")))


# ---- ヘッダ語の辞書（様式種別ごとの列ラベル）----
# 値列の右端アンカーを測るために使う。順序が列順。
HEADER_SETS = {
    # 法人単位CF(1-1)/拠点CF(1-4)
    "CF_BUDGET": ["予算(A)", "決算(B)", "差異(A)-(B)"],
    # 法人単位PL(2-1)/拠点PL(2-4)/法人BS(3-1相当)・拠点BS(3-4)
    "PL_YOY": ["当年度決算(A)", "前年度決算(B)", "増減(A)-(B)"],
    "BS_YOY": ["当年度末", "前年度末", "増減"],
    # 事業区分別(×-2)/拠点別内訳(×-3): 社福/公益/収益/合計/内部取引消去/法人合計 等
    "SEGWARI": ["社会福祉事業", "公益事業", "収益事業", "合計", "内部取引消去", "法人合計"],
}


def group_rows(words, y_tol=3.0):
    """words を y(top) でグルーピング。改ページ連結後の通しwordsを渡す前提でもよいが、
    通常はページ単位で呼び、呼び出し側でページをまたいで連結する。"""
    rows = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        for row in rows:
            if abs(row["top"] - w["top"]) <= y_tol:
                row["words"].append(w)
                break
        else:
            rows.append({"top": w["top"], "words": [w]})
    for r in rows:
        r["words"].sort(key=lambda w: w["x0"])
    rows.sort(key=lambda r: r["top"])
    return rows


def detect_value_columns(words, expected_labels=None):
    """
    ヘッダ行から値列の右端アンカー(x1)のリストを返す。
    見つからなければ None（=このページにはヘッダ無し→呼び出し側で前回値を引き継ぐ）。
    expected_labels が与えられればそのラベル集合で照合、無ければ既知セットを総当り。
    戻り: (col_names, right_edges)  例 (["予算","決算","差異"], [304.4,380.6,462.8])
    """
    sets = [expected_labels] if expected_labels else list(HEADER_SETS.values())
    for labels in sets:
        found = {}
        for w in words:
            t = w["text"]
            for lab in labels:
                # 完全一致 or ラベル先頭一致（"予算(A)" を "予算" でも拾えるよう緩める）
                if t == lab or t.replace(" ", "") == lab.replace(" ", ""):
                    found[lab] = w["x1"]
        # 半数以上のラベルが揃えばヘッダ行とみなす
        if len(found) >= max(2, len(labels) // 2):
            col_names = [_short(lab) for lab in labels if lab in found]
            edges = [found[lab] for lab in labels if lab in found]
            return col_names, edges
    return None


def _short(label):
    # "予算(A)" -> "予算" 等、列名を正規化
    return re.sub(r"\(.*?\)", "", label).replace("(A)-(B)", "").strip()


def assign_columns(value_words, right_edges, tol=40.0):
    """
    数値 words を右端x1で列アンカーに最近傍割り当て。
    戻り: 列index -> 値(int)。複数が同じ列に来たら最後を採用（通常起きない）。
    tol を超えて離れた数値は無視（脚注番号など）。
    """
    out = {}
    for w in value_words:
        v = to_int(w["text"])
        if v is None:
            continue
        x1 = w["x1"]
        best_i, best_d = None, 1e9
        for i, edge in enumerate(right_edges):
            d = abs(edge - x1)
            if d < best_d:
                best_d, best_i = d, i
        if best_i is not None and best_d <= tol + 40:  # 右寄せの数値は右端より右に出るので余裕
            out[best_i] = v
    return out


# ---- 見出し（拠点/区分）アンカー検出 ----
SEGMENT_TITLE_RE = re.compile(r"(.+?拠点区分|.+?本部拠点|.+?事業区分)")
STATEMENT_WORDS = ["資金収支計算書", "事業活動計算書", "貸借対照表", "資金収支内訳表",
                   "事業活動内訳表", "貸借対照表内訳表"]


def extract_segment_title(rows):
    """
    ページ内の行から拠点/区分見出しを推定して返す（無ければ None）。
    例: "社会福祉法人神奈川厚生福祉会本部拠点拠点区分" + "資金収支計算書" → "本部拠点"
    """
    for r in rows:
        text = "".join(w["text"] for w in r["words"])
        if "拠点区分" in text and any(s in text for s in STATEMENT_WORDS) is False:
            # 見出し行（科目行ではない）の可能性。拠点名を抽出。
            m = re.search(r"(.+?)拠点区分", text)
            if m:
                name = m.group(1)
                # 法人名プレフィックスを除去（"社会福祉法人○○本部拠点" → "本部拠点"）
                name = re.sub(r"^社会福祉法人.*?会", "", name)
                return name.strip() or None
    return None


def margin_strip(label_words, gap=2.0):
    """左マージンの縦書き1文字（"勘定科目"の縦ラベル等）を除去。"""
    lw = list(label_words)
    while len(lw) >= 2 and len(lw[0]["text"]) == 1 and (lw[1]["x0"] - lw[0]["x1"]) >= gap:
        lw.pop(0)
    return lw


def label_column_mode(rows, value_first_edge, y_label_max_cx=None):
    """
    全行の「margin_strip 後の先頭ラベル語 x0」の最頻値を返す。
    これが実際の勘定科目カラムの左端。縦書きマージン語が混入した行はこれより左に出る。
    """
    from collections import Counter
    xs = []
    for r in rows:
        lab = [w for w in r["words"]
               if (w["x0"] + w["x1"]) / 2 < value_first_edge - 25]
        lw = margin_strip(lab)
        if lw:
            xs.append(round(lw[0]["x0"]))
    if not xs:
        return None
    # 最頻値（同数なら大きい方＝右側を採用：マージン混入は左にズレるため）
    c = Counter(xs)
    top = max(c.items(), key=lambda kv: (kv[1], kv[0]))
    return top[0]


def clean_label(label_words, col_mode, tol=4.0, page_chars=None, row_top=None, y_tol=3.0):
    """
    縦書きマージン文字の融合を除去する。
    識別の決め手は x位置ではなく「ベースライン(top)の一致」:
      本来の科目名は同一topに等間隔で並ぶ。縦書きマージン文字はtopがズレる。
    page_chars があれば、先頭語のx範囲の文字のうち最頻topの文字だけ残す。
    """
    lw = margin_strip(label_words)
    if not lw:
        return ""
    first = lw[0]
    rest = "".join(w["text"] for w in lw[1:])
    if col_mode is None or first["x0"] >= col_mode - tol:
        return first["text"] + rest
    if page_chars is not None and row_top is not None:
        chs = [c for c in page_chars
               if first["x0"] - 0.5 <= c["x0"] <= first["x1"] + 0.5
               and abs(c["top"] - row_top) <= y_tol + 4]
        chs.sort(key=lambda c: c["x0"])
        if chs:
            i = 0
            while i < len(chs) - 1:
                c = chs[i]
                gap = chs[i + 1]["x0"] - c["x1"]
                if c["x0"] < col_mode - tol and gap >= 1.5:
                    i += 1
                    continue
                break
            kept = chs[i:]
            name = "".join(c["text"] for c in kept)
            if name:
                return name + rest
    if len(first["text"]) >= 2:
        w_per_char = (first["x1"] - first["x0"]) / len(first["text"])
        drop = int((col_mode - first["x0"]) / w_per_char)
        drop = max(0, min(drop, len(first["text"]) - 1))
        return first["text"][drop:] + rest
    return first["text"] + rest


def label_from_chars(page_chars, top, y_tol, col_mode, value_first_edge, tol=3.0):
    """
    page.chars を使い、行(top±y_tol)かつ x0>=col_mode-tol かつ値列より左の文字だけを
    連結して科目名を作る。縦書きマージン文字（col_modeより左）を座標で確実に除外できる。
    """
    if col_mode is None:
        return None
    chs = [c for c in page_chars
           if abs(c["top"] - top) <= y_tol
           and c["x0"] >= col_mode - tol
           and (c["x0"] + c["x1"]) / 2 < value_first_edge - 20]
    chs.sort(key=lambda c: c["x0"])
    return "".join(c["text"] for c in chs).strip()
