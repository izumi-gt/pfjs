# -*- coding: utf-8 -*-
"""
shafuku_parser: 社会福祉法人 決算PDF 自動パーサ（座標ベース＋動的検出＋改ページ対応）。

進捗:
  layout / hierarchy / naming / registry : コア（完成）
  extract_houjin (タイプ1: 1-1/2-1/3-1)  : 完成・検証済
  extract_segwari (タイプ2: 1-2/2-2/3-2) : 完成・2-2を5社で検証済（金額52/52一致・科目名クリーン）
  extract_segwari3 (タイプ3: 1-3/2-3/3-3): 完成・神奈川(3拠点)＋あしたか(11拠点/多ページ/社福+公益)で検証済（全額一致220/220・多区分/列可変/depth対応）
  extract_kyoten4 (タイプ4: 1-4/2-4/3-4) : 完成・あしたか(11拠点/CF・PL多ページ縦連続/BS左右2ブロック)で検証済
                                            （BS残高一致11/11・CF/PL内部演算一致33/33・全総計一致・depth付与）

詳細・残作業・設計確定事項は同梱の HANDOFF.md を参照。
依存: pip install pdfplumber
"""
from . import layout, hierarchy, naming, registry
from . import extract_houjin, extract_segwari, extract_segwari3, extract_kyoten4

__all__ = ["layout", "hierarchy", "naming", "registry",
           "extract_houjin", "extract_segwari", "extract_segwari3", "extract_kyoten4"]
