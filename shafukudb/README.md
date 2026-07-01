# shafukudb（再構築版）

社会福祉法人 決算PDF → 構造化データベース化プロジェクトの、フェーズ2-3時点での再構築版。

## この構成に置き換えた理由

旧 `shafukudb/`（`normalize.py` / `matcher.py` / `seiten.py` / 旧マスタ691行）は、
以下2点により**設計の前提そのものが成立しなくなった**ため置き換えた。

1. **正典マスタがv3（1,061行、6セグメントcode `L0コード`〜`L5科目`）に更新され、
   旧マスタ（691行、`活動・部`/`区分`列、5セグメントcode）とスキーマが根本的に異なる。**
2. **旧`matcher.py`はインデント深度（縦書き軸帯・座標）に依拠した照合を前提にしていたが、
   WAM生成PDFは改ページでセルが物理的に分断され、見えない重複文字が同一座標に重なって
   埋め込まれる（どのページでも発生）ため、縦書き軸帯は座標ベースでは原理的に読めないことが
   フェーズ2-3で判明した。**

旧ファイルはリポジトリ内に**削除せず archive として残す**こと（`deliver/` と同じ運用）。
実体は旧セッションのGitHub上にのみ存在し、このセッションのサンドボックスには無いため、
実際のアーカイブ操作（`shafukudb/` → `archive/shafukudb_v1_indent_based/` 等への移動）は
izumi氏がリポジトリ側で行う。

## 新しい構成

```
shafukudb/
  seiten_master_v3.csv        正典マスタ(唯一の正本、1,061行)
  matching/                   照合層。様式(CF/PL/BS)非依存。
    seiten_loader.py            マスタ読み込み(load_master, leaf_name, is_nanika)
    matcher.py                   2ポインタ照合・NANIKA/UNRESOLVED判定・
                                  同名の子探索・集計検算(verify_totals)・CSV出力
  readers/                    抽出層。様式ごとに完全に別実装。
    reader_1_4_cf.py             様式1-4(拠点区分CF)専用。座標定数・改ページ処理・
                                  左端2列の混入対策はこのファイル固有。
    (今後) reader_1_2_cf.py      様式1-2用。ゼロから実測して実装する。
```

### 層の分離の考え方

- **照合層(matching/)** はどの様式でも同じロジックで動く。マスタをCSV行順で読み、
  横書き科目名のリスト `[{'page','top','text'}, ...]` を受け取ってcodeに突き合わせるだけで、
  PDFの座標や罫線の知識を一切含まない。
- **抽出層(readers/)** は様式ごとのPDF形式（罫線構造・改ページ挙動・縦書き軸帯の混入パターン）
  に依存する。様式が変われば実測からやり直し、新しい `reader_*.py` を追加する。
  `matching/` 側は変更しない想定。

### 使い方

```python
from readers.reader_1_4_cf import process_pdf
from matching.matcher import verify_totals, write_csv

rows = process_pdf('example_1-4.pdf', statement='CF')
ok, ng, skip, ng_list = verify_totals(rows, statement='CF')
write_csv(rows, 'out.csv')
```

reader を直接実行すると、同ディレクトリのPDFに対する動作確認と集計検算が走る
（`python3 readers/reader_1_4_cf.py`。要 `pdfplumber`）。

## 参照

- `フェーズ2-3_様式1-4読み取り機の確立.md`：本構成の元になった検証記録・失敗知見。
- `引き継ぎプロンプト_様式1-2読み取り機.md`：次にreaderを追加する際の申し送り。
