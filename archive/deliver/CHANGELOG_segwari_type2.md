# CHANGELOG — タイプ2 橋渡しアダプタ実装（事業区分別内訳表 1-2 / 2-2 / 3-2）

分岐タイプ2。`shafuku_kyoten4_adapter.zip`（タイプ4まで完成・修正5件入り）を土台に、
タイプ2（事業区分別内訳表）のアダプタを実装した。あしたか太陽の丘 1-2/2-2/3-2 を
**validate NG=0** で取り込み。神奈川基準線（fact 3,326 / sha256 一致 / NG=0）は**非破壊**。

---

## 新規追加ファイル（衝突しない・様式群専用）

### shafuku_parser/adapter_segwari.py
タイプ2の橋渡しアダプタ。
- `build_corpdata_segwari(pdf_paths, corp_no, corp_name, fiscal_year, masters, ..., base_corp=None)`
  → `(CorpData, regs, review_rows, held_rows)`
- `extract_segwari` のフラット出力 `[(name,{col0..5})]` を走査し、各行を
  `resolve_global` で1対1に幹stemコード化。`corp.cf["1-2"]/pl["2-2"]/bs["3-2"]
  = {code:(社福,公益,収益,合計,内部消去,法人合計)}` を組む。
- タイプ2は depth 階層なし（フラット様式）。親子 local 構築は不要。
- 未知科目（master非収載）は `resolve_local` でレビューキューに積むが **fact には載せず
  held に退避**（理由は下記「設計判断」）。`_build_io_map` はタイプ4アダプタから再利用。
- `base_corp` を渡せば既存 CorpData に追記でき、他タイプとの統合 ingest に対応。

### shafuku_parser/run_segwari.py
タイプ2の無人完走ランナー（検算→隔離→CSV）。タイプ4ランナーのCSVヘルパを流用。
出力: fact / dim各種 / review_queue / quarantine / **held_unresolved**（未知科目・値つき）。

---

## 共有ファイルへの変更（本体マージ対象・最小修正）

### shafuku_parser/extract_segwari.py（2点）

**(1) 金額最上位桁の科目名混入バグ（CF/PL/BS 共通・20件超）**
- 症状: 値列の金額の先頭桁が科目名領域(x < first_edge-20)へ食い込み、科目名が
  `流動資産→'1'`、`障害福祉サービス等事業収入→…収入1`、`人件費→人件費1` 等に化け、
  `resolve_global` が全滅していた。
- 原因: name/value の x 分離だけでは、金額の最上位桁が境界を越えた場合に弾けない。
  さらに `_clean_name_chars` の stage2 が、混入した数字を「本体」とみなして
  本来の科目名（流動資産）を margin として捨てていた。
- 修正: name バケット構築時に **半角の数値グリフ `0-9 , -` を除外**する
  （`_HALFWIDTH_NUMGLYPH`）。科目名の連番は全角 `（１）` で別物なので影響なし
  （`east_asian_width`: 半角=Na / 全角=F で判別可能なことを確認）。
- 該当行: import 群直後に `_HALFWIDTH_NUMGLYPH = set("0123456789,-")` を追加。
  `extract_segwari` の「科目名文字を最近傍数値行へスナップ」ループに
  `if c["text"] in _HALFWIDTH_NUMGLYPH: continue` を追加。

**(2) `_skip` の `（自`/`（至` 過剰一致（本体修正③と同種）**
- 症状: `bad_substr` に部分一致 `（自`/`（至` があり、科目名 `訓練等給付費収益（自立）`
  を誤って落とす。
- 修正: 部分一致を撤廃し、**行頭アンカー** `^[（(]\s*[自至]` の正規表現 `_re_jishi` で
  期間ヘッダ（`（自）令和…`）とその誤分割断片（`（自`）だけを除外。科目名の `（自立）` は
  括弧が名前の途中に現れるため安全に区別できる。
  ※ 当初 `（自）` 閉じ括弧マッチも試したが、ヘッダが `（自` 断片に割れる実データがあり、
    行頭アンカー方式が最も頑健だった。
- 該当行: import 群直後に `_re_jishi = re.compile(r"^[（(]\s*[自至]")` を追加。
  `_skip` の `bad_substr` から `（自）/（自/（至）/（至` を除去し、`if _re_jishi.search(n): return True` を追加。

> engine（ingest.py / validate.py / masters.py / schema.py）と registry.py / naming.py は
> **一切変更していない**。本体マージで衝突するのは extract_segwari.py のみ。

---

## 設計判断（本体レビュー要確認）

**未知科目（master非収載）は fact に載せず held + review に退避する。**
- 理由: engine の `emit_segwari` はタイプ2の local を `dim_account_local` に
  materialize しない（タイプ2は元々「全行が幹stem」前提の実装）。未知科目に
  provisional な `L-` コードを振って fact に載せると、`dim_account_local` 欠落＝
  **FK破壊**になる（実測で確認: 載せると orphan L- コードが 2件発生）。
- 対応: 未知科目は `resolve_local` でレビューキューに積みつつ、fact には入れず
  `held_unresolved.csv`（科目名・6列値・暫定コード）へ退避。値が非ゼロなら人手で
  master 採番後に再取り込みする運用。あしたかの該当2件（事業区分間貸付金/借入金）は
  **いずれも全列0**なので情報損失なし。
- 代替案: engine 側で type-2 local の dim 出力を追加することも可能だが、共有ファイル
  （ingest.py）の変更＝マージ衝突増になるため、本分岐ではアダプタ側で完結させた。
  本体での最終判断に委ねる。

---

## 検証結果（あしたか太陽の丘 8080105000129）

| 様式 | 結果 |
|---|---|
| 1-2 資金収支内訳表 (CF) | validate NG=0 |
| 2-2 事業活動内訳表 (PL) | validate NG=0 |
| 3-2 貸借対照表内訳表 (BS) | validate NG=0 |

- ingest fact: **992行**、held(未知科目): 2件、review: 4件、segments: 7、orphan FK: **0（クリーン）**。
- **内部整合**: 全科目で `社福+公益+収益==合計`、`合計-内部消去==法人合計`（violations=0）。
- **PDF突合**: 事業活動収入計(1,697,198,806)・サービス活動収益計・資産の部合計
  (4,101,046,674)・負債純資産合計 等が PDF と完全一致。
- **タイプ4との相互突合**（参考・突合の妥当性確認）: BS 法人合計(type2) vs 全拠点総和(type4)
  は 15科目中9一致。差分6件はすべて **拠点区分間貸付金/借入金（148,082,534）等の
  拠点間取引**で説明でき、type-2(法人合計=消去後) と type-4(拠点別=消去前) の
  測定段階の違いによる正当な差（type-2のBSバランス自体は完全一致でNG=0）。
- **神奈川基準線**: fact 3,326 / sha256 `0aa61d8b…f102e54` 一致 / NG=0（engine非破壊を確認）。

---

## 本体マージ時の申し送り
1. 共有ファイルの実差分は **extract_segwari.py のみ**（上記2点）。3-wayマージはここだけ。
2. 設計判断（未知科目 held 方式）の採否を確認。engine側で type-2 local を materialize
   する方針に変えるなら ingest.py の emit_segwari 拡張が必要。
3. 全12様式統合時、タイプ2 CorpData は `base_corp` 経由で他タイプと同一 CorpData に
   合流させられる（cf/pl/bs のキーが 1-2/2-2/3-2 で重複しないため安全）。
