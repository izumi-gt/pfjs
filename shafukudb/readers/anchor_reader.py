"""(C)突合用・アンカー行(CF末尾の収支差額/支払資金残高)の罫線グリッド由来リーダー。
reader_1_4本体は改変しない（案A・非侵襲）。縦罫線でx境界と縦スパンを導出し、
表本体グリッド内の横書きトークンのみ採用することで、タイトル化け等の汚染を排除する。
"""
import pdfplumber
import reader_1_4_cf as r4

# アンカー科目名(式番号を除いた本体名) → master code
ANCHOR_MAP = {
    '事業活動資金収支差額':  'CF-02-00-000-000-000',
    '施設整備等資金収支差額': 'CF-04-00-000-000-000',
    'その他の活動資金収支差額':'CF-06-00-000-000-000',
    '当期資金収支差額合計':   'CF-08-00-000-000-000',
    '前期末支払資金残高':     'CF-09-00-000-000-000',
    '当期末支払資金残高':     'CF-10-00-000-000-000',
}

def _grid(p):
    """科目名帯の縦罫線 x境界リストと、表本体の縦スパン(vtop,vbottom)を返す。"""
    vlines = [(round(r['x0'],1), r['top'], r['top']+r['height'])
              for r in p.rects if r['width']<3 and r['height']>50 and r['x0']<300]
    if not vlines: return None
    xs = sorted(set(x for x,_,_ in vlines))
    vtop = min(t for _,t,_ in vlines); vbot = max(b for _,_,b in vlines)
    return xs, vtop, vbot

def read_anchor_rows(pdf_path):
    """アンカー行を [{拠点seq, code, name, 決算B}] で返す(グリッド内・横書きのみ)。
    拠点境界はreader_1_4のbuild_facility_rangesを流用して拠点別に集計可能にする。"""
    pdf = pdfplumber.open(pdf_path)
    ranges = r4.build_facility_ranges(pdf)  # [(拠点名, page_range), ...]
    page2fac = {}
    for fi,(fname, prng) in enumerate(ranges):
        for pi in prng: page2fac[pi] = fi
    out = []
    for pi, p in enumerate(pdf.pages):
        g = _grid(p)
        if not g: continue
        xs, vtop, vbot = g
        # 金額帯の左端(=最初の金額列罫線)。科目名は그 左側。
        amt_left = min(x for x in xs if x >= 250) if any(x>=250 for x in xs) else 254.9
        subj_left = xs[0]
        words = p.extract_words(x_tolerance=1.5)
        # 決算B列 [331,407) 近傍
        amtsB = [w for w in words if 331 <= w['x0'] < 407]
        for w in words:
            if not (subj_left-0.5 <= w['x0'] < amt_left): continue
            if len(w['text']) < 2: continue
            if not (vtop <= w['top'] <= vbot): continue   # ★グリッド内スパン=汚染排除
            base = w['text']
            code = None
            for key, c in ANCHOR_MAP.items():
                if base.startswith(key): code = c; break
            if not code: continue
            # 同一topの決算B
            b = None; bd = 2.1
            for a in amtsB:
                d = abs(a['top']-w['top'])
                if d <= 2.0 and d < bd: b, bd = a['text'], d
            out.append({'page': pi, '拠点seq': page2fac.get(pi),
                        'code': code, 'name': base,
                        '決算B': r4.parse_amount(b)})
    return out

if __name__ == '__main__':
    from collections import defaultdict
    rows = read_anchor_rows('hanano_1-4.pdf')
    print('華野 アンカー行抽出:', len(rows), '行')
    bycode = defaultdict(int)
    for r in rows:
        print(f'  拠点{r["拠点seq"]} {r["code"]} {r["name"][:22]:24} 決算B={r["決算B"]}')
        if r['決算B'] is not None: bycode[r['code']] += r['決算B']
    print('--- code別 拠点合算 ---')
    for c,v in sorted(bycode.items()):
        print(f'  {c}: {v:,}')
