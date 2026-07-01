"""様式別の抽出層(reader)。様式ごとにPDF形式が異なるため、様式ごとに実装する。

各readerは「横書き科目名の抽出(page/top/text)」と「金額列の抽出・紐付け」までを担当し、
実際のcode照合は matching.matcher に委譲する。
"""
