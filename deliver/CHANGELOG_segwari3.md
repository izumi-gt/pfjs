# CHANGELOG — タイプ3 拠点別内訳表アダプタ（1-3 / 2-3 / 3-3）

分岐タイプ3。あしたか太陽の丘の 1-3/2-3/3-3 を全様式 **NG=0** で取り込み、
タイプ4（確定済み正解値）との拠点×科目クロス突合も **全点一致**。神奈川基準線
（fact 3,326・sha256 `0aa61d8b…f102e54`・NG=0）は**非破壊**を維持。

---

## 新規追加ファイル（2件・他ブランチと衝突しない別名）

### shafuku_parser/adapter_segwari3.py
タイプ3アダプタ本体。`extract_segwari3(pdf)` のブロック列を engine の「拠点別」形
`corp.cf/pl/bs["1-3"等] = { code: ([拠点値...], 合計, 内部消去, 事業区分計) }` へ配線。
- **2事業区分ブロックの統合**: 社会福祉事業区分(8拠点)＋公益事業区分(3拠点)を
  11拠点の単一行へ統合。拠点並びは block 順連結（社福8→公益3）で、タイプ4の拠点順と一致。
  合計/内部消去/事業区分計は block ごとの値を総和（各 block で Σ拠点=合計・
  合計-消去=区分計 が成立するため、総和後も両恒等式が成立）。
- **末尾3列**＝合計/内部取引消去/事業区分合計(計)。拠点列＝colnames[:-3]。
- **本部拠点**の法人名融合は `adapter_kyoten4._strip_corp_prefix_for_honbu` を流用
  （あしたかは本部拠点を持たないが、神奈川型に備え防御的に適用）。
- **BS depth1 の扱い**: 全 depth1 を `resolve_local`（親=直近depth0の幹stem）で local 採番。
  理由: BS master には 土地/建物/定期預金/貸倒引当金/その他の固定資産 が
  **中区分違いで2回ずつ**存在し、`resolve_global`（指紋=正規化名+計算書）では中区分を
  区別できず衝突する。local は親stemで区別できるため衝突しない。これはタイプ4 BSアダプタ
  （depth1 を local 化）とも一貫。local 行/概念行は `corp._segwari3_locals` /
  `corp._segwari3_concepts` に格納し、ランナーが ingest 後に注入する（下記参照）。

### shafuku_parser/run_segwari3.py
タイプ3 無人完走ランナー（adapter→検算→隔離→CSV）。run_kyoten4 の補助関数を再利用。
- **local 行注入**: engine.ingest は明細様式(1-4/2-4)のみ build_local_for を回し add_local する。
  タイプ3は emit_kyoten 経由で add_local を呼ばないため、アダプタ生成の local/concept 行を
  ingest 後に Ingested へ注入する。FK/PK 事後検査=0 を確認済み。
- **隔離粒度**: 拠点が全コード横断のため拠点単位の部分除去は不可。NG の出た様式を
  様式単位で隔離する（あしたかは全 green のため未発火）。

---

## 既存共有ファイルへの変更（1件のみ・要マージ確認）

### shafuku_parser/extract_segwari3.py  ← ★本ブランチで変更した唯一の共有ファイル
タイプ3固有の「縦分割（行が次ページへ）」で既存バグを2件踏んだため最小修正。
**engine（ingest/validate）・naming・registry・他のextractは一切変更していない**ので、
他ブランチとのマージ衝突は extract_segwari3.py のみで起きる。

1. **縦連続ページのアンカー欠落 → 値の誤割当**
   - 症状: 公益事業区分BSの継続ページで、3拠点目(訓練校給食)の値が疎なため
     `detect_anchors`(min_members=5) がその列アンカーを検出できず、
     `負債及び純資産の部合計` の訓練校 5,230,204 が隣列(合計)へ吸われて消失。
   - 修正: `extract_page` の各行に生数値 `rawnums`(text,x1) を保持。
     `extract_type3_multi` の縦連続マージで、継続ページのアンカー(欠けている)ではなく
     **グループ(先頭ページ)の全アンカー**で `assign_columns` を再実行して列割当。
   - 非破壊性: アンカーが一致する通常ページ/既存corpsでは結果不変。

2. **縦分割の行top衝突 → 科目の入れ替わり**
   - 症状: 継続ページの行（新しい勘定科目）が、メインページの行と top 座標が偶然一致し、
     `merged[round(rt,1)]` で同一行としてマージされ科目順が崩壊（流動資産が純資産の後に
     出る等）。結果、区分間貸付金/借入金の親stemが誤って `BS-LN-T` に。
   - 修正: 縦連続ページの行 top に十分大きなオフセット（既出最大top+1000）を与え、
     メインページ行の後ろへ**追記**する（縦分割＝新規行の追加であり、top一致による
     マージをしない）。
   - 非破壊性: 縦分割が無いページ/既存corpsでは merged キーに影響なし。

> どちらも `extract_segwari3.py` 内に閉じる。修正後、神奈川基準線 sha256 一致を再確認済み
> （engine 無改修なので当然だが、規律として毎回確認）。

---

## 検証結果

| 対象 | 様式 | NG | 備考 |
|---|---|---|---|
| 神奈川厚生福祉会 | 全12様式 | 0 | fact 3,326・sha256一致（基準線非破壊） |
| あしたか太陽の丘 | 1-3 (CF) | 0 | 8拠点+3拠点 統合 |
| あしたか太陽の丘 | 2-3 (PL) | 0 | 同上 |
| あしたか太陽の丘 | 3-3 (BS) | 0 | depth0 15 + depth1 local化 |
| 統合DB（神奈川12+あしたかtype3） | — | 0 | fact 4,784・FK/PK problems=0 |

**タイプ4とのクロス突合（タイプ4は確定済み正解値）**: 拠点×科目で
- CF: 383点一致（mismatch=0）
- PL: 413点一致（mismatch=0）
- BS: 165点一致（mismatch=0, 幹stem×11拠点）
depth1 値は engine の Σ拠点=合計・合計-消去=区分計 検算（NG=0）で間接的に保証。

あしたか type3 CSV出力（fact 1,458 / concept 70 / local 70 / review 140 / quarantine 0）。

---

## 本体チャットでのマージ時の注意

- **マージが必要な共有ファイルは `extract_segwari3.py` の1件のみ**。他ブランチが
  この同一ファイルを触っていなければ単純コピーで済む。engine/naming/registry は無改修。
- 新規 `adapter_segwari3.py` / `run_segwari3.py` はそのまま追加。
- `run_segwari3.py` は `run_kyoten4.py` の `_write_csv` / `_global_account_rows` /
  `_dedup_concepts` を import している（依存あり）。
- 統合DB再構築時、あしたかは type1〜4 を1法人として束ねる必要がある（本ブランチでは
  type3 のみ。type4 とは corp_no が同一なので、本体マージ時に1つの Ingested に統合するか、
  build の append 機能でまとめること）。
