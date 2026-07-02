# -*- coding: utf-8 -*-
"""
shafuku_db_engine: 社会福祉法人 決算情報 統合DB 構築・検算エンジン。

ローカルでの基本フロー:
  1) corps/<法人>.py に構造化データ(CorpData)を用意（kanagawa.py をテンプレに）
  2) validate.validate(corp) で全恒等式・様式間突合・計算書またぎを検算（不一致0を確認）
  3) ingest.ingest(corp) で fact + dim 行を生成
  4) build.build([...], out_path[, append_to=既存DB]) で Excel 出力＋FK/重複の事後検査

CLI:
  python -m shafuku_db_engine ingest corps.kanagawa --out 決算情報データベース.xlsx
  python -m shafuku_db_engine ingest corps.kanagawa --append 既存DB.xlsx --out 既存DB.xlsx
"""
from .ingest import CorpData, ingest, Ingested
from .validate import validate
from .build import build

__all__ = ["CorpData", "ingest", "Ingested", "validate", "build"]
__version__ = "1.0.0"
