"""様式1-3（事業区分 資金収支内訳表・CF）専用読み取り機。

■ 様式1-3の構造（広島県福祉事業団・華野福祉会の実測で確定）
  - 事業区分（社福/公益/収益）ごとに1つの内訳表。各表は「拠点区分の列 + 合計 +
    内部取引消去 + 事業区分合計」の展開。拠点数は法人ごとに可変。
  - WAMはページ幅を列数で等分するため、列幅・行高は法人ごとに変わる。
    → 座標は固定定数を使わず、page.rects の縦罫線から頁ごとに導出する。
  - 大きな表は2通りに分割される（両対応が必須）:
      横分割: 列が多い法人。各頁が「全科目行 × 一部の列」を持ち、頁ごとにタイトルと
              ヘッダー行がある。列を横に連結する（例:広島 社福=9列頁+5列頁）。
      縦分割: 行が多い法人。先頭頁が「上半分の行 × 全列」、継続頁がタイトル/ヘッダー
              無しで残りの行を続ける。行を縦に連結する（例:華野 社福=65行頁+12行頁）。
  - 判別: タイトル「○○事業区分」の有無。タイトル有=新カラムセクション開始、
          無=直前セクションの縦継続。

■ 抽出方針（1-4/1-2と同思想。座標は流用せず1-3固有に導出）
  - 縦書き軸帯は読まない。横書き科目名だけを拾う。
  - extract_words(x_tolerance=1.5): 軸帯の縦書きL1/L2文字（施/入/収/出/支）が
    科目に前置結合する混入を、低toleranceで単独1文字語に分離できる（実科目は
    科目列x0に綺麗に残る）。集計行ラベルは分断されず健全。1-4の「先頭数文字剥ぎ」
    より安全（集計行を壊さない）。
  - 座標: 縦罫線 rules=sorted。rules[0]=外枠左, rules[2]=科目列左, rules[3]=科目列右
    (=第1金額列左)。金額列境界=rules[3:]。軸帯=[rules[0],rules[2])、科目=[rules[2],rules[3])。
  - 科目: 科目列x範囲の語（勘定科目/日付/単位/様式/ページ番号を除外）+ 軸帯x範囲の
    len>=5 の集計行ラベル。topは頁で異なるので絶対閾値を使わない。
  - ヘッダー(拠点名/合計/内部取引消去/事業区分合計): データ先頭行の直上帯
    (first_data-35, first_data-3) の語を列x範囲で (top,x0)順連結。折り返し・1文字分割も復元。

■ 照合方針（1-2の照合層をそのまま流用）
  - HIT / 法人固有 の2分類。法人固有は元名保持+連番。
  - 2段階照合（run_match）: Stage1=深度<=3を順不同・一意名、
    Stage2=Stage1未解決分を末尾接尾辞で(何)L3へ帰属（nanika_l3_for）。
    旧Stage2（深度>=4を位置照合）は4法人×3様式の実測で貢献が広島の1件のみ
    （かつ誤ったブランチ選択）と判明し削除済み（reader_1_2_cf.py参照）。
  - 表示レベル2の照合時正規化（数式番号ずれ対策）。予備費支出が無く末尾3行の番号が
    ずれるのは1-2と同型（(１０)↔(１１)等）→ 正規化HIT。

■ 出力: ロング（tidy）形式。1セル=1行。
  法人名,様式,計算書,事業区分,列名,列種別,status,seq,code,name,master_name,金額
  列種別 ∈ {拠点, 合計, 内部取引消去, 事業区分合計}

■ 検算（三本柱・広島/華野で全NG=0を確認済み）
  1. 列恒等式（全行・code非依存）: 合計=Σ拠点列 / |合計−事業区分合計|=|内部取引消去|
     （内部取引消去の符号は法人により正負まちまちのため絶対値で判定・2026-07実測）
  2. 数式連鎖（集計行・事業区分合計列）: (3)=(1)-(2)… 当期末=合計+前期末
  3. ブロック合算（位置ベース・code非依存）: 各「計」=直前ブロック明細行の和
"""
import re
import csv
import pdfplumber
from reader_1_2_cf import load_master_cf, run_match, leaf_name  # 照合層を流用
from reader_1_2_cf import stitch, is_axis_residue  # 縦書き軸帯混入バグ対策(2026-07実測で確定)

CAT = {'社会福祉事業区分': '社福', '公益事業区分': '公益', '収益事業区分': '収益'}
SPECIAL = ('合計', '内部取引消去', '事業区分合計')
AXIS_LABEL_MINLEN = 5


def parse_amount(s):
    if s is None or s == '':
        return None
    return int(s.replace(',', '').replace('△', '-').replace('▲', '-').replace(' ', ''))


# ---------------- 抽出層（1-3固有・座標は rects から導出）----------------
def vertical_rules(page):
    """縦罫線のx中心（細い縦長矩形）をユニーク・昇順で返す。"""
    xs = set()
    for r in page.rects:
        if (r['x1'] - r['x0']) < 3 and (r['bottom'] - r['top']) > 10:
            xs.add(round((r['x0'] + r['x1']) / 2, 1))
    return sorted(xs)


def page_coords(rules):
    """rules -> (axis_lo, subj_lo, subj_hi, amount_columns[(lo,hi),...])。
    科目列=最大幅の区間（金額列より明確に広い）。金額列=科目列右端以降の罫線群。
    軸帯左端=外枠左(rules[0])。
    2026-07実測: 改ページで軸帯内部の区切り罫線が欠落し縦罫線本数が変わる
    （みらい1-3: p0=8本→p1=7本）ため、位置依存(rules[2]/rules[3]決め打ち)だと
    2ページ目で科目列を1本ぶん取り違え、金額を科目名として誤抽出していた。
    最大幅区間で科目列を同定することで罫線本数の増減に頑健化（1-1と同方式）。"""
    gaps = [(rules[i + 1] - rules[i], i) for i in range(len(rules) - 1)]
    _, idx = max(gaps, key=lambda g: g[0])
    subj_lo, subj_hi = rules[idx], rules[idx + 1]
    amt = [(rules[i], rules[i + 1]) for i in range(idx + 1, len(rules) - 1)]
    return rules[0], subj_lo, subj_hi, amt


def title_of(page):
    """ページ上部の事業区分タイトル（社福/公益/収益）。無ければ None。"""
    for w in page.extract_words():
        if w['text'] in CAT and w['top'] < 95:
            return CAT[w['text']]
    return None


def _is_excl_subject(t):
    """科目として拾ってはいけない語（見出し・日付・単位・様式・ページ番号・事業区分見出し）。
    注: 「事業区分間…」等の正規科目は除外しない。"""
    if t == '勘定科目':
        return True
    if re.match(r'^\d+\s*/\s*\d+$', t):
        return True
    if t in CAT:
        return True
    if t.startswith('（自）') or t.startswith('（至）'):
        return True
    for k in ('令和', '単位', '様式', '第一号', '資金収支内訳表'):
        if k in t:
            return True
    return False


def table_top_of(page):
    """表本体の上端（最も上の罫線rectのtop）。表の外側=罫線より上の見出し等を
    データ行から除外するために使う。rectが無ければNone。"""
    tops = [r['top'] for r in page.rects]
    return min(tops) if tops else None


def extract_subjects(page, axis_lo, subj_lo, subj_hi):
    """(top, 科目名) を上から順に。科目列の語 + 軸帯の len>=5 集計ラベル。
    topの絶対閾値は使わない（頁ごとに行高・データ域が違うため）。
    ただし表本体の上端罫線より上の語（法人名見出し等）は除外する。
    2026-07実測: 桜虹会は法人名が「社会福祉法人」「桜虹会」の2語に分かれ、
    後者(x0≈60)が科目名列に入り込みデータ先頭行として誤検出されていた
    (他法人は「社会福祉法人○○」が1語でx0≈29のため軸帯より左=非該当)。"""
    tt = table_top_of(page)
    out = []
    for w in stitch(page.extract_words(x_tolerance=1.5)):
        t = w['text']
        if _is_excl_subject(t):
            continue
        if is_axis_residue(t):
            continue
        if tt is not None and w['top'] < tt - 1.0:  # 表上端罫線より上=見出し等
            continue
        if subj_lo <= w['x0'] < subj_hi:
            out.append((round(w['top'], 1), t))
        elif axis_lo <= w['x0'] < subj_lo and len(t) >= AXIS_LABEL_MINLEN:
            out.append((round(w['top'], 1), t))
    out.sort()
    return out


def _is_bad_header(t):
    if re.match(r'^[0-9,\.\u25b2\u25b3-]+$', t):   # 金額
        return True
    for k in ('令和', '単位', '様式', '第一号', '資金収支内訳表'):
        if k in t:
            return True
    return False


def extract_col_headers(page, amt_cols, first_data_top):
    """各金額列のヘッダー（拠点名 or 合計/内部取引消去/事業区分合計）を復元。
    データ先頭行の直上帯 (first_data-35, first_data-3) の語を列x範囲で (top,x0)順連結。"""
    ws = page.extract_words(x_tolerance=1.5)
    lo_b, hi_b = first_data_top - 35, first_data_top - 3
    out = []
    for lo, hi in amt_cols:
        h = [w for w in ws if lo_b < w['top'] < hi_b and lo <= w['x0'] < hi
             and not _is_bad_header(w['text'])]
        h.sort(key=lambda w: (round(w['top']), w['x0']))
        out.append(''.join(w['text'] for w in h))
    return out


def extract_amounts(page, tops, amt_cols):
    """(row_index, col_index) -> 金額文字列。列x範囲(中心)membership + top差<=3 で紐付け。"""
    ws = stitch(page.extract_words(x_tolerance=3))
    nums = [w for w in ws if any(c.isdigit() for c in w['text'])
            and re.match(r'^[0-9,\.\u25b2\u25b3-]+$', w['text'])]
    g = {}
    for ri, top in enumerate(tops):
        for ci, (lo, hi) in enumerate(amt_cols):
            best, bd = None, 3.01
            for w in nums:
                xc = (w['x0'] + w['x1']) / 2
                if lo <= xc < hi and abs(w['top'] - top) <= 3.0 and abs(w['top'] - top) < bd:
                    best, bd = w['text'], abs(w['top'] - top)
            g[(ri, ci)] = best
    return g


def build_sections(pdf):
    """ページ列を「カラムセクション」に束ねる。
    タイトル有=新セクション開始、無=直前セクションへ行を縦連結。
    返り値: [{'cat','headers','ncol','rows':[name...],'amt':{(ri,ci):str}}...]"""
    sections = []
    cur = None
    for page in pdf.pages:
        rules = vertical_rules(page)
        if len(rules) < 5:            # 表のない頁はスキップ
            continue
        axis_lo, subj_lo, subj_hi, amt_cols = page_coords(rules)
        subs = extract_subjects(page, axis_lo, subj_lo, subj_hi)
        if not subs:
            continue
        tops = [t for t, _ in subs]
        names = [n for _, n in subs]
        amt = extract_amounts(page, tops, amt_cols)
        title = title_of(page)
        if title is not None:         # 新カラムセクション
            headers = extract_col_headers(page, amt_cols, subs[0][0])
            cur = {'cat': title, 'headers': headers, 'ncol': len(amt_cols),
                   'rows': list(names),
                   'amt': {(ri, ci): amt.get((ri, ci))
                           for ri in range(len(names)) for ci in range(len(amt_cols))}}
            sections.append(cur)
        else:                          # 縦継続: 現セクションに行追加（列は同一x範囲）
            if cur is None:
                continue
            base = len(cur['rows'])
            cur['rows'].extend(names)
            for ri in range(len(names)):
                for ci in range(cur['ncol']):
                    cur['amt'][(base + ri, ci)] = amt.get((ri, ci))
    return sections


def assemble_categories(sections):
    """カラムセクションを事業区分ごとにマージ（横分割の列連結）。
    返り値: OrderedDict cat -> {'rows':[name...], 'headers':[h...], 'amt':{(ri,ci):str},
                               'rows_consistent':bool}"""
    from collections import OrderedDict
    cats = OrderedDict()
    for s in sections:
        cats.setdefault(s['cat'], []).append(s)
    result = OrderedDict()
    for cat, secs in cats.items():
        rows = secs[0]['rows']
        consistent = all(s['rows'] == rows for s in secs)
        headers, amt, coff = [], {}, 0
        for s in secs:
            for ci in range(s['ncol']):
                headers.append(s['headers'][ci])
                for ri in range(len(rows)):
                    amt[(ri, coff + ci)] = s['amt'].get((ri, ci))
            coff += s['ncol']
        result[cat] = {'rows': rows, 'headers': headers, 'amt': amt,
                       'rows_consistent': consistent}
    return result


def col_kind(header):
    """列種別を判定。"""
    if header in SPECIAL:
        return header
    return '拠点'


# ---------------- 統合処理 ----------------
def get_corp_name(pdf):
    words = pdf.pages[0].extract_words()
    top = [w for w in words if w['top'] < 30 and w['x0'] < 120]
    top.sort(key=lambda w: (w['top'], w['x0']))
    return top[0]['text'] if top else ''


def process_pdf(pdf_path):
    """1-3 PDF -> ロング形式の結果行リスト。"""
    pdf = pdfplumber.open(pdf_path)
    corp = get_corp_name(pdf)
    cf = load_master_cf()
    sections = build_sections(pdf)
    cats = assemble_categories(sections)

    long_rows = []
    for cat, d in cats.items():
        rows, headers, amt = d['rows'], d['headers'], d['amt']
        matched = run_match([{'name': n} for n in rows], cf)  # 事業区分ごとに照合
        for ri, m in enumerate(matched):
            for ci, h in enumerate(headers):
                long_rows.append({
                    '法人名': corp, '様式': '1-3', '計算書': 'CF',
                    '事業区分': cat, '列名': h, '列種別': col_kind(h),
                    'status': m['status'], 'seq': m['seq'],
                    'code': m['code'], 'name': m['name'],
                    'master_name': m['master_name'],
                    '金額': parse_amount(amt.get((ri, ci))),
                })
    return long_rows, cats


def write_csv(long_rows, out_path):
    cols = ['法人名', '様式', '計算書', '事業区分', '列名', '列種別',
            'status', 'seq', 'code', 'name', 'master_name', '金額']
    with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in long_rows:
            w.writerow(r)


# ---------------- 検算 ----------------
BLOCK_MARKERS = ['事業活動収入計', '事業活動支出計', '施設整備等収入計',
                 '施設整備等支出計', 'その他の活動収入計', 'その他の活動支出計']


def _is_summary(name):
    return any(m in name for m in BLOCK_MARKERS) or '差額' in name or '支払資金残高' in name


def verify_category(d):
    """1事業区分について三本柱を検算。返り値 dict。"""
    rows, headers, amt = d['rows'], d['headers'], d['amt']
    fac = [i for i, h in enumerate(headers) if h not in SPECIAL]
    igo = next((i for i, h in enumerate(headers) if h == '合計'), None)
    inb = next((i for i, h in enumerate(headers) if h == '内部取引消去'), None)
    ijg = next((i for i, h in enumerate(headers) if h == '事業区分合計'), None)

    # (1) 列恒等式
    ci_chk = ci_ng1 = ci_ng2 = 0
    for ri in range(len(rows)):
        go = parse_amount(amt.get((ri, igo))) if igo is not None else None
        nb = parse_amount(amt.get((ri, inb))) if inb is not None else None
        jg = parse_amount(amt.get((ri, ijg))) if ijg is not None else None
        fs = sum((parse_amount(amt.get((ri, i))) or 0) for i in fac)
        if go is not None:
            ci_chk += 1
            if fs != go:
                ci_ng1 += 1
        # 内部取引消去の符号は法人により正負まちまち(2026-07実測)。
        # 固定式(合計-内部取引消去 等)ではなく、符号非依存の
        # |合計 − 事業区分合計| == |内部取引消去| で判定する(§符号問題_決着報告)。
        if jg is not None and go is not None and abs(go - jg) != abs(nb or 0):
            ci_ng2 += 1

    # 縦検算に使う列（事業区分合計 > 合計 > 先頭拠点）
    vc = ijg if ijg is not None else (igo if igo is not None else (fac[0] if fac else 0))

    def cv(ri):
        return parse_amount(amt.get((ri, vc)))

    def g(k):
        for ri, n in enumerate(rows):
            if k in n:
                return cv(ri)
        return None

    # (2) 数式連鎖
    v = [None] + [g(k) for k in ['事業活動収入計', '事業活動支出計', '事業活動資金収支差額',
         '施設整備等収入計', '施設整備等支出計', '施設整備等資金収支差額',
         'その他の活動収入計', 'その他の活動支出計', 'その他の活動資金収支差額',
         '当期資金収支差額合計', '前期末支払資金残高', '当期末支払資金残高']]
    fng = 0
    for a, b, c in [(1, 2, 3), (4, 5, 6), (7, 8, 9)]:
        if None not in (v[a], v[b], v[c]) and v[a] - v[b] != v[c]:
            fng += 1
    if None not in (v[3], v[6], v[9], v[10]) and v[3] + v[6] + v[9] != v[10]:
        fng += 1
    if None not in (v[10], v[11], v[12]) and v[10] + v[11] != v[12]:
        fng += 1

    # (3) ブロック合算
    acc, bok, bng = 0, 0, 0
    for ri, n in enumerate(rows):
        val = cv(ri)
        if any(m in n for m in BLOCK_MARKERS):
            if val is not None:
                if val == acc:
                    bok += 1
                else:
                    bng += 1
            acc = 0
        elif _is_summary(n):
            continue
        else:
            if val is not None:
                acc += val
    return {'nrow': len(rows), 'nfac': len(fac), 'headers': headers,
            'rows_consistent': d['rows_consistent'],
            'ci_checked': ci_chk, 'ci_ng': ci_ng1 + ci_ng2,
            'formula_ng': fng, 'block_ok': bok, 'block_ng': bng,
            'vc_header': headers[vc] if headers else None}


if __name__ == '__main__':
    from collections import Counter
    targets = ['hiroshima_1-3.pdf', 'hanano_1-3.pdf']
    all_rows = []
    for path in targets:
        long_rows, cats = process_pdf(path)
        all_rows.extend(long_rows)
        corp = long_rows[0]['法人名'] if long_rows else path
        print(f'===== {corp} ({path}) =====')
        for cat, d in cats.items():
            matched = run_match([{'name': n} for n in d['rows']], load_master_cf())
            c = Counter(m['status'] for m in matched)
            r = verify_category(d)
            print(f'  [{cat}] 行{r["nrow"]} 拠点{r["nfac"]}列 行一致={r["rows_consistent"]} '
                  f'照合={dict(c)}')
            print(f'    列恒等式 {r["ci_checked"]}行 NG={r["ci_ng"]} / '
                  f'数式連鎖 NG={r["formula_ng"]}(列={r["vc_header"]}) / '
                  f'ブロック合算 OK={r["block_ok"]} NG={r["block_ng"]}')
        print()
    write_csv(all_rows, '/mnt/user-data/outputs/照合結果_1-3_CF.csv')
    print(f'総行数 {len(all_rows)} を 照合結果_1-3_CF.csv に出力')
