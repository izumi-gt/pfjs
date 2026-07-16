"""様式1-2（資金収支内訳表・CF）専用読み取り機。

1-4との違い:
  - 科目1列 × 事業区分6列（社福/公益/収益/合計/内部消去/法人合計）。
  - 拠点境界なし。CF全体を1周する。改ページは考慮。
  - 縦書き軸帯（L1/L2）は読まない（1-4と同思想）。横書き科目名だけを上から拾う。

照合方針（確定事項）:
  - HIT / 法人固有 の2分類のみ。NANIKA・UNRESOLVED・親一致は使わない
    （1-2は集計様式でL4が並ぶだけ、親子概念が薄いため一律「法人固有」に寄せる）。
  - 非HITは code=None・元科目名保持・連番付与。マスタポインタは前進しない。
  - codeは遡らない（HIT時のみmp=found+1で単調前進、先読みは範囲無制限）。
  - 集計行ラベルの（数式番号）ずれ対策: マスタの表示レベル=2 の行に限り
    括弧・演算子を除去して照合（照合時正規化。マスタ本体は無改変）。

検算:
  - 列恒等式（全行）: 合計=社福+公益+収益 / 法人合計=合計−内部消去。code非依存。
  - 縦の数式連鎖（集計行）: (3)=(1)-(2), (6)=(4)-(5), (9)=(7)-(8),
    当期資金収支差額合計=(3)+(6)+(9), 当期末=合計+前期末。
  - ブロック合算（位置ベース）: 各「計」行 = 直前ブロック行の法人合計和（HIT+法人固有）。
"""
import pdfplumber
import csv
import re

# ---- 1-2 座標定数（華野・広島で罫線一致を確認済み）----
SUBJECT_X = (54.5, 185.7)        # 勘定科目列
AXIS_X = (37.0, 54.5)            # 縦書き軸帯（横書き集計ラベルの割込のみ拾う）
AMOUNT_COLUMNS = [               # 事業区分6列（左→右, 順序固定）
    ('社福',     185.7, 247.8),
    ('公益',     247.8, 310.0),
    ('収益',     310.0, 372.2),
    ('合計',     372.2, 434.4),
    ('内部消去', 434.4, 496.5),
    ('法人合計', 496.5, 558.7),
]
AMOUNT_X_MIN = 185.7
COL_HEADER = '勘定科目'
AXIS_LABEL_MINLEN = 5            # 軸帯に割込む集計ラベルの最小長（縦書き残骸=1文字を除外）


# ---------------- マスタ（照合層は流用可能な共通ヘルパ）----------------
def load_master_cf(path='seiten_master_v4.csv'):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r['L0コード'] == 'CF']


def leaf_name(r):
    for i in range(5, 0, -1):
        if r[f'L{i}科目']:
            return r[f'L{i}科目']
    return ''


def parse_amount(s):
    if s is None or s == '':
        return None
    return int(s.replace(',', '').replace('△', '-').replace('▲', '-').replace(' ', ''))


def strip_formula(s):
    """（数式番号）と演算子（＝＋－=）を除去。表示レベル2行の照合キー生成に使う。"""
    s = re.sub(r'（[^）]*）', '', s)
    return s.replace('＝', '').replace('＋', '').replace('－', '').replace('=', '').strip()


def match_key(r):
    """マスタ行の照合キー。表示レベル2のみ正規化、他は原文leaf。"""
    if r['表示レベル'] == '2':
        return strip_formula(leaf_name(r))
    return leaf_name(r)


# ---------------- 抽出層（1-2固有）----------------
def extract_rows(pdf):
    """全ページから (page, top, 科目名, {列:金額文字列}) を上から順に抽出。
    科目名: 科目列(x0∈SUBJECT_X, ≠'勘定科目') と 軸帯割込の集計ラベル(len≥5)。
    絶対top閾値は使わず、x0とヘッダ語除外だけで判定（継続ページの独自topに非依存）。"""
    rows = []
    for pi, p in enumerate(pdf.pages):
        words = p.extract_words()
        subjects = []
        for w in words:
            t = w['text']
            x0 = w['x0']
            if SUBJECT_X[0] <= x0 < SUBJECT_X[1]:
                if t == COL_HEADER:
                    continue
                subjects.append((round(w['top'], 1), t))
            elif AXIS_X[0] <= x0 < AXIS_X[1] and len(t) >= AXIS_LABEL_MINLEN:
                subjects.append((round(w['top'], 1), t))
        subjects.sort(key=lambda x: x[0])
        # 各科目行に6列金額を紐付け
        amt_words = [w for w in words if w['x0'] >= AMOUNT_X_MIN]
        for top, name in subjects:
            cols = {}
            for label, lo, hi in AMOUNT_COLUMNS:
                best = None
                bestd = 3.0 + 1
                for w in amt_words:
                    if lo <= w['x0'] < hi:
                        d = abs(w['top'] - top)
                        if d <= 3.0 and d < bestd:
                            best = w['text']
                            bestd = d
                cols[label] = best
            rows.append({'page': pi, 'top': top, 'name': name, 'amounts': cols})
    return rows


# ---------------- 照合層（1-2: HIT / 法人固有 の2分類）----------------
def code_depth(code):
    """codeの最深の非000セグメント位置(1..5)。例 CF-01-01-004-000-000 → 3。"""
    d = 0
    for i, s in enumerate(code.split('-')[1:]):
        if s not in ('000', '00'):
            d = i + 1
    return d


def _build_nanika_l3(cf):
    """cf から (何) L3 catch-all の {接尾辞: index} を作る。
    対象は depth3(=L3)の記号的 catch-all 科目名のみ。CFには（何）事業収入/
    （何）収入/（何）支出 が存在する（（何）事業収益/（何）収益 はCFには無い）。"""
    m = {}
    for j, r in enumerate(cf):
        if code_depth(r['code']) != 3:
            continue
        n = leaf_name(r)
        if n == '（何）事業収入':
            m['事業収入'] = j
        elif n == '（何）事業収益':
            m['事業収益'] = j
        elif n == '（何）収入':
            m['収入'] = j
        elif n == '（何）支出':
            m['支出'] = j
        elif n == '（何）収益':
            m['収益'] = j
    return m


def nanika_l3_for(name, nmap):
    """L3実名に当たらない科目を、接尾辞で(何)L3へ帰属。
    まず4文字接尾辞（事業収入/事業収益）→（何）事業収入 系を優先し、
    次に2文字接尾辞（収入/支出/収益）→（何）収入/（何）支出 系で判定する。
    （何）事業収入→（何）収入/（何）支出 の順に評価する（izumi氏指摘の照合順序）。
    該当なしはNone（真の法人固有）。"""
    if name[-4:] in ('事業収入', '事業収益'):
        return nmap.get(name[-4:])
    if name[-2:] in ('収入', '支出', '収益'):
        return nmap.get(name[-2:])
    return None


def run_match(rows, cf):
    """2段階照合。
      Stage1: 深度≤3（カテゴリ科目）を順不同・一意名で照合。
              深度≤3の照合キーはマスタ全体で一意（'収入'/'支出'の軸帯見出しは
              科目として現れないため除外）。よって順序に依存せず安全で、
              WAM出現順の揺れ（例: 経常経費補助金収入が事業収入の後に出る）を吸収する。
      Stage2: Stage1でも未解決の科目を、末尾接尾辞で(何)L3へ帰属（nanika_l3_for）。
              様式1-1/1-2/1-3はL2/L3集計値のみを掲載しdepth4以上が出現しないため
              （華野・広島・あと会・エフアイジイの4法人×3様式で実測確認。医療系・
              保育系を含む法人でも同様）、未解決の残りは「よそのカテゴリの深い子科目」
              ではなく法人独自の事業名であり、接尾辞での(何)帰属が安全に機能する。
              末尾が収入/支出で終わらないもの（例:「〜事業」で止まる）のみ真の
              法人固有として残す。
      それ以外は法人固有（code=None・元名保持・連番）。

      [削除履歴] 旧Stage2（深度≥4の科目を位置で照合。直前一致より後方の最小候補を
      採用）は、上記4法人×3様式(12ケース)の実測で貢献が広島の1件（「その他の
      事業収入」がmaster内17ヶ所の同名候補のうち深度5の1枝へ機械的に位置一致）
      のみと判明。かつその1件は1-4の階層照合が導く枝と異なる誤ったブランチ選択
      であったため削除。この行は削除後、本Stage2(nanika)が深度3の「（何）事業収入」
      に正しく帰属させる（1-4のstage0解決と一致）。全4法人×3様式で削除前後の
      HIT/法人固有件数・列恒等式/数式連鎖/ブロック合算のNG件数に変化なしを確認済み。
    """
    depth = [code_depth(r['code']) for r in cf]
    # 深度≤3の照合キー -> index群（順不同・一意判定用）
    key3 = {}
    for j, r in enumerate(cf):
        if depth[j] <= 3:
            key3.setdefault(match_key(r), []).append(j)

    resolved = [None] * len(rows)  # 各行の確定index（HIT時）

    # --- Stage1: 深度≤3 順不同・一意名 ---
    for i, row in enumerate(rows):
        text = row['name']
        cand = key3.get(text) or key3.get(strip_formula(text)) or []
        if len(cand) == 1:
            resolved[i] = cand[0]

    # --- Stage2: 未解決を末尾接尾辞で(何)L3へ帰属 ---
    nmap = _build_nanika_l3(cf)
    for i, row in enumerate(rows):
        if resolved[i] is not None:
            continue
        j = nanika_l3_for(row['name'], nmap)
        if j is not None:
            resolved[i] = j

    # --- 整形 ---
    results = []
    kokoyu_seq = 0
    for i, row in enumerate(rows):
        j = resolved[i]
        if j is not None:
            results.append({**row, 'status': 'HIT',
                            'code': cf[j]['code'],
                            'master_name': leaf_name(cf[j]),
                            'seq': None})
        else:
            kokoyu_seq += 1
            results.append({**row, 'status': '法人固有',
                            'code': None, 'master_name': None,
                            'seq': kokoyu_seq})
    return results


# ---------------- 検算 ----------------
def verify_column_identity(results):
    """全行: 合計=社福+公益+収益 / 法人合計=合計−内部消去。空欄は0とみなす。"""
    ng = []
    checked = 0
    for r in results:
        a = {k: parse_amount(v) for k, v in r['amounts'].items()}
        sf, ko, sy = a['社福'], a['公益'], a['収益']
        go, nb, ho = a['合計'], a['内部消去'], a['法人合計']
        if None in (sf, ko, sy, go):
            continue
        checked += 1
        if sf + ko + sy != go:
            ng.append(('合計', r['name'], f'{sf}+{ko}+{sy}≠{go}'))
        if ho is not None and go - (nb or 0) != ho:
            ng.append(('法人合計', r['name'], f'{go}-{nb or 0}≠{ho}'))
    return checked, ng


def _amt(results, key_contains, col='法人合計'):
    for r in results:
        if key_contains in r['name']:
            return parse_amount(r['amounts'][col])
    return None


def verify_formula_chain(results, col='法人合計'):
    """集計行の縦の数式連鎖を検算（col列）。"""
    g = lambda k: _amt(results, k, col)
    checks = []
    v1, v2, v3 = g('事業活動収入計'), g('事業活動支出計'), g('事業活動資金収支差額')
    v4, v5, v6 = g('施設整備等収入計'), g('施設整備等支出計'), g('施設整備等資金収支差額')
    v7, v8, v9 = g('その他の活動収入計'), g('その他の活動支出計'), g('その他の活動資金収支差額')
    v10 = g('当期資金収支差額合計')
    v11 = g('前期末支払資金残高')
    v12 = g('当期末支払資金残高')

    def chk(label, expr, got):
        if expr is None or got is None:
            return
        checks.append((label, expr == got, expr, got))
    chk('(3)=(1)-(2)', (v1 - v2) if None not in (v1, v2) else None, v3)
    chk('(6)=(4)-(5)', (v4 - v5) if None not in (v4, v5) else None, v6)
    chk('(9)=(7)-(8)', (v7 - v8) if None not in (v7, v8) else None, v9)
    chk('合計=(3)+(6)+(9)', (v3 + v6 + v9) if None not in (v3, v6, v9) else None, v10)
    chk('当期末=合計+前期末', (v10 + v11) if None not in (v10, v11) else None, v12)
    return checks


BLOCK_MARKERS = ['事業活動収入計', '事業活動支出計',
                 '施設整備等収入計', '施設整備等支出計',
                 'その他の活動収入計', 'その他の活動支出計']


def verify_block_sums(results, col='法人合計'):
    """各「計」行 = 直前ブロックの明細行の和（法人合計）。位置ベース・code非依存。
    差額/合計/残高行および計行自身は明細に含めない。"""
    ng = []
    ok = 0
    acc = 0
    have = False
    def is_marker(name):
        return any(m in name for m in BLOCK_MARKERS)
    def is_summary(name):
        return is_marker(name) or ('差額' in name) or ('当期資金収支差額合計' in name) \
            or ('支払資金残高' in name)
    for r in results:
        name = r['name']
        v = parse_amount(r['amounts'].get(col))
        if is_marker(name):
            total = parse_amount(r['amounts'].get(col))
            if total is not None:
                if total == acc:
                    ok += 1
                else:
                    ng.append((name, total, acc))
            acc = 0
            have = False
        elif is_summary(name):
            # 差額・合計・残高はブロック明細ではないので加算しない。累積もリセットしない
            # （収入計→支出計の間に差額は無いのでこの分岐は主に末尾用）
            continue
        else:
            if v is not None:
                acc += v
                have = True
    return ok, ng


# ---------------- 統合処理 ----------------
def get_corp_name(pdf):
    words = pdf.pages[0].extract_words()
    top = [w for w in words if w['top'] < 30 and w['x0'] < 120]
    top.sort(key=lambda w: (w['top'], w['x0']))
    return top[0]['text'] if top else ''


def process_pdf(pdf_path):
    pdf = pdfplumber.open(pdf_path)
    cf = load_master_cf()
    corp = get_corp_name(pdf)
    rows = extract_rows(pdf)
    results = run_match(rows, cf)
    for r in results:
        r['法人名'] = corp
        r['計算書'] = 'CF'
        r['様式'] = '1-2'
        # 6列を平坦化
        for label, _, _ in AMOUNT_COLUMNS:
            r[label] = parse_amount(r['amounts'][label])
    return results


def write_csv(results, out_path):
    cols = ['法人名', '様式', '計算書', 'page', 'top', 'status', 'seq',
            'code', 'name', 'master_name',
            '社福', '公益', '収益', '合計', '内部消去', '法人合計']
    with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in results:
            w.writerow(r)


if __name__ == '__main__':
    from collections import Counter
    targets = ['shafuku_1-2.pdf', 'hiroshima_1-2.pdf', 'ashitaka_1-2.pdf']
    all_rows = []
    for path in targets:
        res = process_pdf(path)
        all_rows.extend(res)
        corp = res[0]['法人名']
        c = Counter(r['status'] for r in res)
        ci_checked, ci_ng = verify_column_identity(res)
        fchain = verify_formula_chain(res)
        bok, bng = verify_block_sums(res)
        print(f'===== {corp} =====')
        print(f'  抽出/照合: {dict(c)} (計{len(res)})')
        print(f'  列恒等式: {ci_checked}行検査 NG={len(ci_ng)}')
        for k in ci_ng:
            print(f'    NG {k}')
        print(f'  数式連鎖:')
        for label, ok, exp, got in fchain:
            print(f'    [{"OK" if ok else "NG"}] {label}  期待={exp:,} 実={got:,}')
        print(f'  ブロック合算: OK={bok} NG={len(bng)}')
        for name, tot, acc in bng:
            print(f'    NG {name} 表記={tot:,} 明細和={acc:,} 差={tot-acc:,}')
        print()
    write_csv(all_rows, '/mnt/user-data/outputs/照合結果_1-2_CF.csv')
    print(f'総行数 {len(all_rows)} を 照合結果_1-2_CF.csv に出力')
