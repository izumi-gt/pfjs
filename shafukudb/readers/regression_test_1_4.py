"""reader_1_4_cf.py 回帰テスト（デグレ防止）。

改修のたびに実行し、確定済みの正解が不変であることを保証する。
- 既知の帰属正解（izumi氏指定・広島若草園）が結果に含まれるか
- 3社の HIT/法人特有 件数、集計検算 OK/NG/SKIP 件数が基準値と一致するか

使い方: python3 regression_test_1_4.py
  PDFは同ディレクトリに hanano_1-4.pdf / hiroshima_1-4.pdf / atokai_1-4.pdf を置くこと。
基準値を意図的に更新したいときは UPDATE_BASELINE=1 python3 regression_test_1_4.py。
"""
import os
import sys
from collections import Counter

import pdfplumber
import reader_1_4_cf as r

# ---- 基準値（確定版reader・拠点ごと検算） ----
BASELINE_COUNTS = {
    'hanano_1-4.pdf':    {'HIT': 550,  '法人特有': 40,  'OK': 45,  'NG': 4,  'SKIP': 175},
    'hiroshima_1-4.pdf': {'HIT': 6102, '法人特有': 558, 'OK': 370, 'NG': 71, 'SKIP': 1575},
    'atokai_1-4.pdf':    {'HIT': 1120, '法人特有': 410, 'OK': 154, 'NG': 32, 'SKIP': 934},
}

# 既知の帰属正解: (PDF, 拠点index, 科目名, インデント段, 期待code or None, 期待status)
# 「結果セットの中に、この (stage, code, status) を持つ行が最低1つ存在する」ことを確認する。
KNOWN_ATTRIBUTIONS = [
    ('hiroshima_1-4.pdf', 0, '県立施設運営事業収入',            0, 'CF-01-01-017-000-000', 'HIT'),
    ('hiroshima_1-4.pdf', 0, '受託事業収入',                    1, 'CF-01-01-017-001-000', 'HIT'),
    ('hiroshima_1-4.pdf', 0, 'スポーツ交流センター運営事業収入', 2, None,                   '法人特有'),
    ('hiroshima_1-4.pdf', 0, 'その他の事業収入',                1, 'CF-01-01-017-002-000', 'HIT'),
    ('hiroshima_1-4.pdf', 0, '補助金事業収入（公費）',          2, 'CF-01-01-009-007-001', 'HIT'),
    ('hiroshima_1-4.pdf', 0, '県納付金支出',                    0, 'CF-01-03-006-000-000', 'HIT'),
]


def run():
    failures = []

    # 1) 件数・検算の基準チェック
    for path, base in BASELINE_COUNTS.items():
        if not os.path.exists(path):
            failures.append(f'[SKIP不可] PDFが無い: {path}')
            continue
        rows = r.process_pdf(path)
        c = Counter(x['status'] for x in rows)
        ok, ng, skip, _ = r.verify_totals(rows)
        got = {'HIT': c['HIT'], '法人特有': c['法人特有'], 'OK': ok, 'NG': ng, 'SKIP': skip}
        for k, v in base.items():
            if got[k] != v:
                failures.append(f'[件数] {path} {k}: 期待{v} 実際{got[k]}')

    # 2) 既知の帰属チェック
    for path, fac_i, name, stage, code, status in KNOWN_ATTRIBUTIONS:
        if not os.path.exists(path):
            continue
        pdf = pdfplumber.open(path)
        ranges = r.build_facility_ranges(pdf)
        prng = ranges[fac_i][1]
        res = r.match_facility(r.extract_zoneC(pdf, prng))
        hit = any(x['name'] == name and x['stage'] == stage and x['code'] == code and x['status'] == status
                  for x in res)
        if not hit:
            actual = [(x['stage'], x['code'], x['status']) for x in res if x['name'] == name]
            failures.append(f'[帰属] {path} 拠点{fac_i} 「{name}」stage{stage} '
                            f'期待({code},{status}) が見つからない。実際={actual[:6]}')

    if failures:
        print(f'❌ 回帰テスト失敗: {len(failures)}件')
        for f in failures:
            print('   ' + f)
        return 1
    print('✅ 回帰テスト全通過（件数・検算・既知帰属すべて基準どおり）')
    return 0


def update_baseline():
    """意図的に基準値を更新する（改修で件数が変わることを承認した場合のみ）。"""
    print('# 新しい BASELINE_COUNTS（手動でスクリプトに反映すること）')
    for path in BASELINE_COUNTS:
        if not os.path.exists(path):
            continue
        rows = r.process_pdf(path)
        c = Counter(x['status'] for x in rows)
        ok, ng, skip, _ = r.verify_totals(rows)
        print(f"    '{path}': {{'HIT': {c['HIT']}, '法人特有': {c['法人特有']}, "
              f"'OK': {ok}, 'NG': {ng}, 'SKIP': {skip}}},")


if __name__ == '__main__':
    if os.environ.get('UPDATE_BASELINE') == '1':
        update_baseline()
    else:
        sys.exit(run())
