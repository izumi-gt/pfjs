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

■ フェーズ2-7: 法人特有金額のプールによる帳尻合わせ(2025年度3社データで確認)
   マスタに無い法人特有科目(実名にもnanika接尾辞にも当たらず親も特定できず法人特有へ
   落ちる科目。例: 「居宅介護料収入（利用者負担金収入）」のような連結見出し、
   「高次脳機能障害支援体制整備事業」のようなstage0の事業名)は金額が集計から行落ちして
   親の検算がNGになっていた。これを「法人特有のまま(=NG一覧に残したまま)」金額だけ
   親の集計にプールして帳尻を合わせる。
   - match_facility: 法人特有行に pseudo_code(監査用疑似コード)、pool_parent(金額を
     プールする先の実code。stage>=1は直近成功親、stage0は直近HITのL1/L2から収入計/
     支出計を継承)、pool_depth(二重計上防止用の相対深さ)を付与。
   - _verify_one_facility: pool_parent 配下の法人特有金額を、同一parent配下では最深段
     だけ採用(見出し=明細合計の2段組で見出しと明細を両方足す二重計上を防止)して
     親ノードの計算値に加算。浅い段と最深段の合計が食い違う場合は警告を出す
     (verify_totals_with_warnings で取得)。

■ フェーズ2-8: stage0見出しの子先読みによる(何)帰属(2025年度・広島で確認)
   「高次脳機能障害支援体制整備事業」のように、stage0の見出し自身の名前が「〜事業」で
   終わり収入/支出を判別できない科目は、従来 nanika_l3_for に当たらず法人特有へ落ちて
   親(事業活動収入計)の検算NGになっていた。直後に続く子(stage>=1)の名前末尾を先読みし、
   「〜収入/収益」なら（何）事業収入、「〜支出」なら（何）支出へ帰属する
   (nanika_l3_by_child_suffix)。見出し名だけで収入側へ倒すと同名パターンの支出系を
   誤帰属する危険があるため、必ず子の末尾で収入/支出を判定する。

■ フェーズ2-9: 縦罫線ベースの科目列切り出し + 深いstage対応(2025年度・誠和学園で確認)
   (1) 科目名の分割トークン結合(extract_zoneC + subject_right_edge)。
       extract_wordsが1科目名を字間で複数トークンに割り(例:「基本財産特定定期預金
       取崩収入」が3分割)、右側トークンのx0が大きいため幻のstage4+が生まれ、
       cur辞書KeyErrorやstage誤判定を起こしていた。金額列との境界は座標固定(255)では
       なく予算(A)列の左側縦罫線から動的に取得し(WAMは列幅を動的に割る)、その罫線より
       左の同一行(top近接)トークンを空白除去して1科目に連結する。1-4では同一行で親子を
       表現しないため、この連結は安全。
   (2) match_facilityのcur辞書を固定{0,1,2,3}からdefaultdictに変更し、任意深さの
       stage(保育事業収入配下のstage3等)に対応。stage0見出しで全段リセット、stage
       確定時に自分より深い段をクリアして、兄弟間の古い親の誤継承を防ぐ。
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import pdfplumber

MASTER_PATH = Path(__file__).resolve().parent / 'seiten_master_v5.csv'
if not MASTER_PATH.exists():
    MASTER_PATH = Path(__file__).resolve().parent.parent / 'seiten_master_v5.csv'

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
# 区画(ゾーン)対応の（何）解決（2026-07 実測で導入）。
# 旧実装は BY_DEPTH[3] のみ走査し「接尾辞→ノード1つ」を代入していたため、
# その他の活動/施設整備等の区画に出た法人固有科目まで CF-01側の（何）へ誤誘導していた
# （宗越福祉会で実証: 介護福祉士修学資金貸付金支出 680,000 が CF-01-03-006 に吸われ、
#  事業活動支出計が過大・その他の活動支出計が過小になっていた）。
# 全depthを走査して区画別に候補を持ち、各区画で最も浅いノードを代表とする。
_NANIKA_SUFFIXES = ('事業収入', '事業収益', '収入', '支出', '収益')
NANIKA_BY_ZONE = {}
NANIKA_L3 = {}
_cand = {}
for _j, _r in enumerate(CF):
    _code = _r['code']
    _n = leaf_name(_r)
    for _sfx in _NANIKA_SUFFIXES:
        if _n == f'（何）{_sfx}':
            _seg_ = _code.split('-')
            if len(_seg_) >= 3:
                _cand.setdefault(('-'.join(_seg_[:3]), _sfx), []).append(
                    (code_depth(_code), _code, _j))
            break
for _k, _lst in _cand.items():
    _lst.sort()
    NANIKA_BY_ZONE[_k] = _lst[0][2]
for (_z, _sfx), _j in NANIKA_BY_ZONE.items():
    _prev = NANIKA_L3.get(_sfx)
    if _prev is None or (code_depth(CF[_j]['code']), CF[_j]['code']) < \
            (code_depth(CF[_prev]['code']), CF[_prev]['code']):
        NANIKA_L3[_sfx] = _j

# CF様式の法令固定順。区画マーカー(横書き固定文字列)通過で現在区画を進める。
ZONE_FIRST = 'CF-01-01'
ZONE_AFTER_MARKER = [
    ('事業活動収入計', 'CF-01-03'),
    ('事業活動支出計', 'CF-03-01'),
    ('施設整備等収入計', 'CF-03-03'),
    ('施設整備等支出計', 'CF-05-01'),
    ('その他の活動収入計', 'CF-05-03'),
    ('その他の活動支出計', None),
]


def zone_after(name):
    """行名が区画マーカーなら通過後の区画を返す。マーカーでなければ False。"""
    for _base, _nxt in ZONE_AFTER_MARKER:
        if name.startswith(_base):
            return _nxt
    return False


def nanika_l3_for(name, zone=None):
    """L3実名に当たらない科目を、接尾辞で(何)へ帰属。該当なしはNone(法人特有)。
    zone を渡すとその区画の候補を優先し、無ければグローバルへフォールバックする。"""
    sfx = None
    if name[-4:] in ('事業収入', '事業収益'):
        sfx = name[-4:]
    elif name[-2:] in ('収入', '支出', '収益'):
        sfx = name[-2:]
    if sfx is None:
        return None
    if zone is not None:
        j = NANIKA_BY_ZONE.get((zone, sfx))
        if j is not None:
            return j
    return NANIKA_L3.get(sfx)


def _nanika_pick(sfx, zone=None, alt=None):
    """接尾辞→(何)ノード。現在区画を最優先し、区画内に無ければ代替接尾辞(alt)も
    同じ区画内で試す。それでも無ければグローバルへフォールバックする。
    altを区画内で先に試すのは、区画をまたいだ誤帰属(パターン①)を防ぐため。"""
    if zone is not None:
        j = NANIKA_BY_ZONE.get((zone, sfx))
        if j is not None:
            return j
        if alt is not None:
            j = NANIKA_BY_ZONE.get((zone, alt))
            if j is not None:
                return j
    j = NANIKA_L3.get(sfx)
    if j is None and alt is not None:
        j = NANIKA_L3.get(alt)
    return j


def nanika_l3_by_child_suffix(child_names, zone=None):
    """子科目名の接尾辞から親の(何)L3を推定する（stage0見出しが曖昧な場合の先読み）。

    社会福祉法人会計基準のCFでは末端勘定は必ず〜収入/〜収益または〜支出で終わる。
    子を持つstage0見出しは「（何）事業」のグルーピングなので、収入系の子を持つ場合は
    単独枠の（何）収入ではなく、子を持てる（何）事業収入へ帰属させる（旧版からの仕様）。
    また CF側マスタには「（何）収益」が存在しない（（何）収益はPL側のみで、
    load_master_cf が L0=='CF' で絞るため対象外）。そのため「〜収益」で終わる子は
    事業収入側として救済する。
    2026-07: 区画対応化のリファクタでこの救済ルールを落としてしまい、収益表記中心の
    法人(広島いのちの電話)で収入がまるごと未集計になる退行を起こしたため復元した。
    区画内に（何）事業収入が無い区画(施設整備等/その他の活動)では、区画内の
    （何）収入へ寄せる(_nanika_pick の alt)ことで区画またぎの誤帰属を避ける。
    """
    for cn in child_names:
        if cn[-4:] in ('事業収入', '事業収益'):
            return _nanika_pick(cn[-4:], zone, alt='事業収入')
        if cn[-2:] in ('収入', '収益'):
            return _nanika_pick('事業収入', zone, alt='収入')
        if cn[-2:] == '支出':
            return _nanika_pick('支出', zone)
    return None


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


def subject_right_edge(p, default=254.9):
    """科目列の右端x0(=予算(A)列の左側縦罫線)を返す(フェーズ2-9)。

    WAMは列幅を列数で動的に割るため、金額列との境界も座標固定ではなく
    ページごとに縦罫線から取るのが正しい。科目列左端(x0≈60)より右で最初に現れる
    縦罫線を予算(A)列の左端とみなす。継続ページ等で縦罫線が取れない場合は
    従来のハードコード値254.9(=default)にフォールバックする。
    この右端より左が勘定科目、右が金額列。同一行で科目名がスペースにより複数
    トークンに割れていても、この右端までは全て科目名の一部として結合してよい
    (1-4では同一行で親子を表現することは無いため)。"""
    vs = sorted(set(round(rc['x0'], 1) for rc in p.rects if rc['width'] < 3 and rc['height'] > 50))
    cands = [v for v in vs if v > 70]
    return cands[0] if cands else default


def extract_zoneC(pdf, page_range):
    """ゾーンC(科目列、横書き科目名)を上から順に、金額4列付きで抽出。

    (フェーズ2-9) 科目名の分割トークン結合:
    pdfplumberのextract_wordsは、1つの科目名でも内部の字間が広いと複数トークンに
    割る(例:「基本財産特定定期預金取崩収入」→「基本財産特定」「定期預金」「取崩収入」)。
    従来は各トークンを独立行として扱い、右側トークンのx0が大きいため幻のstage4+を
    生み、cur辞書のKeyErrorやstage誤判定を起こしていた。
    1-4では同一行で親子を表現しないので、subject_right_edge(予算A左罫線)より左に
    ある同一行(top近接)のトークンは、x0昇順に連結し空白を除去して1科目とする。
    stage判定は連結後の左端トークンのx0で行う。"""
    rows = []
    for pi in page_range:
        p = pdf.pages[pi]
        words = p.extract_words(x_tolerance=1.5)
        t0 = page_table_top(p)
        right = subject_right_edge(p)
        amt = [w for w in words if w['x0'] >= right]
        subj = [w for w in words if 60 <= w['x0'] < right and w['top'] > t0]
        # 同一行(top近接<=1.5)のトークンをまとめる
        subj.sort(key=lambda w: (w['top'], w['x0']))
        by_top = []  # [(top_key, [words])]
        for w in subj:
            placed = False
            for entry in by_top:
                if abs(entry[0] - w['top']) <= 1.5:
                    entry[1].append(w)
                    placed = True
                    break
            if not placed:
                by_top.append((w['top'], [w]))
        for top, ws in by_top:
            ws.sort(key=lambda w: w['x0'])
            name = normalize_name(''.join(w['text'] for w in ws).replace(' ', ''))
            if len(name) < 2:
                continue
            left_x0 = ws[0]['x0']
            cols = {}
            for lb, lo, hi in AMOUNT_COLUMNS:
                best, bd = None, 2.1
                for a in amt:
                    if lo <= a['x0'] < hi:
                        d = abs(a['top'] - top)
                        if d <= 2.0 and d < bd:
                            best, bd = a['text'], d
                cols[lb] = best
            rows.append({'page': pi, 'top': round(top, 1), 'x0': round(left_x0, 1),
                         'name': name, 'amounts': cols})
    rows.sort(key=lambda r: (r['page'], r['top']))
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


def _l1l2_of_code(code):
    """codeから (L1, L2) を返す。例: 'CF-01-03-001-000-000' -> ('01', '03')。"""
    if not code:
        return None, None
    parts = code.split('-')
    return parts[1], parts[2]


def match_facility(rows):
    """1拠点分のゾーンC科目を階層照合。各rowに status/code/master_name/kind を付与。

    (フェーズ2-7) 照合失敗行(法人特有)の金額プール:
    マスタに無い法人特有科目は status='法人特有' のままだが、金額の行落ちで集計検算が
    合わなくなるのを防ぐため、次を付与する。
    - pseudo_code: 監査用。「直近成功した親の実code + '/' + 未照合科目名」。'/'区切りで
      実codeと衝突しない。親が全く無い(stage0で直近HITも無い)場合は 'CF-<name>'。
    - pool_parent: この金額をプールすべき先の実code。stage>=1で親が特定できていれば
      その親のcode。stage0で親不明のときは直近HITのL1/L2から収入計/支出計のL2集計code。
      いずれも無ければNone(検算に寄与しない純粋な法人特有)。
    - pool_depth: 同一プール親配下での相対的な深さ(親からの段差)。二重計上防止に使う。
      verify_totals は「同一pool_parent配下では最も深い段の行だけ」をプールに採用する
      (見出し=明細合計の2段組で見出しと明細を両方足す二重計上を防ぐ)。
    """
    assign_indent_stage(rows)
    res = []
    # stage -> 直近HITのCF index。任意深さに対応(フェーズ2-9)。
    # 従来は固定辞書{0,1,2,3}で、保育事業収入配下のstage3や、それ以上の深さが
    # 出現するとKeyErrorでクラッシュしていた。defaultdictで任意段に対応する。
    cur = defaultdict(lambda: None)
    last_hit_l1l2 = (None, None)  # stage0失敗時の収入/支出継承用
    zone = ZONE_FIRST             # 現在の区画(区画マーカー通過で進める)
    for i, r in enumerate(rows):
        name, st = r['name'], r['stage']
        found, kind = None, None

        if st == 0:
            if name in L2_NAME:
                found, kind = L2_NAME[name][0], 'L2'
            elif name in L3_NAME:
                found, kind = L3_NAME[name][0], 'L3'
            else:
                nj = nanika_l3_for(name, zone)
                if nj is not None:
                    found, kind = nj, 'L3（何）'
                else:
                    # (フェーズ2-8) 見出し自身では収入/支出を判別できない
                    # (例:「〜事業」で終わる)。直後に続く子(stage>=1が連続する範囲)の
                    # 名前末尾を先読みして収入側/支出側の(何)L3へ帰属する。
                    child_names = []
                    for r2 in rows[i + 1:]:
                        if r2['stage'] == 0:
                            break
                        child_names.append(r2['name'])
                    nj2 = nanika_l3_by_child_suffix(child_names, zone)
                    if nj2 is not None:
                        found, kind = nj2, 'L3（何）子先読み'
            cur.clear()  # stage0見出しで全段リセット(深い段の値の誤継承を防ぐ)
            cur[0] = found
        elif st >= 1 and cur[st - 1] is not None:
            found, kind = match_in_children(name, cur[st - 1])
            cur[st] = found
            # 自分より深い段に残った古い値を消す(兄弟間での誤継承防止)
            for s in list(cur):
                if s > st:
                    cur[s] = None

        if found is not None:
            code = CF[found]['code']
            last_hit_l1l2 = _l1l2_of_code(code)
            res.append({**r, 'status': 'HIT', 'code': code,
                        'kind': kind, 'master_name': leaf_name(CF[found]),
                        'pseudo_code': None, 'pool_parent': None, 'pool_depth': None})
        else:
            # 直近成功した親(自分より浅い段でHITしているcur)を探す
            anc_idx, anc_stage = None, None
            for s in range(st - 1, -1, -1):
                if cur.get(s) is not None:
                    anc_idx, anc_stage = cur[s], s
                    break
            if anc_idx is not None:
                anc_code = CF[anc_idx]['code']
                pseudo = f'{anc_code}/{name}'
                pool_parent = anc_code
                pool_depth = st - anc_stage
            else:
                # stage0で親なし: プール先を持たない純粋な法人特有とする。
                # (フェーズ2-7当初は直近HITのL1/L2から収入計/支出計へプールする案だったが、
                #  様式1-4では収入計/支出計の集計対象ノード CF-01-01-000... 等は金額列を
                #  持たず出現しないため、そこへプールすると「期待値0 vs 計算値=プール額」の
                #  偽NGを生む。実データ上stage0の親なし科目は金額0/Noneで実益も無いため、
                #  プールせず純粋な法人特有に留める。将来実額が出たら別途対応する。)
                pseudo = f'CF-{name}'
                pool_parent = None
                pool_depth = None
            res.append({**r, 'status': '法人特有', 'code': None, 'kind': None,
                        'master_name': None, 'pseudo_code': pseudo,
                        'pool_parent': pool_parent, 'pool_depth': pool_depth})

        # 区画マーカーを通過したら次区画へ（この行の処理後に更新）
        if st == 0:
            _nxt = zone_after(name)
            if _nxt is not False:
                zone = _nxt
    return res


# ---------------- 拠点分割・法人名 ----------------
def _is_facility_title(text):
    """タイトル行のトークンが拠点名か判定する（2026-07実測で確定）。

    旧実装は '拠点区分' の部分一致でページ上部の最初の該当語を拠点名にしていたが、
    科目名「拠点区分間長期借入金収入」「拠点区分間その他の委託費支出」等も
    '拠点区分' を含むため、これらがページ上部に来ると拠点名として誤検出し、
    複数拠点が1バケツに混ざって検算NGを起こしていた（法輪福祉会・大崎福祉会で確認）。

    実測した判別軸: 本物の拠点名トークンは必ず「…拠点区分」で終わる
    （例: 「本部拠点区分拠点区分」「大崎荘拠点区分」「ケアハウス拠点区分」）のに対し、
    科目名は「拠点区分間…」のように '拠点区分' の直後に文字が続く。よって末尾一致で
    確実に切り分けられる（正常系5法人・誤検出2法人の全ページで確認）。
    保険として「拠点区分間」で始まる語も明示的に除外する。"""
    if '拠点区分間' in text:
        return False
    return text.endswith('拠点区分')


def detect_facility_boundaries(pdf):
    bounds = []
    for pi, p in enumerate(pdf.pages):
        for w in p.extract_words(x_tolerance=1.5):
            if w['top'] < 90 and _is_facility_title(w['text']):
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


# 自ノードの検算 expected に採用する kind（科目名そのものが一致したもの）。
# '（何）'系は placeholder への吸収なので、単独行なら expected に含めない。
EXPECTED_KINDS = frozenset({'L2', 'L3', '実名'})


def _pool_amounts_by_parent(fac_rows):
    """法人特有行のプール金額を pool_parent ごとに集計する(フェーズ2-7)。

    二重計上防止は「行単位」で判定する(2026-07 変更)。
    ある法人特有行の直後の行が自分より深い段なら、その行は見出しであり金額は明細の
    合計と重複するため採用しない。そうでなければ内訳を持たない単独項目なので採用する。
    判定基準は _verify_one_facility の is_header と同じ「次行の stage が自分より深いか」。

    旧実装は pool_parent 配下を pool_depth でグルーピングし「最深段の合計だけ」を
    採用していた。これは「1つの見出し+その明細」という2段構造しか想定しておらず、
    同一祖先の下に複数の独立した法人特有科目が並び、そのうち一部だけが内訳を持つ場合に、
    内訳を持たない項目まで一律に切り捨てていた
    (若菜: 1項目 2,646,172円 / 広島県社会福祉協議会: 12項目 15,822,200円が消失)。
    行単位判定なら、見出し+明細の2段組(あと会・東広島の36箇所で確認)も
    単独項目の混在も同時に正しく扱える。

    金額矛盾チェック: 見出し行の金額がその直下明細の合計と一致しない場合に警告を出す
    (検算は明細側を採用して続行)。

    戻り値: (pool_by_parent: {parent_code: 採用金額}, warnings: [str])。
    """
    pool_by_parent = defaultdict(int)
    warnings = []
    n = len(fac_rows)
    for i, r in enumerate(fac_rows):
        if r['status'] != '法人特有' or not r.get('pool_parent'):
            continue
        parent = r['pool_parent']
        nxt = fac_rows[i + 1] if i + 1 < n else None
        is_header = bool(nxt and nxt['stage'] > r['stage'])
        if is_header:
            # 見出しは採用しない(明細側で拾う)。金額が明細合計と合わなければ警告。
            if r['決算B'] is not None:
                own = parse_amount(r['決算B'])
                csum = 0
                for j in range(i + 1, n):
                    r2 = fac_rows[j]
                    if r2['stage'] <= r['stage']:
                        break
                    if (r2['stage'] == r['stage'] + 1 and r2['status'] == '法人特有'
                            and r2['決算B'] is not None):
                        csum += parse_amount(r2['決算B'])
                if own != csum:
                    warnings.append(
                        f'{parent}: 法人特有見出し「{r["name"]}」={own:,} が '
                        f'明細合計={csum:,} と不一致')
        elif r['決算B'] is not None:
            pool_by_parent[parent] += parse_amount(r['決算B'])
    return dict(pool_by_parent), warnings


EXPECTED_KINDS = frozenset({'L2', 'L3', '実名'})


def _pool_amounts_by_parent(fac_rows):
    """法人特有行のプール金額を pool_parent ごとに集計する(フェーズ2-7)。

    二重計上防止: 同一 pool_parent 配下に複数段(見出し+明細)がある場合、
    最も深い pool_depth の行だけを採用する。見出し=明細合計の2段組で
    見出しと明細を両方足す誤りを防ぐ。

    金額矛盾チェック: 同一 pool_parent 配下で「浅い段の合計」と「最深段の合計」が
    どちらも非ゼロなのに一致しない場合、単純な見出し=明細ではない可能性があるため
    警告に載せる(検算は最深段採用で続行)。

    戻り値: (pool_by_parent: {parent_code: 採用金額}, warnings: [str])。
    """
    from collections import defaultdict as _dd
    by_parent_depth = _dd(lambda: _dd(int))  # parent -> depth -> 金額合計
    for r in fac_rows:
        if r['status'] == '法人特有' and r.get('pool_parent') and r.get('決算B') is not None:
            d = r.get('pool_depth') or 1
            by_parent_depth[r['pool_parent']][d] += parse_amount(r['決算B'])

    pool_by_parent = {}
    warnings = []
    for parent, depth_map in by_parent_depth.items():
        depths = sorted(depth_map)
        deepest = depths[-1]
        pool_by_parent[parent] = depth_map[deepest]
        for d in depths[:-1]:
            if depth_map[d] != 0 and depth_map[d] != depth_map[deepest]:
                warnings.append(
                    f'{parent}: プール金額の段間不一致 '
                    f'(depth{d}={depth_map[d]:,} vs depth{deepest}={depth_map[deepest]:,})')
    return pool_by_parent, warnings


def _verify_one_facility(fac_rows, master):
    """1拠点分の行に対する集計検算。空欄子は0。全子未出現の集計行はSKIP。

    同一codeが複数行に出現する場合は合算する(フェーズ2-6で変更)。
    マスタに個別コードを持たない法人特有的な科目が、複数とも同じ(何)placeholder
    (例: CF-03-03-002-005-000（何）取得支出、CF-01-01-017-000-000（何）事業収入)
    に帰属するケースがあり、以前は「最初に見つかった非null値のみ採用・以降は無視」
    としていたため、2件目以降の金額が集計から漏れて検算NGになっていた。
    同一placeholderに複数の実額が乗るのは構造的に正常(マスタの粒度がそこまで
    細かくないだけ)なので、単純合算が正しい。

    (フェーズ2-7) 法人特有行の金額を pool_parent 経由でプールし、その親ノードの
    計算値に加算する。マスタに無い科目の行落ちで親集計が合わないNGを、法人特有の
    まま(=一覧に残したまま)帳尻だけ合わせる。
    (2026-07) is_total ノードへの直接ヒットの扱い:
    法人が大区分に自前の科目(例「選挙広報・名簿製作事業収入」)を立てると、末尾接尾辞に
    より (何) placeholder ノード(例 CF-01-01-017-000-000)へ吸収される。この値は当該
    小計の構成要素ではなく「兄弟」なので、そのまま expected に足すと子の合計と食い違い
    NGになる(広島県視覚障害者団体連合会・寿老園ほか、全440法人中7法人で発生)。
    そこで expected には「実名一致でヒットした行」と「子を従える見出し行」だけを使う
    (amt_exp)。上位集計へ伝播する amt は従来どおり全HITを合算するため、事業活動収入計
    などの上位検算には一切影響しない。印字された小計行が無い場合は検算不能としてSKIP。
    検出力は維持される(印字された小計と子の合計が食い違えば従来どおりNG)。

    戻り値: (ok, ng, skip, ng_list, pool_warnings)。ng_listは (code, 期待値, 計算値)。"""
    # is_total ノードの「印字された小計行」だけを expected に使うための判定。
    # 次の行の stage が自分より深ければ、その行は子を従える見出し(=小計行)。
    is_header = [False] * len(fac_rows)
    for _i, _r in enumerate(fac_rows):
        if _i + 1 < len(fac_rows) and fac_rows[_i + 1]['stage'] > _r['stage']:
            is_header[_i] = True

    amt = {}       # 上位集計へ伝播する値。従来どおり全HITを合算する(変更しない)
    amt_exp = {}   # 自ノードの検算 expected 専用。実名一致 or 見出し行のみを集める
    for _i, r in enumerate(fac_rows):
        if r['status'] == 'HIT' and r['code'] and r['決算B'] is not None:
            _v = parse_amount(r['決算B'])
            amt[r['code']] = amt.get(r['code'], 0) + _v
            if r.get('kind') in EXPECTED_KINDS or is_header[_i]:
                amt_exp[r['code']] = amt_exp.get(r['code'], 0) + _v

    pool_by_parent, pool_warnings = _pool_amounts_by_parent(fac_rows)

    ok = ng = skip = 0
    ng_list = []
    for code, mr in master.items():
        if mr['is_total'] != '1' or not mr['合算定義']:
            continue
        if code not in amt and code not in pool_by_parent:
            skip += 1
            continue
        ch = json.loads(mr['合算定義'])
        if all(c['code'] not in amt for c in ch) and code not in pool_by_parent:
            skip += 1
            continue
        if code in amt and code not in amt_exp:
            # 値は乗っているが、印字された小計行(実名一致 or 見出し)が無い。
            # (何)placeholder に吸収された法人独自の大区分行だけが乗っている状態で、
            # 比較対象となる小計が存在しないため検算不能としてSKIPする。
            skip += 1
            continue
        tot = sum((amt.get(c['code']) or 0) * (1 if c['sign'] == '+' else -1) for c in ch)
        tot += pool_by_parent.get(code, 0)  # このノード自身にプールされた法人特有金額
        expected = amt_exp.get(code, 0)
        if tot == expected:
            ok += 1
        else:
            ng += 1
            ng_list.append((code, expected, tot))
    return ok, ng, skip, ng_list, pool_warnings


def verify_totals(rows, statement='CF'):
    """合算定義による集計検算。拠点ごとに検算してOK/NG/SKIPを積算する。

    同一codeが複数拠点に出現するため、拠点をまたいで一括検算すると2拠点目以降の値が
    握り潰されて検算が壊れる。必ず拠点(拠点区分)ごとに区切って検算すること。
    (フェーズ2-7) 法人特有金額のプール警告も拠点名付きで集約して返す。
    戻り値: (ok, ng, skip, ng_list)。ng_listは (拠点区分, code, 期待値, 計算値)。
    プール警告は verify_totals_with_warnings で取得可能。
    """
    ok, ng, skip, ng_list, _ = _verify_totals_impl(rows)
    return ok, ng, skip, ng_list


def verify_totals_with_warnings(rows, statement='CF'):
    """verify_totals と同じだが、プール金額の段間不一致警告も返す(フェーズ2-7)。
    戻り値: (ok, ng, skip, ng_list, warnings)。warningsは (拠点区分, メッセージ)。"""
    return _verify_totals_impl(rows)


def _verify_totals_impl(rows):
    master = {r['code']: r for r in CF}
    by_fac = defaultdict(list)
    for r in rows:
        by_fac[r.get('拠点区分', '(単一拠点)')].append(r)
    ok = ng = skip = 0
    ng_list = []
    warnings = []
    for fac_name, fac_rows in by_fac.items():
        o, n, s, nl, wl = _verify_one_facility(fac_rows, master)
        ok += o
        ng += n
        skip += s
        for code, exp, calc in nl:
            ng_list.append((fac_name, code, exp, calc))
        for w in wl:
            warnings.append((fac_name, w))
    return ok, ng, skip, ng_list, warnings


def write_csv(rows, out_path):
    cols = ['法人名', '拠点区分', '計算書', 'page', 'top', 'status', 'kind', 'code', 'name',
            'master_name', 'pseudo_code', 'pool_parent', 'pool_depth',
            '予算A', '決算B', '差異AB', '備考']
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
        ok, ng, skip, _, warns = verify_totals_with_warnings(rows)
        print(f"{corp} ({path}): {dict(c)} 計{len(rows)} / 集計検算 OK={ok} NG={ng} SKIP={skip}")
        for fac, w in warns:
            print(f"    ⚠ プール警告[{fac}]: {w}")
