"""様式1-4(拠点区分 資金収支計算書)専用の読み取り機。

抽出層のみを担当する。code照合・NANIKA/UNRESOLVED判定・集計検算は
matching.matcher に委譲する(様式非依存のため)。

確立した方針(フェーズ2-3):
- 縦書き軸帯(L1/L2)は読まない。改ページでのセル分断・見えない重複文字により、
  座標ベースでは原理的に読めないため。横書き科目名だけを上から順に読む。
- 左端2列(x0=40-50)に混入する縦書き残骸(17文字セット)は、先頭1-3文字目に限り除去する。
- ヘッダー除外・拠点境界検出はページ構造(罫線・見出し語)で判定し、座標固定値には依存しない。

本reader固有の座標定数(x0境界・金額列位置・改ページ挙動)は様式1-4専用であり、
他様式(1-2, 2-4等)では別途実測してreaderを新規実装すること。
"""
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from matching.matcher import run_match_subjects, verify_totals, write_csv  # noqa: E402

AXIS_CHARS = set("事業活動施設整等そのに他よる収支")

AMOUNT_COLUMNS = [
    ('予算A', 255.0, 331.0),
    ('決算B', 331.0, 407.0),
    ('差異AB', 407.0, 483.0),
    ('備考', 483.0, 560.0),
]


def clean_axis_contamination(text):
    """先頭1〜3文字目の中にある17文字セット文字だけを除去する。
    正規の科目名本体(4文字目以降)には手を出さない。混入は必ず先頭1-3文字目に限定される。"""
    head = text[:3]
    rest = text[3:]
    cleaned_head = ''.join(ch for ch in head if ch not in AXIS_CHARS)
    return cleaned_head + rest


def is_axis_residue(text):
    """17文字セット以外の文字を一切含まない=縦書き軸帯の残骸そのもの。"""
    return all(ch in AXIS_CHARS for ch in text)


def detect_facility_boundaries(pdf):
    """拠点境界(様式先頭ページ)を検出。top<80に'拠点区分'を含む語があるページ。"""
    boundaries = []
    for pi, p in enumerate(pdf.pages):
        words = p.extract_words()
        hdr = [w for w in words if w['top'] < 80 and '拠点区分' in w['text']]
        if hdr:
            boundaries.append((pi, hdr[0]['text']))
    return boundaries


def page_table_top(p):
    """このページの表開始top。外枠由来の横線が2本(105付近, 116付近)あれば
    ヘッダー付きページなので2本目(116.1相当)を返す。1本以下なら継続ページとして0。"""
    h_outer = [r['top'] for r in p.rects if r['height'] < 1.0 and abs(r['x0'] - 39.7) < 0.5]
    h_outer = sorted(set(round(t, 1) for t in h_outer))
    if len(h_outer) >= 2 and h_outer[1] - h_outer[0] < 20:
        return h_outer[1]
    return 0


def extract_pdf_subjects_multi(pdf_path, page_range):
    """複数ページにわたる横書き科目名抽出。page_range: 対象ページindexのリスト。"""
    pdf = pdfplumber.open(pdf_path)
    subjects = []
    for pi in page_range:
        p = pdf.pages[pi]
        words = p.extract_words()
        t0 = page_table_top(p)
        normal = [w for w in words if 50 <= w['x0'] < 255 and w['top'] >= t0 and len(w['text']) >= 2]
        axis_zone = [w for w in words if 40 <= w['x0'] < 50 and w['top'] >= t0]
        items = []
        for w in normal:
            items.append({'top': w['top'], 'text': w['text']})
        for w in axis_zone:
            t = w['text']
            if len(t) <= 2:
                continue
            if is_axis_residue(t):
                continue
            items.append({'top': w['top'], 'text': clean_axis_contamination(t)})
        items.sort(key=lambda x: x['top'])
        for it in items:
            subjects.append({'page': pi, 'top': round(it['top'], 1), 'text': it['text']})
    return subjects


def extract_amounts_for_page(p):
    """ページ内の各top行について、4列の金額(備考含む)を集める。
    戻り値: {round(top,1): {'予算A':str, '決算B':str, '差異AB':str, '備考':str}}"""
    words = p.extract_words()
    amount_words = [w for w in words if w['x0'] >= 255]
    by_top = {}
    for w in amount_words:
        t = round(w['top'], 1)
        by_top.setdefault(t, []).append(w)
    result = {}
    for t, ws in by_top.items():
        row = {}
        for name, xlo, xhi in AMOUNT_COLUMNS:
            for w in ws:
                if xlo <= w['x0'] < xhi:
                    row[name] = w['text']
                    break
        result[t] = row
    return result


def find_amount_row(amounts_by_top, target_top, tol=2.0):
    """target_topに最も近いtopの金額行を返す(誤差tol以内)。"""
    best = None
    best_diff = tol + 1
    for t, row in amounts_by_top.items():
        diff = abs(t - target_top)
        if diff <= tol and diff < best_diff:
            best = row
            best_diff = diff
    return best or {}


def run_match_with_amounts(pdf_path, page_range, statement='CF'):
    """指定ページ範囲で照合し、各行に金額4列を紐付ける。"""
    pdf = pdfplumber.open(pdf_path)
    subjects = extract_pdf_subjects_multi(pdf_path, page_range)
    results = run_match_subjects(subjects, statement=statement)

    amounts_cache = {}
    for r in results:
        pi = r['page']
        if pi not in amounts_cache:
            amounts_cache[pi] = extract_amounts_for_page(pdf.pages[pi])
        row = find_amount_row(amounts_cache[pi], r['top'])
        r['予算A'] = row.get('予算A')
        r['決算B'] = row.get('決算B')
        r['差異AB'] = row.get('差異AB')
        r['備考'] = row.get('備考')
    return results


def get_corp_name(pdf):
    """法人名(1ページ目 top<30 の最初の語)を取得。"""
    words = pdf.pages[0].extract_words()
    top = [w for w in words if w['top'] < 30]
    top.sort(key=lambda w: (w['top'], w['x0']))
    return top[0]['text'] if top else ''


def build_facility_ranges(pdf):
    """拠点境界から (拠点名, page_range) のリストを作る。"""
    boundaries = detect_facility_boundaries(pdf)
    n = len(pdf.pages)
    ranges = []
    for i, (pi, name) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else n
        ranges.append((name, range(pi, end)))
    if not ranges:  # 拠点境界が検出できない場合は全体を1拠点扱い
        ranges.append(('(単一拠点)', range(0, n)))
    return ranges


def process_pdf(pdf_path, statement='CF'):
    """PDF全体を拠点ごとに照合し、金額付きの行リストを返す。"""
    pdf = pdfplumber.open(pdf_path)
    corp = get_corp_name(pdf)
    fac_ranges = build_facility_ranges(pdf)
    all_rows = []
    for fac_name, page_range in fac_ranges:
        res = run_match_with_amounts(pdf_path, page_range, statement=statement)
        for r in res:
            r['法人名'] = corp
            r['拠点区分'] = fac_name
            r['計算書'] = statement
            all_rows.append(r)
    return all_rows


if __name__ == '__main__':
    from collections import Counter

    targets = [('tsuneishi_1-4.pdf', 'CF'), ('youshiki_1-4.pdf', 'CF')]
    all_rows = []
    for path, stmt in targets:
        rows = process_pdf(path, statement=stmt)
        all_rows.extend(rows)
        for fac in sorted(set(r['拠点区分'] for r in rows)):
            sub = [r for r in rows if r['拠点区分'] == fac]
            c = Counter(r['status'] for r in sub)
            ok, ng, skip, ng_list = verify_totals(sub, statement=stmt)
            print(f"{sub[0]['法人名']} / {fac}")
            print(f"  照合: {dict(c)} (計{len(sub)})")
            print(f"  集計検算: OK={ok} NG={ng} SKIP={skip}")
            for code, exp, calc in ng_list:
                print(f"    NG {code} 期待={exp:,} 計算={calc:,} 差={exp - calc:,}")
    write_csv(all_rows, '照合結果_1-4_CF.csv')
    print(f"\n総行数 {len(all_rows)} を 照合結果_1-4_CF.csv に出力")
