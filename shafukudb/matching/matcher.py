"""2ポインタ照合エンジン。様式(CF/PL/BS)非依存。

抽出層(readers/*.py)が作った「横書き科目名のリスト(page, top, text)」を受け取り、
正典マスタのcodeへ突き合わせる。抽出層の実装(座標・改ページ処理)には一切依存しない。

確立した方針(フェーズ2-3):
- マスタはCSV行順(WAM出現順)で単調前進。codeは遡らない。
- 完全一致のみ。先読みは範囲無制限、近傍探索はしない(ポインタずれの危険が上回るため)。
- HIT / NANIKA(直近HITとL1-L3一致の「（何）」に割当) / UNRESOLVED(親一致「（何）」も無い
  =WAM生成の未登録科目、code=Noneで退避・金額は保持) の3分類。
- 同名連続は「自身(直前HITしたcode)の子の範囲」でのみ再探索。子があればHIT、なければUNRESOLVED。
"""
import csv
import json

from .seiten_loader import load_master, leaf_name, is_nanika


def parent_segments_match(code_a, code_b):
    """2つのcodeのL1〜L3セグメントが一致するか(同じ親配下の兄弟か)。"""
    sa = code_a.split('-')[1:4]
    sb = code_b.split('-')[1:4]
    return sa == sb


def deepest_level(code):
    """codeの最も深い非000セグメントの位置(1..5)を返す。"""
    segs = code.split('-')[1:]
    lvl = 0
    for i, s in enumerate(segs):
        if s not in ('000', '00'):
            lvl = i + 1
    return lvl


def is_child_of(parent_code, code):
    """codeがparent_codeの直接の子か。parentの末端レベルの1つ下でセグメント共有。"""
    pl = deepest_level(parent_code)
    if pl >= 5:
        return False
    ps = parent_code.split('-')[1:]
    cs = code.split('-')[1:]
    if ps[:pl] != cs[:pl]:
        return False
    if cs[pl] in ('000', '00'):
        return False
    for k in range(pl + 1, 5):
        if cs[k] not in ('000', '00'):
            return False
    return True


def run_match_subjects(subjects, statement='CF'):
    """2ポインタ照合の本体。

    subjects: [{'page':int, 'top':float, 'text':str}, ...] (top順に並んでいること)
    戻り値: 各subjectに status/code/master_name を付けたリスト。
    """
    cf = load_master(statement)

    mp = 0
    save_point = None
    last_hit_idx = None
    results = []

    for s in subjects:
        text = s['text']
        found = None

        # 直前HITと同名の場合: 自身(直前HITしたcode)の子の範囲でのみ探す
        if last_hit_idx is not None and text == leaf_name(cf[last_hit_idx]):
            parent_code = cf[last_hit_idx]['code']
            child_found = None
            for j in range(mp, len(cf)):
                if not is_child_of(parent_code, cf[j]['code']):
                    break  # mp位置から連続する子だけを見る。子でない行が来たら打ち切り
                if leaf_name(cf[j]) == text:
                    child_found = j
                    break
            if child_found is not None:
                mp = max(mp, child_found + 1)
                last_hit_idx = child_found
                results.append({'pdf': text, 'page': s['page'], 'top': s['top'],
                                 'code': cf[child_found]['code'], 'master_name': leaf_name(cf[child_found]), 'status': 'HIT'})
            else:
                # 子に同名が無い = WAM生成の重複科目。UNRESOLVEDへ。mpは消費しない。
                results.append({'pdf': text, 'page': s['page'], 'top': s['top'],
                                 'code': None, 'master_name': None, 'status': 'UNRESOLVED'})
            continue

        # 通常の先読み(mpから前進探索、範囲無制限)
        for j in range(mp, len(cf)):
            mname = leaf_name(cf[j])
            if is_nanika(mname):
                continue
            if text == mname:
                found = j
                break

        if found is not None:
            for j in range(mp, found):
                if is_nanika(leaf_name(cf[j])):
                    save_point = j
            mp = max(mp, found + 1)
            last_hit_idx = found
            results.append({'pdf': text, 'page': s['page'], 'top': s['top'],
                             'code': cf[found]['code'], 'master_name': leaf_name(cf[found]), 'status': 'HIT'})
        else:
            # NANIKA候補: 直近HIT行とL1〜L3が一致する（何）行のみを対象にする
            hit_code = cf[last_hit_idx]['code'] if last_hit_idx is not None else None
            new_save = None
            if hit_code is not None:
                for j in range(mp, len(cf)):
                    if is_nanika(leaf_name(cf[j])) and parent_segments_match(hit_code, cf[j]['code']):
                        new_save = j
                        break
            if new_save is not None:
                save_point = new_save
                results.append({'pdf': text, 'page': s['page'], 'top': s['top'],
                                 'code': cf[save_point]['code'], 'master_name': leaf_name(cf[save_point]), 'status': 'NANIKA'})
            elif save_point is not None and hit_code is not None and parent_segments_match(hit_code, cf[save_point]['code']):
                results.append({'pdf': text, 'page': s['page'], 'top': s['top'],
                                 'code': cf[save_point]['code'], 'master_name': leaf_name(cf[save_point]), 'status': 'NANIKA(継続)'})
            else:
                # 親一致する（何）が無い = WAM生成の未登録科目の可能性。誤割当を避けて退避
                results.append({'pdf': text, 'page': s['page'], 'top': s['top'],
                                 'code': None, 'master_name': None, 'status': 'UNRESOLVED'})

    return results


def parse_amount(s):
    """金額文字列(カンマ区切り・△▲マイナス表記)を整数に変換。空欄はNone。"""
    if s is None or s == '':
        return None
    return int(s.replace(',', '').replace('△', '-').replace('▲', '-'))


def verify_totals(rows, statement='CF'):
    """集計行(is_total=1)について、合算定義の子の符号付き和が集計行の値と一致するか検算。

    空欄(None)の子は0とみなす(決算書では0項目が空欄表示される通例)。
    全子がNoneの集計行は検算不能でSKIP。金額は決算(B)列で検算する。
    戻り値: (ok, ng, skip, ng_list)。ng_listは (code, 期待値, 計算値) のリスト。
    """
    cf = load_master(statement)
    master = {r['code']: r for r in cf}
    amt = {}
    for r in rows:
        if r.get('status') == 'HIT' and r.get('code') and r.get('決算B') is not None:
            if r['code'] not in amt:
                amt[r['code']] = parse_amount(r['決算B'])

    ok = ng = skip = 0
    ng_list = []
    for code, r in master.items():
        if r['is_total'] != '1' or not r['合算定義']:
            continue
        if code not in amt:
            skip += 1
            continue
        children = json.loads(r['合算定義'])
        child_vals = [amt.get(ch['code']) for ch in children]
        if all(v is None for v in child_vals):
            skip += 1
            continue
        total = 0
        for ch in children:
            cv = amt.get(ch['code']) or 0
            total += cv if ch['sign'] == '+' else -cv
        if total == amt[code]:
            ok += 1
        else:
            ng += 1
            ng_list.append((code, amt[code], total))
    return ok, ng, skip, ng_list


def write_csv(rows, out_path):
    """照合結果をDB投入前形式のCSVとして書き出す。"""
    cols = ['法人名', '拠点区分', '計算書', 'page', 'top', 'status', 'code', 'pdf', 'master_name',
            '予算A', '決算B', '差異AB', '備考']
    with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow(r)
