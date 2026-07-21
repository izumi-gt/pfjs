"""様式1-1（財務諸表 法人単位資金収支計算書＝法人全体・CF）専用読み取り機。

■ 様式1-1の構造（華野・広島・三篠会・エフアイジイの実測で確定）
  - 事業区分・拠点区分の分解は無く、法人1本の単一表。
  - 金額列は4列固定: 予算(A) / 決算(B) / 差異(A)-(B) / 備考。
      * 差異(A)-(B) ＝ 予算 − 決算（符号確定・code非依存の列恒等式）。
      * 備考はテキスト列（サンプル4社では全て空）。金額パースせず、ロング出力からは除外。
  - 予備費支出（１０）行が実在。よって末尾集計行の（数式番号）がマスタと完全一致し、
    表示レベル2正規化は不要（照合層を無改変流用しても strip_formula で両者素名に落ちるため無害）。
  - 縦分割あり（行が複数頁に跨る）。継続頁はタイトル・ヘッダー無し、負topの痕あり。

■ 座標導出（最重要・1-3の rects 思想を踏襲しつつ 1-1固有に頑健化）
  WAM は改ページ位置により継続頁の縦罫線本数が変わる（実測）:
      8本（通常）  : [外枠左, 軸帯内仕切, 科目列左, 科目列右, 予算右, 決算右, 差異右, 備考右]
      7本（三篠会）: その他活動ブロック内で改ページ → 科目列左(=61.3付近) が欠落
      6本（エフアイジイ）: 末尾集計行だけで改ページ → 軸帯仕切と科目列左が両方欠落
  → 位置依存（rules[2]/rules[3]）は破綻する。以下で頑健化:
    - 科目列 ＝ 罫線間ギャップが最大の列（金額列は等幅で狭いため、科目列が常に最大幅）。
    - 金額列 ＝ 科目列右端(subj_hi)以降の罫線群を左→右に。先頭3列を予算/決算/差異に対応。
    - 科目・集計ラベルの拾い上げ ＝ ラベル領域 [外枠左, subj_hi) の語のうち len>=2。
      len==1 は縦書き軸帯（収入/支出/事業活動による収支…）の残骸なので一律除外。
      6本頁では軸帯が科目列域へ潰れるが、この len>=2 規則で残骸混入を防ぐ。
    - top の絶対閾値は使わない（頁で行高・データ域が異なり、継続頁は負topの痕あり）。

■ 照合方針（1-2の照合層をそのまま流用）
  - HIT / 法人固有 の2分類。法人固有は元名保持＋連番。
  - 2段階照合（run_match）: Stage1=深度<=3を順不同・一意名、
    Stage2=Stage1未解決分を末尾接尾辞で(何)L3へ帰属（nanika_l3_for）。
    旧Stage2（深度>=4を位置照合）は4法人×3様式の実測で貢献が広島の1件のみ
    （かつ誤ったブランチ選択）と判明し削除済み（reader_1_2_cf.py参照）。
  - 予備費支出があるため末尾集計行はマスタと逐語一致（正規化に頼らずHIT）。

■ 出力: ロング（tidy）形式。1セル=1行。
  法人名, 様式(1-1), 計算書(CF), 列名(予算/決算/差異), status, seq, code, name, master_name, 金額
  備考列（テキスト）は捕捉のみで出力から除外（非空なら警告）。

■ 検算（三本柱・全て code非依存 / 4社で全NG=0を確認）
  1. 列恒等式（全行）: 差異 ＝ 予算 − 決算（予算のある行のみ検査可能）。
  2. 数式連鎖（決算列・予備費込みフル）: (3)=(1)-(2), (6)=(4)-(5), (9)=(7)-(8),
     当期資金収支差額合計=(3)+(6)+(9)-(10), 当期末=合計+前期末。
  3. ブロック合算（位置ベース）: 各「計」行 ＝ 直前ブロック明細行の和（決算列）。
"""
import re
import csv
import pdfplumber
from reader_1_2_cf import load_master_cf, run_match, leaf_name  # 照合層を無改変流用
from reader_1_2_cf import stitch, is_axis_residue  # 縦書き軸帯混入バグ対策(2026-07実測で確定)

# 金額列のラベル（左→右）。第4列=備考はテキスト列で出力対象外。
AMOUNT_LABELS = ['予算', '決算', '差異', '備考']
OUTPUT_COLS = ['予算', '決算', '差異']     # ロング出力に載せる列
VERIFY_COL = '決算'                        # 縦検算・ブロック合算に使う列
TOP_TOL = 3.0                              # 金額を科目行に紐付ける top 許容差(pt)
MIN_LABEL_LEN = 2                          # ラベル領域で拾う最小文字数（len==1=縦書き残骸を除外）

BLOCK_MARKERS = ['事業活動収入計', '事業活動支出計',
                 '施設整備等収入計', '施設整備等支出計',
                 'その他の活動収入計', 'その他の活動支出計']


def parse_amount(s):
    if s is None or s == '':
        return None
    if not re.match(r'^[0-9,\.\u25b2\u25b3\-]+$', s):
        return None
    return int(s.replace(',', '').replace('△', '-').replace('▲', '-').replace(' ', ''))


# ---------------- 抽出層（1-1固有・座標は rects から頑健に導出）----------------
def vertical_rules(page):
    """縦罫線のx中心（細い縦長矩形）をユニーク・昇順で返す。"""
    xs = set()
    for r in page.rects:
        if (r['x1'] - r['x0']) < 3 and (r['bottom'] - r['top']) > 10:
            xs.add(round((r['x0'] + r['x1']) / 2, 1))
    return sorted(xs)


def page_coords(rules):
    """rules -> (label_lo, subj_hi, amount_columns[(lo,hi),...])。
    科目列=最大幅の列。金額列=科目列右端(subj_hi)以降の罫線群。
    ラベル領域左端=外枠左(rules[0])。改ページによる罫線欠落(8/7/6本)に頑健。"""
    gaps = [(rules[i + 1] - rules[i], rules[i], rules[i + 1]) for i in range(len(rules) - 1)]
    _, _subj_lo, subj_hi = max(gaps, key=lambda g: g[0])
    amt_rules = [x for x in rules if x >= subj_hi - 0.1]
    amt = [(amt_rules[i], amt_rules[i + 1]) for i in range(len(amt_rules) - 1)]
    return rules[0], subj_hi, amt


def is_excl_subject(t):
    """科目として拾ってはいけない語（見出し・日付・単位・様式・計算書名・ページ番号）。
    注: 「事業区分間…」等の正規科目は部分一致で消さない。"""
    if t == '勘定科目':
        return True
    if t.startswith('（自）') or t.startswith('（至）'):
        return True
    if re.match(r'^\d+\s*/\s*\d+$', t):        # ページ番号 1/2
        return True
    for k in ('令和', '単位', '様式', '第一号', '資金収支計算書'):
        if k in t:
            return True
    return False


def extract_rows(pdf):
    """全頁を上から順に走査し、(name, {列:金額文字列}) を読み取り順で返す。
    1-1は単一表なので、頁をまたいで行を素直に連結する（縦分割）。"""
    rows = []
    for p in pdf.pages:
        rules = vertical_rules(p)
        if len(rules) < 5:                     # 表のない頁はスキップ
            continue
        label_lo, subj_hi, amt = page_coords(rules)

        # 科目・集計ラベル: ラベル領域[label_lo, subj_hi) の len>=2 語（縦書き残骸=len1を除外）
        subjects = []
        for w in stitch(p.extract_words(x_tolerance=1.5)):
            t = w['text']
            if is_excl_subject(t):
                continue
            if is_axis_residue(t):
                continue
            if label_lo <= w['x0'] < subj_hi and len(t) >= MIN_LABEL_LEN:
                subjects.append((round(w['top'], 1), t))
        subjects.sort(key=lambda x: x[0])

        # 金額紐付け: 列中心x membership + |top差|<=TOP_TOL（右寄せ数値を最近傍で）
        nums = stitch(p.extract_words(x_tolerance=3))
        for top, name in subjects:
            cells = {}
            for ci, (lo, hi) in enumerate(amt):
                if ci >= len(AMOUNT_LABELS):
                    break
                label = AMOUNT_LABELS[ci]
                best, bd = None, TOP_TOL + 0.01
                for w in nums:
                    xc = (w['x0'] + w['x1']) / 2
                    d = abs(w['top'] - top)
                    if lo <= xc < hi and d <= TOP_TOL and d < bd:
                        best, bd = w['text'], d
                cells[label] = best
            rows.append({'name': name, 'cells': cells})
    return rows


# ---------------- 統合処理 ----------------
def get_corp_name(pdf):
    words = pdf.pages[0].extract_words()
    top = [w for w in words if w['top'] < 30 and w['x0'] < 120]
    top.sort(key=lambda w: (w['top'], w['x0']))
    return top[0]['text'] if top else ''


def process_pdf(pdf_path):
    """1-1 PDF -> ロング形式の結果行リスト。"""
    pdf = pdfplumber.open(pdf_path)
    corp = get_corp_name(pdf)
    cf = load_master_cf()
    rows = extract_rows(pdf)
    matched = run_match([{'name': r['name']} for r in rows], cf)

    long_rows = []
    for r, m in zip(rows, matched):
        for label in OUTPUT_COLS:
            long_rows.append({
                '法人名': corp, '様式': '1-1', '計算書': 'CF',
                '列名': label,
                'status': m['status'], 'seq': m['seq'],
                'code': m['code'], 'name': m['name'],
                'master_name': m['master_name'],
                '金額': parse_amount(r['cells'].get(label)),
            })
    return long_rows, rows, matched


def write_csv(long_rows, out_path):
    cols = ['法人名', '様式', '計算書', '列名',
            'status', 'seq', 'code', 'name', 'master_name', '金額']
    with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in long_rows:
            w.writerow(r)


# ---------------- 検算（三本柱・code非依存）----------------
def verify_column_identity(rows):
    """差異 ＝ 予算 − 決算（予算のある行のみ）。"""
    checked = ng = 0
    details = []
    for r in rows:
        y = parse_amount(r['cells'].get('予算'))
        k = parse_amount(r['cells'].get('決算'))
        d = parse_amount(r['cells'].get('差異'))
        if None in (y, k, d):
            continue
        checked += 1
        if y - k != d:
            ng += 1
            details.append((r['name'], f'{y}-{k}≠{d}'))
    return checked, ng, details


def _g(rows, key, col=VERIFY_COL):
    for r in rows:
        if key in r['name']:
            return parse_amount(r['cells'].get(col))
    return None


def verify_formula_chain(rows, col=VERIFY_COL):
    """集計行の縦の数式連鎖（予備費込みフル）。"""
    g = lambda k: _g(rows, k, col)
    v3, v6, v9 = g('事業活動資金収支差額'), g('施設整備等資金収支差額'), g('その他の活動資金収支差額')
    v = {
        '(3)=(1)-(2)': (g('事業活動収入計'), g('事業活動支出計'), v3),
        '(6)=(4)-(5)': (g('施設整備等収入計'), g('施設整備等支出計'), v6),
        '(9)=(7)-(8)': (g('その他の活動収入計'), g('その他の活動支出計'), v9),
    }
    checks = []
    for label, (a, b, c) in v.items():
        if None not in (a, b, c):
            checks.append((label, a - b == c, a - b, c))
    v10 = g('予備費支出') or 0
    v11 = g('当期資金収支差額合計')
    v12 = g('前期末支払資金残高')
    v13 = g('当期末支払資金残高')
    if None not in (v3, v6, v9, v11):
        checks.append(('(11)=(3)+(6)+(9)-(10)', v3 + v6 + v9 - v10 == v11, v3 + v6 + v9 - v10, v11))
    if None not in (v11, v12, v13):
        checks.append(('当期末=(11)+(12)', v11 + v12 == v13, v11 + v12, v13))
    return checks


def verify_block_sums(rows, col=VERIFY_COL):
    """各「計」行 ＝ 直前ブロック明細行の和（位置ベース）。差額/合計/残高は明細に含めない。"""
    def is_marker(n):
        return any(m in n for m in BLOCK_MARKERS)

    def is_summary(n):
        return is_marker(n) or '差額' in n or '当期資金収支差額合計' in n or '支払資金残高' in n

    ok = ng = acc = 0
    details = []
    for r in rows:
        name = r['name']
        v = parse_amount(r['cells'].get(col))
        if is_marker(name):
            if v is not None:
                if v == acc:
                    ok += 1
                else:
                    ng += 1
                    details.append((name, v, acc))
            acc = 0
        elif is_summary(name):
            continue
        else:
            if v is not None:
                acc += v
    return ok, ng, details


def check_biko_empty(rows):
    """備考列が非空の行を返す（設計上は空想定。非空なら要確認）。"""
    return [(r['name'], r['cells'].get('備考')) for r in rows
            if r['cells'].get('備考') not in (None, '')]


if __name__ == '__main__':
    from collections import Counter
    targets = ['hanano_1-1.pdf', 'hiroshima_1-1.pdf', 'misasa_1-1.pdf', 'fig_1-1.pdf']
    all_long = []
    for path in targets:
        long_rows, rows, matched = process_pdf(path)
        all_long.extend(long_rows)
        corp = long_rows[0]['法人名'] if long_rows else path
        c = Counter(m['status'] for m in matched)
        ci_chk, ci_ng, ci_d = verify_column_identity(rows)
        fchain = verify_formula_chain(rows)
        fng = sum(1 for _, ok, _, _ in fchain if not ok)
        bok, bng, bd = verify_block_sums(rows)
        biko = check_biko_empty(rows)
        print(f'===== {corp} ({path}) =====')
        print(f'  照合: {dict(c)} (科目{len(rows)})')
        print(f'  列恒等式(差異=予算-決算): {ci_chk}行 NG={ci_ng}')
        for n, m in ci_d:
            print(f'    NG {n}: {m}')
        print(f'  数式連鎖(決算,予備費込): {len(fchain)}式 NG={fng}')
        for label, ok, exp, got in fchain:
            if not ok:
                print(f'    NG {label} 期待={exp:,} 実={got:,}')
        print(f'  ブロック合算(決算): OK={bok} NG={bng}')
        for n, tot, acc in bd:
            print(f'    NG {n} 表記={tot:,} 明細和={acc:,}')
        print(f'  備考非空: {len(biko)}件')
        kokoyu = [m['name'] for m in matched if m['status'] != 'HIT']
        if kokoyu:
            print(f'  法人固有({len(kokoyu)}): {kokoyu}')
        print()
    write_csv(all_long, '/mnt/user-data/outputs/照合結果_1-1_CF.csv')
    print(f'総行数 {len(all_long)} を 照合結果_1-1_CF.csv に出力')
