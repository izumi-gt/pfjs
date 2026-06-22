# -*- coding: utf-8 -*-
"""
様式タイプ4: 拠点ごとの明細（1-4 / 2-4 / 3-4）。

構造（あしたか・神奈川で確認）:
  1拠点 = 1計算書が拠点ごとに繰り返される。
  - 見出し行 "○○拠点区分拠点区分" + 計算書名(資金収支計算書/事業活動計算書/貸借対照表)
    が拠点境界アンカー。見出し＋列ヘッダを持つページが拠点の先頭。
  - CF/PL は1拠点が複数ページに跨る（縦分割＝行が次ページへ続く）。続きページは
    見出しも列ヘッダも持たない → 直前拠点を継承し、列アンカーも先頭ページから継承。
  - BS は1拠点=1ページ。資産(左ブロック)と負債・純資産(右ブロック)が横並び。
    → タイプ1(3-1)の左右2ブロック分割と同型。

タイプ3からの再利用:
  - 数値再構成      : build_numbers（page.chars をベースライン連結）→ _seg._build_numbers と同等
  - 列アンカー検出   : detect_anchors（数値の右端x1クラスタ）
  - 縦書きマージン除去: _seg._clean_name_chars（CF/PL の左2列縦書きラベルに対応）
  - depth          : クリーン後の先頭 全角空白(U+3000)数。x0ラダーでクロスチェック。
  - 行束ね          : 数値 top をアンカーに科目名文字を最近傍へスナップ。
  - 多ページ統合     : 見出し有無で拠点境界を判別（横分割は存在しないので縦連続のみ）。

戻り値:
  extract_kyoten4(pdf, statement) -> [ {kyoten, colnames, rows}, ... ] 拠点ごと
    kyoten  : 拠点名（例 'かぬき学園'）
    colnames: 列名（CF=['予算','決算','差異'] / PL=['当年度決算','前年度決算','増減']
              / BS=['当年度末','前年度末','増減']）。BSは資産/負債で同じ列構成。
    rows    : [(name, depth, {col_index: value}), ...]
              col_index は colnames に対応。BSは資産→負債・純資産の順で連結。
              CF の "備考"(テキスト列) は値列に含めない。

注:
  CF/PL の列ヘッダは値列より左寄せ（予算(A) 右端304 vs 数値右端327等）なので、
  列アンカーは必ず「数値の右端x1クラスタ」から取る（ヘッダ右端は使わない）。
"""
import re
from collections import defaultdict, Counter
from . import layout, extract_segwari as _seg

STMT_WORDS = {
    "CF": "資金収支計算書",
    "PL": "事業活動計算書",
    "BS": "貸借対照表",
}
# 列ヘッダ語（拠点先頭ページ判定にのみ使用。列アンカーには使わない）。
HEADER_HINTS = ("予算(A)", "当年度決算(A)", "当年度末", "勘定科目")

FW_SPACE = ("\u3000", " ", "\t")


# ---------- 数値再構成（タイプ3と同一ロジック） ----------
def build_numbers(chars, xmin):
    rows = defaultdict(list)
    digs = [c for c in chars if _seg._is_dig(c["text"]) and c["x0"] > xmin]
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
            t = "".join(c["text"] for c in run)
            if layout.to_int(t) is not None:
                nums.append({"text": t, "x0": run[0]["x0"], "x1": run[-1]["x1"], "top": top})
            i = j + 1
    return nums


def cluster1d(vals, tol):
    vals = sorted(vals)
    cl = []
    for v in vals:
        if cl and v - cl[-1][-1] <= tol:
            cl[-1].append(v)
        else:
            cl.append([v])
    return cl


def detect_anchors(nums, min_members=4, tol=5):
    """数値の右端x1クラスタ→列右端アンカー。CFは3列(備考は数値なしなので出ない)。"""
    cl = cluster1d([n["x1"] for n in nums], tol)
    return [round(sum(c) / len(c), 1) for c in cl if len(c) >= min_members]


def snap_rows(tops, tol=2.5):
    tops = sorted(tops)
    cl = []
    for t in tops:
        if cl and t - cl[-1][-1] <= tol:
            cl[-1].append(t)
        else:
            cl.append([t])
    return [sum(c) / len(c) for c in cl]


# ---------- 拠点見出し / 先頭ページ判定 ----------
def _first_data_top(pg):
    """ページ最初の「データ行」top（>=2個の数値が同一topに乗る行の最小top）。
    ヘッダ帯/見出し帯の下端推定に使う。日付等の単発数字を除外。"""
    nums = build_numbers(list(pg.chars), 120)
    by_top = defaultdict(int)
    for n in nums:
        by_top[round(n["top"], 1)] += 1
    data = [t for t, c in by_top.items() if c >= 2]
    return min(data) if data else 110.0


def page_title(words, statement, band_bottom):
    """ページの拠点見出し(拠点名)を返す。見出し帯(top<band_bottom)のみ対象。
    見出し語は '○○拠点区分拠点区分'(法人名+拠点名+列見出し連結) の形。
    法人名プレフィックスは '社会福祉法人' 以降〜最初の '拠点区分' 直前までを拠点名とし、
    会/丘/園 等の固定語に依存しない（過学習回避）。"""
    title = None
    stmt_seen = False
    for w in words:
        if w["top"] >= band_bottom:
            continue
        if "拠点区分" in w["text"]:
            # 末尾の重複見出し "拠点区分拠点区分" → 1個に。先頭の法人名を除去。
            raw = re.sub(r"(拠点区分)+$", "", w["text"])
            m = re.match(r"^社会福祉法人(.+)$", raw)
            cand = m.group(1) if m else raw
            # 法人名と拠点名の境界が不明なときは、既知の拠点語尾より後ろを採れないので
            # そのまま採用（DB側の名寄せ・レビューで吸収）。本PDFは法人名が別トークンで
            # 出るため raw に法人名が含まれないケースが多い。
            title = cand.strip() or raw.strip()
        if w["text"] == STMT_WORDS[statement]:
            stmt_seen = True
    return title if (title and stmt_seen) else None


def has_header(words):
    return any(w["text"] in HEADER_HINTS for w in words)


# ---------- 1ブロック（1拠点・縦連続の全ページ）をパース ----------
def _parse_label_rows(all_chars, anchors, value_first_edge, body_edge, name_xmax,
                      name_xmin=0.0, clean_margin=True):
    """数値 top をアンカーに行を作り、科目名・depth・列割当を返す。
       name_xmax: 科目名文字の中心x上限（値列の手前）。
       name_xmin: 科目名文字の中心x下限（BS右ブロックで左ブロック領域を除外）。
       clean_margin: True=縦書きマージン除去(CF/PL)。False=素直に連結(BS)。"""
    nums = [n for n in build_numbers_from(all_chars, anchors)]
    if not nums:
        return []
    row_tops = snap_rows([n["top"] for n in nums], 2.5)
    name_top_min = min(row_tops) - 6 if row_tops else 0

    name_chars = defaultdict(list)
    for c in all_chars:
        cx = (c["x0"] + c["x1"]) / 2
        if cx >= name_xmax or cx < name_xmin or c["text"] in _seg._DIGIT or c["text"].isdigit():
            continue
        if c["top"] < name_top_min:
            continue
        if not row_tops:
            continue
        best = min(row_tops, key=lambda rt: abs(rt - c["top"]))
        if abs(best - c["top"]) <= 4.5:
            name_chars[round(best, 1)].append(c)

    out = []
    for rt in sorted(set(round(t, 1) for t in row_tops)):
        chs = name_chars.get(rt, [])
        if clean_margin:
            kept = _seg._clean_name_chars(chs, body_edge)
        else:
            # BS: stage0(最頻baseline)だけ適用しマージン除去はしない
            kept = sorted(chs, key=lambda c: c["x0"])
            if len(kept) >= 3:
                tc = Counter(round(c["top"] * 2) / 2 for c in kept)
                modal_top, _ = tc.most_common(1)[0]
                kept = [c for c in kept if abs(c["top"] - modal_top) <= 0.8]
                kept = sorted(kept, key=lambda c: c["x0"])
        depth = 0
        while kept and kept[0]["text"] in FW_SPACE:
            depth += 1
            kept = kept[1:]
        name = "".join(c["text"] for c in kept).strip()
        if not name or _skip(name):
            continue
        rownums = [n for n in nums if abs(n["top"] - rt) <= 4.5]
        assign = layout.assign_columns(
            [{"text": n["text"], "x1": n["x1"]} for n in rownums], anchors)
        out.append((name, depth, assign))
    return out


def build_numbers_from(all_chars, anchors):
    """anchors の最左 - 40 より右の数値だけ再構成（科目名内の数字混入を避ける）。"""
    if not anchors:
        return []
    xmin = min(anchors) - 45
    nums = build_numbers(all_chars, xmin)
    # 列アンカー近傍(±tol)に乗る数値のみ採用（備考列のテキスト等を除外）
    keep = []
    for n in nums:
        if any(abs(n["x1"] - a) <= 8 for a in anchors):
            keep.append(n)
    return keep


def _concat_chars(pages):
    """複数ページの chars を top オフセットして連結（縦連続の統合）。"""
    out = []
    y = 0.0
    for pg in pages:
        for c in pg.chars:
            d = dict(c)
            d["top"] = c["top"] + y
            out.append(d)
        y += pg.height + 10.0
    return out


def _body_edge_from_chars(all_chars, value_first_edge, name_top_min=0):
    """レベル0科目名の最頻 x0（縦書きマージン語より右の本体左端）。
       タイプ2/3の _body_edge と同趣旨だが chars ベースで算出。"""
    # 行ごとに最左の「非マージン」文字を集める。マージンは x0<54 かつ単独で離れる。
    cand = []
    rows = defaultdict(list)
    for c in all_chars:
        cx = (c["x0"] + c["x1"]) / 2
        if cx < value_first_edge - 25 and c["top"] >= name_top_min \
                and c["text"] not in _seg._DIGIT and not c["text"].isdigit():
            rows[round(c["top"], 1)].append(c)
    for top, cs in rows.items():
        cs.sort(key=lambda c: c["x0"])
        # 4文字以上の本体行のみ
        txt = "".join(x["text"] for x in cs)
        if len(txt.strip()) >= 4:
            cand.append(round(cs[0]["x0"]))
    if not cand:
        return 62
    c = Counter(cand)
    # 縦書きマージン(42/52)を避け、最頻のうち >=58 を優先
    common = [x for x, _ in c.most_common() if x >= 58]
    return common[0] if common else c.most_common(1)[0][0]


# ---------- BS（左右2ブロック）専用 ----------
def _parse_bs_page(pg):
    """3-4: 1ページ=1拠点。左(資産)・右(負債純資産)を中央xで分割し各々パース。
       列アンカーは各ブロックの数値右端クラスタから取る。"""
    chars = list(pg.chars)
    nums_all = build_numbers(chars, 120)
    if not nums_all:
        return [], ["当年度末", "前年度末", "増減"]
    words = pg.extract_words(use_text_flow=False, keep_blank_chars=False)
    band = _first_data_top(pg) - 1.5   # ヘッダ/見出し帯の下端（動的）
    # 中央分割x: 左ブロック(資産)のラベル列群と右ブロック(負債純資産)のラベル列群の
    # 間に引く。ラベル(非数値)語の x0 を集めると、資産ラベル(x0~34-90)と
    # 負債ラベル(x0~296-)の間に大きな空白帯がある。最大ギャップの中点を境界とする。
    lab_x0 = sorted(w["x0"] for w in words
                    if not any(ch.isdigit() for ch in w["text"])
                    and w["top"] >= band and w["x0"] < 540
                    and w["text"] not in ("資産の部", "負債の部", "純資産の部"))
    split_x = 300.0
    if len(lab_x0) >= 2:
        best = (0, split_x)
        for i in range(len(lab_x0) - 1):
            g = lab_x0[i + 1] - lab_x0[i]
            if g > best[0]:
                best = (g, (lab_x0[i] + lab_x0[i + 1]) / 2)
        split_x = best[1]

    out_cols = ["当年度末", "前年度末", "増減"]
    # 値の左右分割は「値列x1の最大ギャップ」(資産値≤~307 と 負債値≥~447 の間)。
    vxs = sorted(n["x1"] for n in nums_all)
    vsplit = 380.0
    if len(vxs) >= 2:
        bg = (0, vsplit)
        for i in range(len(vxs) - 1):
            g = vxs[i + 1] - vxs[i]
            if g > bg[0]:
                bg = (g, (vxs[i] + vxs[i + 1]) / 2)
        vsplit = bg[1]
    blocks = []
    for side, llo, lhi, vlo, vhi in (
            ("L", 0, split_x, 0, vsplit),
            ("R", split_x, 10 ** 6, vsplit, 10 ** 6)):
        side_nums = [n for n in nums_all if vlo <= n["x1"] < vhi]
        anchors = detect_anchors(side_nums, min_members=4, tol=5)
        if len(anchors) < 1:
            blocks.append([])
            continue
        anchors = sorted(anchors)[:3]
        vfe = min(anchors)
        # 科目名文字: ラベル領域[llo,lhi) かつ値列より左。
        side_chars = [c for c in chars if llo <= (c["x0"] + c["x1"]) / 2 < lhi]
        body_edge = _body_edge_from_chars(side_chars, vfe, band)
        # 右ブロックは、左ブロックの長い折返しラベルの末尾が同一topに侵入することが
        # ある。負債側ラベル列の左端(最頻x0)を name_xmin にして混入を断つ。
        nm_xmin = llo
        if side == "R":
            from collections import Counter as _C
            cand = [round(w["x0"]) for w in words
                    if split_x <= w["x0"] < vfe - 10 and w["top"] >= band
                    and not any(ch.isdigit() for ch in w["text"])
                    and len(w["text"]) >= 3]
            if cand:
                # レベル0(最左)ラベルの最頻x0 − 余白
                nm_xmin = min(_C(cand).most_common(3), key=lambda kv: kv[0])[0] - 4
        rows = _parse_label_rows(chars, anchors, vfe, body_edge,
                                 name_xmax=vfe - 18, name_xmin=nm_xmin,
                                 clean_margin=False)
        blocks.append(rows)
    return blocks[0] + blocks[1], out_cols


# ---------- メイン ----------
def _colnames(statement):
    return {
        "CF": ["予算", "決算", "差異"],
        "PL": ["当年度決算", "前年度決算", "増減"],
        "BS": ["当年度末", "前年度末", "増減"],
    }[statement]


def extract_kyoten4(pdf, statement):
    """タイプ4 抽出のエントリポイント。拠点ごとのブロックのリストを返す。"""
    pages = list(pdf.pages)
    # 拠点境界 = 見出し（拠点名+計算書名）を持つページ。
    # 見出しのみで判定（列ヘッダの有無は CF/PL 続きページ判定にも使う）。
    starts = []
    for pi, pg in enumerate(pages):
        ws = pg.extract_words(use_text_flow=False, keep_blank_chars=False)
        t = page_title(ws, statement, band_bottom=_first_data_top(pg) - 1.5)
        if t is not None:
            starts.append((pi, t))
    if not starts:
        raise ValueError(f"タイプ4({statement}): 拠点見出しが見つかりません")

    results = []
    for si, (pi, title) in enumerate(starts):
        pj = starts[si + 1][0] if si + 1 < len(starts) else len(pages)
        block_pages = pages[pi:pj]

        if statement == "BS":
            # BSは1拠点=1ページ前提。複数ページでも各ページ左右ブロックを連結。
            all_rows = []
            cols = _colnames("BS")
            for bp in block_pages:
                rows, _ = _parse_bs_page(bp)
                all_rows.extend(rows)
            results.append({"kyoten": title, "colnames": cols, "rows": all_rows})
            continue

        # CF/PL: 縦連続。全ページ chars を連結し、列アンカーは先頭ページの数値から確定。
        first_chars = list(block_pages[0].chars)
        first_nums = build_numbers(first_chars, 250)
        anchors = sorted(detect_anchors(first_nums, min_members=4, tol=5))
        if len(anchors) < 1:
            # 全ゼロ拠点（数値が極端に少ない）→ ページ内の数値右端から緩めに取る
            anchors = sorted(detect_anchors(first_nums, min_members=2, tol=5))
        # CF/PL は3値列。検出が4以上(備考に数字が出た等)なら左3つを採用。
        if len(anchors) >= 3:
            anchors = anchors[:3]
        all_chars = _concat_chars(block_pages)
        vfe = min(anchors) if anchors else 300
        name_top_min = 0
        body_edge = _body_edge_from_chars(all_chars, vfe, name_top_min)
        rows = _parse_label_rows(all_chars, anchors, vfe, body_edge,
                                 name_xmax=vfe - 18)
        results.append({"kyoten": title, "colnames": _colnames(statement), "rows": rows})

    return results


def _skip(name):
    n = name.strip()
    if n == "":
        return True
    # 期間ヘッダ「（自）令和…/（至）令和…」は '令和' で確実に捕捉される。
    #   かつて '（自'/'（至' を部分一致で除外していたが、これは科目名
    #   '訓練等給付費収益（自立）' を誤って落とす（'（自'立 が '（自' に一致）。
    #   ヘッダ特有の「（自）/（至）」（括弧が直後に閉じる形）だけを除外対象とする。
    bad = ("勘定科目", "様式", "令和", "単位：円", "1/", "社会福祉法人",
           "拠点区分", "資金収支計算書", "事業活動計算書", "貸借対照表", "現在", "備考")
    if any(b in n for b in bad):
        return True
    if re.search(r"[（(]\s*自\s*[）)]", n) or re.search(r"[（(]\s*至\s*[）)]", n):
        return True
    if n in ("資産の部", "負債の部", "純資産の部"):
        return True
    return False
