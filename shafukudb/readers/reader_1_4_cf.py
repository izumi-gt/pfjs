"""様式1-4(拠点区分 資金収支計算書・CF)専用の読み取り機。

■ 照合アーキテクチャ(フェーズ2-4で確定)
1-4は最も複雑な様式のため、1-1/1-2/1-3が共有する run_match(位置照合)では
「事業区分をまたぐ同名科目の帰属」「(何)プレースホルダ帰属」を正しく解けない。
そこで 1-4 は run_match を使わず、本ファイル内に自己完結した「階層照合方式」を持つ。
これにより run_match には一切手を加えず、1-1/1-2/1-3 の既存動作は無傷のまま保たれる。
(照合層の一本化は見送り。1-4のみ独自方式という結論。)

■ 階層照合方式の骨子
縦罫線で決まるインデント段(ゾーンC内のx0段)を、マスタ階層に対応づけて照合する。
- インデント0: まず L2集計科目(収入計/支出計/差額 等) → L3実名 → L3(何)接尾辞帰属 → 法人特有
- インデント1: 直前にヒットしたL3の「子(L4)」の範囲で 実名 → (何)子 → 法人特有
- インデント2: 直前にヒットしたL4の「子(L5)」の範囲で 実名 → (何)子 → 法人特有
親は必ず1周先に確定してから子・孫を照合する(親の子に限定)。

■ L3(何)接尾辞帰属(インデント0でL3実名に当たらないとき)
科目名の末尾で(何)L3に帰属先を決める(収入/支出の軸を縦書きから読まずに判定できる):
  末尾4文字が「事業収入」→ (何)事業収入 / 「事業収益」→ (何)事業収益
  末尾2文字が「収入」→ (何)収入 / 「支出」→ (何)支出 / 「収益」→ (何)収益
  いずれにも該当しなければ法人特有。
例: 県立施設運営事業収入→(何)事業収入(017) / 県納付金支出→(何)支出(006)

■ 見出し＋明細の同額2段組(1-4特有)
内訳を1つしか持たない集計科目は「見出し(x0浅)＋明細(x0深)」が同額で2段表示される。
階層照合では、明細側(次インデント)がマスタに子を持たない科目(繰入金等)のとき
「子の範囲に実名なし・(何)なし」で自然に法人特有へ落ち、二重計上が構造的に防がれる。
マスタに子がある科目(居宅介護支援介護料収入等)は明細が正しく子codeへHITする。

■ 変わらない1-4固有の抽出方針(フェーズ2-3)
- 縦書き軸帯(L1=活動・部/L2=収入・支出)は読まない。ゾーンC(x0>=60)の横書き科目のみ。
- ヘッダー除外・拠点境界検出はページ構造(罫線・見出し語)で判定。
- 半角括弧はマスタ表記(全角)へ正規化。

■ フェーズ2-6: 集計NG原因特定で見つかった2件の改修(2025年度3社データで確認)
(1) 継続ページの先頭行が見出しと誤認されて抽出漏れになる問題(page_table_top)
    L3集計科目の子リストがページをまたぐとき、継続ページには表ヘッダーが
    再描画されず水平線が検出できない。従来はその場合でも一律110ptの下駄を
    履かせて「top<=110は見出し」として除外していたが、継続ページでは
    データ行がページ最上部(縦罫線がtop<60から開始)に来るため、正当な
    科目行(例: 車輌費支出)が丸ごと抽出から漏れていた。縦罫線の開始位置で
    継続ページを判定し、その場合は下駄を外すことで解消。
(2) 同一(何)placeholderコードに複数の実額が乗るとき、検算が最初の1件しか
    合算しない問題(_verify_one_facility)。マスタの粒度を超える法人特有的な
    科目(例: 固定資産取得支出の子である「構築物取得支出」、（何）事業収入配下の
    複数の独立した事業収入ブロック)は同じコードに複数帰属するのが正常であり、
    合算が正しい。従来の「最初の非null値のみ採用」を単純合算に変更して解消。
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import pdfplumber

MASTER_PATH = Path(__file__).resolve().parent / 'seiten_master_v3.csv'
if not MASTER_PATH.exists():
    MASTER_PATH = Path(__file__).resolve().parent.parent / 'seiten_master_v3.csv'

AMOUNT_COLUMNS = [('予算A', 255, 331), ('決算B', 331, 407), ('差異AB', 407, 483), ('備考', 483, 560)]


# ---------------- マスタ ----------------
def load_master_cf(master_path=None):
    with open(master_path or MASTER_PATH, encoding='utf-8-sig', newline='') as f:
        return [r for r in csv.DictReader(f) if r['L0コード'] == 'CF']


CF = load_master_cf()


def leaf_name(r):
    for i in range(5, 0, -1):
        if r[f'L{i}科目']:
            return r[f'L{i}科目']
    return ''


def _seg(code):
    return code.split('-')[1:]


def code_depth(code):
    d = 0
    for i, s in enumerate(_seg(code)):
        if s not in ('000', '00'):
            d = i + 1
    return d


def is_nanika(name):
    return '（何）' in name


def parse_amount(s):
    if s is None or s == '':
        return None
    return int(s.replace(',', '').replace('△', '-').replace('▲', '-').replace(' ', ''))


def normalize_name(t):
    return t.replace('(', '（').replace(')', '）')


# 深さ別index・子探索
BY_DEPTH = defaultdict(list)
for _j, _r in enumerate(CF):
    BY_DEPTH[code_depth(_r['code'])].append(_j)


def children_of(parent_idx):
    """CF[parent_idx]の直接の子(深さ+1・上位セグメント一致)のindexリスト。"""
    pc = CF[parent_idx]['code']
    ps = _seg(pc)
    pl = code_depth(pc)
    return [j for j in BY_DEPTH[pl + 1] if _seg(CF[j]['code'])[:pl] == ps[:pl]]


# インデント0で使う: L2集計名・L3実名の索引
L2_NAME = defaultdict(list)
L3_NAME = defaultdict(list)
for _j in BY_DEPTH[2]:
    L2_NAME[leaf_name(CF[_j])].append(_j)
for _j in BY_DEPTH[3]:
    L3_NAME[leaf_name(CF[_j])].append(_j)

# L3(何)の接尾辞→index
NANIKA_L3 = {}
for _j in BY_DEPTH[3]:
    _n = leaf_name(CF[_j])
    if _n == '（何）事業収入':
        NANIKA_L3['事業収入'] = _j
    elif _n == '（何）事業収益':
        NANIKA_L3['事業収益'] = _j
    elif _n == '（何）収入':
        NANIKA_L3['収入'] = _j
    elif _n == '（何）支出':
        NANIKA_L3['支出'] = _j
    elif _n == '（何）収益':
        NANIKA_L3['収益'] = _j


def nanika_l3_for(name):
    """L3実名に当たらない科目を、接尾辞で(何)L3へ帰属。該当なしはNone(法人特有)。"""
    if name[-4:] in ('事業収入', '事業収益'):
        return NANIKA_L3.get(name[-4:])
    if name[-2:] in ('収入', '支出', '収益'):
        return NANIKA_L3.get(name[-2:])
    return None


# ---------------- 抽出(ゾーンC) ----------------
def page_table_top(p):
    """そのページで科目抽出を開始すべきtop位置を返す。
    - 通常ページ: 表ヘッダー帯の下端(x0≈39.7の水平線ペア)の下側。
    - 継続ページ(フェーズ2-6で追加): 表ヘッダーが再描画されず水平線が検出できないが、
      縦罫線がページ最上部近く(top<60)から始まっている場合、それは見出し無しで
      前ページの子リストがそのまま続いていることを示す。この場合は110ptの
      固定下駄を履かせず、そのままt0=0(実質フィルタ無し)を返す。
      これにより、事業費支出や固定資産取得支出等の子科目リストがページ境界を
      またいだ際、continuation側先頭の行(top<110)が見出しと誤認されて
      抽出漏れになる問題を防ぐ。
    - どちらの判定もできない場合は、安全側としてフォールバックの110を返す。
    """
    h_outer = [r['top'] for r in p.rects if r['height'] < 1.0 and abs(r['x0'] - 39.7) < 0.5]
    h_outer = sorted(set(round(t, 1) for t in h_outer))
    if len(h_outer) >= 2 and h_outer[1] - h_outer[0] < 20:
        return h_outer[1]
    v_rules = [r['top'] for r in p.rects if r['width'] < 3 and r['height'] > 50]
    if v_rules and min(v_rules) < 60:
        return 0
    return 110


def extract_zoneC(pdf, page_range):
    """ゾーンC(x0>=60、横書き科目名)を上から順に、金額4列付きで抽出。"""
    rows = []
    for pi in page_range:
        p = pdf.pages[pi]
        words = p.extract_words(x_tolerance=1.5)
        t0 = page_table_top(p)
        amt = [w for w in words if w['x0'] >= 255]
        subj = [w for w in words if 60 <= w['x0'] < 255 and len(w['text']) >= 2 and w['top'] > t0]
        subj.sort(key=lambda w: w['top'])
        for w in subj:
            cols = {}
            for lb, lo, hi in AMOUNT_COLUMNS:
                best, bd = None, 2.1
                for a in amt:
                    if lo <= a['x0'] < hi:
                        d = abs(a['top'] - w['top'])
                        if d <= 2.0 and d < bd:
                            best, bd = a['text'], d
                cols[lb] = best
            rows.append({'page': pi, 'top': round(w['top'], 1), 'x0': round(w['x0'], 1),
                         'name': normalize_name(w['text']), 'amounts': cols})
    return rows


def assign_indent_stage(rows):
    """ゾーンC内のx0を昇順の段(0,1,2..)へ量子化(近接<=2ptは同段)。"""
    xs = sorted(set(r['x0'] for r in rows))
    stages = []
    for x in xs:
        if stages and x - stages[-1][-1] <= 2:
            stages[-1].append(x)
        else:
            stages.append([x])
    x2s = {x: si for si, grp in enumerate(stages) for x in grp}
    for r in rows:
        r['stage'] = x2s[r['x0']]


# ---------------- 階層照合 ----------------
def match_in_children(name, parent_idx):
    """親の子範囲で 実名一致 → (何)子 → (None,None)。"""
    kids = children_of(parent_idx)
    for j in kids:
        if leaf_name(CF[j]) == name:
            return j, '実名'
    for j in kids:
        if is_nanika(leaf_name(CF[j])):
            return j, '（何）'
    return None, None


def match_facility(rows):
    """1拠点分のゾーンC科目を階層照合。各rowに status/code/master_name/kind を付与。"""
    assign_indent_stage(rows)
    res = []
    cur = {0: None, 1: None, 2: None, 3: None}  # stage -> 直近HITのCF index
    for r in rows:
        name, st = r['name'], r['stage']
        found, kind = None, None

        if st == 0:
            if name in L2_NAME:
                found, kind = L2_NAME[name][0], 'L2'
            elif name in L3_NAME:
                found, kind = L3_NAME[name][0], 'L3'
            else:
                nj = nanika_l3_for(name)
                if nj is not None:
                    found, kind = nj, 'L3（何）'
            cur[0], cur[1], cur[2] = found, None, None
        elif st >= 1 and cur[st - 1] is not None:
            found, kind = match_in_children(name, cur[st - 1])
            cur[st] = found

        if found is not None:
            res.append({**r, 'status': 'HIT', 'code': CF[found]['code'],
                        'kind': kind, 'master_name': leaf_name(CF[found])})
        else:
            res.append({**r, 'status': '法人特有', 'code': None, 'kind': None, 'master_name': None})
    return res


# ---------------- 拠点分割・法人名 ----------------
def detect_facility_boundaries(pdf):
    bounds = []
    for pi, p in enumerate(pdf.pages):
        for w in p.extract_words(x_tolerance=1.5):
            if '拠点区分' in w['text'] and w['top'] < 90:
                bounds.append((pi, w['text']))
                break
    return bounds


def build_facility_ranges(pdf):
    b = detect_facility_boundaries(pdf)
    n = len(pdf.pages)
    ranges = []
    for i, (pi, name) in enumerate(b):
        end = b[i + 1][0] if i + 1 < len(b) else n
        ranges.append((name, range(pi, end)))
    if not ranges:
        ranges.append(('(単一拠点)', range(0, n)))
    return ranges


def get_corp_name(pdf):
    words = pdf.pages[0].extract_words(x_tolerance=1.5)
    top = sorted([w for w in words if w['top'] < 30], key=lambda w: (w['top'], w['x0']))
    return top[0]['text'] if top else ''


# ---------------- メイン ----------------
def process_pdf(pdf_path, statement='CF'):
    """PDF全体を拠点ごとに階層照合し、金額付き行リストを返す。"""
    pdf = pdfplumber.open(pdf_path)
    corp = get_corp_name(pdf)
    all_rows = []
    for fac_name, page_range in build_facility_ranges(pdf):
        rows = extract_zoneC(pdf, page_range)
        res = match_facility(rows)
        for r in res:
            amt = r.get('amounts', {})
            r['法人名'] = corp
            r['拠点区分'] = fac_name
            r['計算書'] = statement
            r['予算A'] = amt.get('予算A')
            r['決算B'] = amt.get('決算B')
            r['差異AB'] = amt.get('差異AB')
            r['備考'] = amt.get('備考')
            all_rows.append(r)
    return all_rows


def _verify_one_facility(fac_rows, master):
    """1拠点分の行に対する集計検算。空欄子は0。全子未出現の集計行はSKIP。

    同一codeが複数行に出現する場合は合算する(フェーズ2-6で変更)。
    マスタに個別コードを持たない法人特有的な科目が、複数とも同じ(何)placeholder
    (例: CF-03-03-002-005-000（何）取得支出、CF-01-01-017-000-000（何）事業収入)
    に帰属するケースがあり、以前は「最初に見つかった非null値のみ採用・以降は無視」
    としていたため、2件目以降の金額が集計から漏れて検算NGになっていた。
    同一placeholderに複数の実額が乗るのは構造的に正常(マスタの粒度がそこまで
    細かくないだけ)なので、単純合算が正しい。
    戻り値: (ok, ng, skip, ng_list)。ng_listは (code, 期待値, 計算値)。"""
    amt = {}
    for r in fac_rows:
        if r['status'] == 'HIT' and r['code'] and r['決算B'] is not None:
            amt[r['code']] = amt.get(r['code'], 0) + parse_amount(r['決算B'])
    ok = ng = skip = 0
    ng_list = []
    for code, mr in master.items():
        if mr['is_total'] != '1' or not mr['合算定義']:
            continue
        if code not in amt:
            skip += 1
            continue
        ch = json.loads(mr['合算定義'])
        if all(c['code'] not in amt for c in ch):
            skip += 1
            continue
        tot = sum((amt.get(c['code']) or 0) * (1 if c['sign'] == '+' else -1) for c in ch)
        if tot == amt[code]:
            ok += 1
        else:
            ng += 1
            ng_list.append((code, amt[code], tot))
    return ok, ng, skip, ng_list


def verify_totals(rows, statement='CF'):
    """合算定義による集計検算。拠点ごとに検算してOK/NG/SKIPを積算する。

    同一codeが複数拠点に出現するため、拠点をまたいで一括検算すると2拠点目以降の値が
    握り潰されて検算が壊れる。必ず拠点(拠点区分)ごとに区切って検算すること。
    戻り値: (ok, ng, skip, ng_list)。ng_listは (拠点区分, code, 期待値, 計算値)。
    """
    master = {r['code']: r for r in CF}
    by_fac = defaultdict(list)
    for r in rows:
        by_fac[r.get('拠点区分', '(単一拠点)')].append(r)
    ok = ng = skip = 0
    ng_list = []
    for fac_name, fac_rows in by_fac.items():
        o, n, s, nl = _verify_one_facility(fac_rows, master)
        ok += o
        ng += n
        skip += s
        for code, exp, calc in nl:
            ng_list.append((fac_name, code, exp, calc))
    return ok, ng, skip, ng_list


def write_csv(rows, out_path):
    cols = ['法人名', '拠点区分', '計算書', 'page', 'top', 'status', 'kind', 'code', 'name',
            'master_name', '予算A', '決算B', '差異AB', '備考']
    with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({**r, 'pdf': r.get('name')})


if __name__ == '__main__':
    import glob
    from collections import Counter
    for path in sorted(glob.glob('*_1-4.pdf')):
        rows = process_pdf(path)
        corp = rows[0]['法人名'] if rows else path
        c = Counter(r['status'] for r in rows)
        ok, ng, skip, _ = verify_totals(rows)
        print(f"{corp} ({path}): {dict(c)} 計{len(rows)} / 集計検算 OK={ok} NG={ng} SKIP={skip}")
