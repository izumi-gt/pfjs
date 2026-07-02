# shafuku_db_engine — 社会福祉法人 決算情報 統合DB 構築・検算エンジン

社会福祉法人の計算書類（全12様式）を、SQL移行を前提とした正規化済みの統合DB
（`決算情報データベース.xlsx`）へ取り込むためのローカル実行エンジン。

PDF→構造化データの起こしは人/Claudeが行う前提。本エンジンは
**「構造化データ → 全件検算 → fact/dim生成 → Excel出力 → 事後検査」** を一気通貫で回す。

---

## 1. 設計思想

- **fact + dim の正規化**：`fact_financial` は金額のみ保持し、属性は dim 側へ。1シート=1テーブルで、そのまま `CREATE TABLE` / `COPY` に載る。
- **科目の2層化（幹/枝）**：
  - **global（幹）** = 全国共通の大区分/中区分/計/差額/残高。`dim_account_global`。法人横断比較はこのコードで突合。
  - **local（枝）** = 法人別の小区分・明細。`dim_account_local`。`L-{法人番号}-{連番}`、`親科目コード`で幹に紐付く。
  - localコードは **(拠点, 親, 名称) で一意化**。同名の小区分が別の親に並存（例：事業費の雑費／事務費の雑費）しても衝突しない。
- **元様式再現シートは持たない**。照合は原本PDFで行う（5万社・SQL運用では「シート」概念と相容れないため）。

## 2. テーブル

| シート | 役割 | 主キー |
|---|---|---|
| `fact_financial` | 縦持ち事実表（1行=法人×年度×様式×科目×事業区分×metric） | 6列複合 |
| `dim_corp` | 法人マスタ | 法人番号 |
| `dim_form` | 様式マスタ（CF-1-1 … BS-3-4 の12） | 様式コード |
| `dim_account_global` | 共通科目（幹・全国固定） | 科目コード |
| `dim_account_local` | 法人別小区分（枝・追記型、親コード付き） | 科目コード |
| `dim_segment` | 事業区分（法人全体/事業区分/拠点/合計/内部取引消去/事業区分合計） | 事業区分コード |

## 3. 検算（取り込み前に全件自己検証。1件でも不一致なら中止）

`validate.validate(corp)` が以下を検査する：

- **CF**：各科目和=計、収入計−支出計=差額、(3)+(6)+(9)−予備費=当期収支差額合計、前期末+当期=当期末残高
- **PL**：サービス活動増減=収益計−費用計、＋活動外=経常、＋特別=当期活動、＋前期繰越=当期末繰越、±積立金=次期繰越
- **BS**：資産合計=負債合計+純資産合計、当年度末−前年度末=増減
- **2階層の小区分和=親**（明細→小区分→大区分）
- **事業区分**：区分和=合計、合計−内部取引消去=法人合計/事業区分計
- **様式間突合**：1-4↔1-3↔1-2↔1-1、2-4↔2-3↔2-2↔2-1、3-4↔3-3↔3-2↔3-1
- **計算書またぎ**：BS当期活動増減差額=PL当期活動増減差額、BS次期繰越=PL次期繰越

出力後は `build.post_checks` が FK違反・fact主キー重複・local親参照を再検査する。

## 4. 使い方（CLI）

```bash
# 検算のみ
python -m shafuku_db_engine validate corps.kanagawa

# 検算 → 取り込み → 新規Excel出力
python -m shafuku_db_engine ingest corps.kanagawa --out 決算情報データベース.xlsx

# 既存DBに追記（別法人を足す）
python -m shafuku_db_engine ingest corps.shinhojin --append 決算情報データベース.xlsx --out 決算情報データベース.xlsx
```

検算で不一致が出ると取り込みは中止される（DBを汚さない）。`--force` で強制可だが非推奨。

依存：`pip install openpyxl`

## 5. 新しい法人を追加する手順

1. `corps/kanagawa.py` と `corps/_kanagawa_{cf,pl,bs}.py` をコピーして法人名にリネーム。
2. `_*_cf/pl/bs.py` の各データ辞書を、その法人のPDFを読んで差し替える（形式は下記）。
3. `corps/<法人>.py` の `CorpData(...)` の法人番号・名称・拠点・`kyoten_loc` を更新。
4. `python -m shafuku_db_engine validate corps.<法人>` で検算（不一致0を確認）。
5. `--append` で既存DBへ取り込み。

幹マスタ（`masters.py`）は原則さわらない。**全国共通の標準科目で幹に無いもの**が出たときだけ、`masters.py` の該当マスタに1行追加する（業種固有の明細は local 側＝データ内のdepth1/2行として持てばよく、幹に足さない）。

## 6. データ形式（corps/_*.py）

```
法人単位 1-1/2-1/3-1 : code -> (m0, m1, m2)
    CF 1-1 = (予算, 決算, 差異)  /  PL 2-1 = (当年度決算, 前年度決算, 増減)  /  BS 3-1 = (当年度末, 前年度末, 増減)
事業区分別 1-2/2-2/3-2: code -> (社福, 公益, 収益, 合計, 内部取引消去, 法人合計)
拠点別     1-3/2-3/3-3: code -> ([拠点...], 合計, 内部取引消去, 事業区分計)
拠点明細   1-4/2-4    : {拠点: [ (depth, code|名称, m0, m1, m2[, 親]) ]}
                        depth0 = 幹stem(科目コード)
                        depth1 = 小区分（名称, 親=幹stemコード）
                        depth2 = 明細（名称, 親="親小区分名@拠点名"）
拠点明細   3-4        : {拠点: { code: (当年度末, 前年度末, 増減) }}  ※明細行なし
```

`None` は「空欄＝行を作らない」。各様式に存在する metric だけが fact 行になる。

## 7. ファイル構成

```
shafuku_db_engine/
  __init__.py        公開API（CorpData / validate / ingest / build）
  __main__.py        CLI
  schema.py          様式・metric・テーブル定義・コード採番
  masters.py         幹マスタ（global CF/PL/BS、全国共通・固定）
  ingest.py          構造化データ → fact + dim 生成（local採番は(拠点,親,名称)で一意化）
  validate.py        全恒等式・様式間突合・計算書またぎ検算
  build.py           Excel出力（新規/追記）＋FK・主キー重複の事後検査
  corps/
    kanagawa.py            参照実装（神奈川厚生福祉会・全12様式・検算済み）
    _kanagawa_cf/pl/bs.py  同データ本体
```


## Option1拡張（registryがコード一元管理・橋渡しアダプタ用）

PDFパーサ(`shafuku_parser`)の registry が採番した local コードを注入できる:
- `CorpData.local_codes = {(loc, parent_code, name): {"code": "L-...", "concept": "LC-...", "io": "..."}}`
- 与えると add_local はその L-/LC- を使い、`dim_account_concept`（概念マスタ）を生成。
- 与えなければ従来通りエンジンが自前採番（既存 corps/*.py は無改修で同一結果）。
- `dim_account_local` に「概念コード」列を追加、新テーブル `dim_account_concept`
  (概念コード/計算書/親科目コード/収支区分/正規名) を追加。
- 神奈川の基準線(fact 3,326行)は本拡張後も完全再現することをテストで確認済み。
