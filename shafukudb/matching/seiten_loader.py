"""正典マスタ(seiten_master_v3.csv)の読み込み。様式(CF/PL/BS)非依存。

reader_1_4_cf.py から分離(フェーズ2-3)。今後の様式別readerはすべてここを共通で使う。
"""
import csv
from pathlib import Path

# shafukudb/ ルートに置かれた正典マスタを、このファイルの位置から相対的に解決する。
# 呼び出し元の作業ディレクトリに依存しないようにするため。
MASTER_PATH = Path(__file__).resolve().parent.parent / 'seiten_master_v3.csv'


def load_master(statement='CF', master_path=None):
    """正典マスタをCSV行順(=WAM出現順)のまま読み込み、指定した計算書(L0コード)配下だけを返す。

    statement: 'CF' / 'PL' / 'BS'
    戻り値の行順は必ずCSVの物理行順を保つ(code文字列の昇順ではない。
    一部に行順の逆転が実在するが、それも含めてWAM出現順として扱う)。
    """
    path = master_path or MASTER_PATH
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r['L0コード'] == statement]


def leaf_name(r):
    """最も深いレベルの科目名(マスタの正式表記)。L5から順に探す。"""
    for i in range(5, 0, -1):
        if r[f'L{i}科目']:
            return r[f'L{i}科目']
    return ''


def is_nanika(name):
    """「（何）」を含むプレースホルダ科目か。"""
    return '（何）' in name
