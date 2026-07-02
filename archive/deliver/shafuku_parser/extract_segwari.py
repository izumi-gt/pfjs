# -*- coding: utf-8 -*-
"""
様式タイプ2: 事業区分別内訳表（1-2 / 2-2 / 3-2）。
標準6列: 社会福祉事業 / 公益事業 / 収益事業 / 合計 / 内部取引消去 / 法人合計。
（法人により列数が変わりうるが、当面は標準6列。ヘッダ語の右端を列アンカーにする。）

戻り値: [(name, {col_index: value}), ...]
col_index 0..5 が上記6列の順。値の無い列は dict に現れない。

----------------------------------------------------------------------
堅牢化メモ（2-2 を5社で検証した結果の設計）:

1. 金額の取りこぼし対策（旧 行分離バグ）
   pdfplumber の extract_words が金額を "162,86"+"2"+","+"7"... と分断するため、
   word 単位の割り当てが壊れていた。→ page.chars から数字・カンマ・符号を
   ベースライン単位で連結して数値を再構成する。行は「数値の top」をアンカーに
   し、科目名文字を最近傍の数値行へ±4.5pxでスナップする。背の高い行でも
   科目名と数値が分離しない。

2. 縦書きマージン文字の除去（過学習を避けた3段階）
   左端には2本の縦書きラベル列がある:
     - 区分ラベル列（サービス活動増減の部 等）  x0 ≈ 38–40
     - 収支区分ラベル列（収益/費用）            x0 ≈ 46–47
   これらが科目名の先頭や内部に融合する。座標で確実に分離する:
     stage 0 (ベースライン): 真の科目名文字は同一 top を共有する。縦書きラベル
        文字は top が ~1.5px ずれ、隣接文字と x で重なる → 最頻 top から外れる
        文字を除去（"経の常増減差額" の の を除去）。
     stage 1 (ギャップ): 本体から明確な間隔で離れた先頭1文字を除去
        （計行・背の高い行に強い）。
     stage 2 (x ジャンプ): 本体(body_edge≈54)直前に密着したマージン文字を、
        [body_edge未満の文字群][ギャップ][body_edge以上の本体] のパターンで除去。
        本体が左寄せの計行（経常増減差額 等。1本の連続ランで body_edge を越える）
        は stage 1/2 で削られず温存される。
   body_edge は様式ごとに「レベル0科目名(長さ≥4, x0≥44)の最頻 x0」から動的決定。

改ページ: ヘッダ（列ラベル）出現ページで列アンカーを測り、後続ページへ引き継ぐ。
全ページの文字・数値を連結ストリームとして扱い、行束ねを跨ページで行う。
"""
from collections import defaultdict, Counter
from . import layout
import re

# 半角の数値グリフ（金額由来）。科目名の連番は全角 '（１）' なのでここには含めない。
# 値列の最上位桁が name 領域へ食い込んだ場合に name から弾くために使う。
_HALFWIDTH_NUMGLYPH = set("0123456789,-")

# 会計期間ヘッダ '（自）令和…'/'（至）令和…' とその誤分割断片（'（自' 等）を除外する。
# ヘッダは行頭が '（自'/'（至' で始まる。科目名の '（自立）' 等は括弧が名前の途中に
# 現れる（行頭ではない）ため、行頭アンカーで安全に区別できる。
_re_jishi = re.compile(r"^[（(]\s*[自至]")

# 列ラベル（出現順）。内部取引消去は2語に割れることがあるので候補で吸収。
COL_LABELS = [
    ["社会福祉事業"],
    ["公益事業"],
    ["収益事業"],
    ["合計"],
    ["内部取引消去", "消去"],   # "内部取引"+"消去" の右側=消去 の右端を使う
    ["法人合計"],
]

_DIGIT = set("0123456789,，△▲-−")


def _is_dig(t):
    return t in _DIGIT or t.isdigit()


def _detect_columns(words):
    """6列の右端アンカーを返す。見つからない列は None。"""
    edges = [None] * len(COL_LABELS)
    for i, cands in enumerate(COL_LABELS):
        best = None
        for w in words:
            t = w["text"]
            if t in cands:
                # 同名語が複数あれば最も上(ヘッダ行)を優先
                if best is None or w["top"] < best["top"]:
                    best = w
        if best is not None:
            edges[i] = best["x1"]
    return edges


def _build_numbers(chars, value_first_edge):
    """page.chars から数字/カンマ/符号をベースライン単位で連結し数値語を再構成。
    値領域（value_first_edge-30 より右）の数値だけを対象にする。
    戻り: [{"text","x0","x1","top"}, ...]（to_int 可能なもののみ）"""
    rows = defaultdict(list)
    digs = [c for c in chars if _is_dig(c["text"]) and c["x0"] > value_first_edge - 30]
    for c in sorted(digs, key=lambda c: (round(c["top"], 1), c["x0"])):
        for k in list(rows):
            if abs(k - c["top"]) <= 2.0:
                rows[k].append(c)
                break
        else:
            rows[c["top"]].append(c)
    nums = []
    for top, cs in rows.items():
        cs.sort(key=lambda c: c["x0"])
        i = 0
        while i < len(cs):
            j = i
            run = [cs[i]]
            while j + 1 < len(cs) and (cs[j + 1]["x0"] - cs[j]["x1"]) < cs[j]["width"] * 1.6:
                j += 1
                run.append(cs[j])
            txt = "".join(c["text"] for c in run)
            if layout.to_int(txt) is not None:
                nums.append({"text": txt, "x0": run[0]["x0"],
                             "x1": run[-1]["x1"], "top": top})
            i = j + 1
    return nums


def _snap_rows(tops, tol=2.5):
    """近接する数値 top をクラスタリングして行アンカー(中心)を返す。"""
    tops = sorted(tops)
    cl = []
    for t in tops:
        if cl and t - cl[-1][-1] <= tol:
            cl[-1].append(t)
        else:
            cl.append([t])
    return [sum(c) / len(c) for c in cl]


def _body_edge(words, value_first_edge):
    """レベル0科目名（長さ≥4, x0≥44）の最頻 x0 を本体左端とみなす。"""
    cand = [round(w["x0"]) for w in words
            if (w["x0"] + w["x1"]) / 2 < value_first_edge - 25
            and len(w["text"]) >= 4 and w["x0"] >= 44]
    return Counter(cand).most_common(1)[0][0] if cand else 54


def _clean_name_chars(chs, body_edge):
    """3段階で先頭・内部の縦書きマージン文字を除去し、残った文字列を返す（座標のみ）。"""
    chs = sorted(chs, key=lambda c: c["x0"])
    # ---- stage 0: 最頻ベースラインの文字だけ残す（内部マージン文字を除去）----
    if len(chs) >= 3:
        tc = Counter(round(c["top"] * 2) / 2 for c in chs)   # 0.5px バケット
        modal_top, _ = tc.most_common(1)[0]
        chs = [c for c in chs if abs(c["top"] - modal_top) <= 0.8]
        chs = sorted(chs, key=lambda c: c["x0"])
    if not chs:
        return []
    # ---- stage 1: 本体から離れた先頭1文字を除去 ----
    changed = True
    while changed and len(chs) >= 2:
        changed = False
        if chs[1]["x0"] - chs[0]["x1"] > chs[0]["width"] * 0.9:
            chs = chs[1:]
            changed = True
    if not chs:
        return []
    # ---- stage 2: 本体に密着したマージン文字を [<edge][gap][>=edge] で除去 ----
    if chs[0]["x0"] < body_edge - 1.5:
        cut = None
        for k in range(len(chs) - 1):
            if chs[k]["x0"] < body_edge - 1.5:
                gap = chs[k + 1]["x0"] - chs[k]["x1"]
                if gap > chs[k]["width"] * 0.5:
                    cut = k + 1
        if cut is not None and any(c["x0"] >= body_edge - 1.5 for c in chs[cut:]):
            chs = chs[cut:]
    return chs


def _clean_name(chs, body_edge):
    """3段階クリーニング後の科目名文字列を返す。"""
    return "".join(c["text"] for c in _clean_name_chars(chs, body_edge)).strip()


def extract_segwari(pdf, statement):
    """statement は使わないが他タイプとシグネチャを揃える。

    改ページ対応: 列アンカーはヘッダ出現ページで確定し後続へ引き継ぐ。
    文字・数値は全ページ連結ストリームとして扱い行束ねする。
    """
    # --- 列アンカーを最初に検出できたページで確定 ---
    edges = None
    for pg in pdf.pages:
        e = _detect_columns(pg.extract_words(use_text_flow=False,
                                             keep_blank_chars=False))
        if sum(1 for x in e if x is not None) >= 4:
            edges = e
            break
    if edges is None:
        raise ValueError("事業区分別: 列ヘッダ検出不足（全ページ）")
    present = [(i, x) for i, x in enumerate(edges) if x is not None]
    present_edges = [x for _, x in present]
    present_idx = [i for i, _ in present]
    first_edge = min(present_edges)

    # --- 全ページの文字・数値・科目名語を連結（ページ間で top をオフセット）---
    all_chars = []
    all_words = []
    y_off = 0.0
    for pg in pdf.pages:
        for c in pg.chars:
            d = dict(c)
            d["top"] = c["top"] + y_off
            all_chars.append(d)
        for w in pg.extract_words(use_text_flow=False, keep_blank_chars=False):
            d = dict(w)
            d["top"] = w["top"] + y_off
            all_words.append(d)
        y_off += pg.height + 10.0

    body_edge = _body_edge(all_words, first_edge)
    nums = _build_numbers(all_chars, first_edge)
    if not nums:
        return []
    row_tops = _snap_rows([n["top"] for n in nums], tol=2.5)

    # 科目名文字を最近傍数値行へスナップ
    #   値列の数字が name 領域 (x < first_edge-20) に左へ食い込むことがある
    #   （金額の最上位桁が境界を越える）。半角の数字・カンマ・符号は科目名には
    #   現れない（科目名の連番は全角 '（１）' でこれは別途 normalize が処理）ため、
    #   半角の数値グリフは name から除外して混入を断つ。
    name_chars = defaultdict(list)
    for c in all_chars:
        cx = (c["x0"] + c["x1"]) / 2
        if cx >= first_edge - 20:
            continue
        if c["text"] in _HALFWIDTH_NUMGLYPH:
            continue
        best = min(row_tops, key=lambda rt: abs(rt - c["top"]))
        if abs(best - c["top"]) <= 4.5:
            name_chars[round(best, 1)].append(c)

    out = []
    for rt in sorted(set(round(t, 1) for t in row_tops)):
        name = _clean_name(name_chars.get(rt, []), body_edge)
        if not name or _skip(name):
            continue
        rownums = [n for n in nums if abs(n["top"] - rt) <= 4.5]
        raw = layout.assign_columns(
            [{"text": n["text"], "x1": n["x1"]} for n in rownums], present_edges)
        assign = {present_idx[k]: v for k, v in raw.items()}
        out.append((name, assign))
    return out


def _skip(name):
    n = name.strip()
    if n == "":
        return True
    # 期間ヘッダ「（自）令和…/（至）令和…」とその誤分割断片（'（自' 等）を除外。
    #   かつて '（自'/'（至' を部分一致で除外していたが、これは科目名
    #   '訓練等給付費収益（自立）' を誤って落とす。ヘッダは行頭が '（自'/'（至'
    #   で始まり、科目名は括弧が名前の途中に現れるので、行頭アンカーで区別する。
    bad_substr = ("勘定科目", "様式", "令和", "単位：円", "内訳表", "1/", "社会福祉法人")
    if any(b in n for b in bad_substr):
        return True
    if _re_jishi.search(n):
        return True
    if n in ("資産の部", "負債の部", "純資産の部"):
        return True
    return False
