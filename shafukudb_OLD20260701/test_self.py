# -*- coding: utf-8 -*-
"""
正典自己テスト。

照合エンジンの正しさを「抽出品質と独立に」検証する。
正典の各行を、実PDF抽出行に見立てた ExtractedLine に変換し:
  (A) 親パスを与えた場合 -> 元の code に厳密に戻ること(完全一意化)を確認。
  (B) 親パスを与えない場合 -> 同名重複がどれだけ AMBIGUOUS になるかを測定し、
      「親パスが照合に必須」であることを定量的に示す。
"""
from seiten import Seiten
from matcher import Matcher, ExtractedLine


def run():
    s = Seiten.load("seiten_master.csv")
    m = Matcher(s)

    # 各正典行を抽出行に見立てる。様式は便宜上 stmt 由来のダミー。
    lines_with_path = []
    lines_without_path = []
    for r in s.records:
        lines_with_path.append(
            ExtractedLine(r.stmt, "self", r.正規名, parent_path=r.parent_path(), amount=0)
        )
        lines_without_path.append(
            ExtractedLine(r.stmt, "self", r.正規名, parent_path=None, amount=0)
        )

    # (A) 親パスあり
    res_a = m.match_all(lines_with_path)
    a_hit = sum(1 for x in res_a if x.matched)
    a_correct = sum(1 for x, r in zip(res_a, s.records) if x.matched and x.code == r.code)
    a_wrong = sum(1 for x, r in zip(res_a, s.records) if x.matched and x.code != r.code)
    a_miss = sum(1 for x in res_a if not x.matched)

    # (B) 親パスなし
    res_b = m.match_all(lines_without_path)
    b_hit = sum(1 for x in res_b if x.matched)
    b_correct = sum(1 for x, r in zip(res_b, s.records) if x.matched and x.code == r.code)
    b_amb = sum(1 for x in res_b if x.reason == "AMBIGUOUS")
    b_noname = sum(1 for x in res_b if x.reason == "NO_NAME")

    print("=== (A) 親パスあり ===")
    print(f"  総数 {len(res_a)} / ヒット {a_hit} / うち正解code {a_correct} / 誤code {a_wrong} / 未ヒット {a_miss}")
    print("=== (B) 親パスなし ===")
    print(f"  総数 {len(res_b)} / ヒット {b_hit} / 正解code {b_correct} / 曖昧退避 {b_amb} / 該当名なし {b_noname}")

    print("\n=== 判定 ===")
    ok_a = (a_correct == len(s.records) and a_wrong == 0 and a_miss == 0)
    print(f"  (A)完全一意化(全{len(s.records)}行が元codeへ): {'PASS' if ok_a else 'FAIL'}")
    print(f"  (B)親パス無しでは {b_amb} 行が曖昧化 -> 親パスが照合に必須であることを実証")
    return ok_a


if __name__ == "__main__":
    ok = run()
    raise SystemExit(0 if ok else 1)
