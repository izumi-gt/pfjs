# -*- coding: utf-8 -*-
"""
階層(depth)検出。固定閾値を使わず、その様式に出現した行頭x0を
クラスタリングして「浅い順に depth 0,1,2,...」を割り当てる。
法人ごとにインデントのx座標が違っても追従できる。
"""


def build_indent_levels(x0_list, gap=2.5):
    """
    出現した行頭x0のリストから、代表クラスタ(昇順)を作る。
    戻り: ソート済みクラスタ中心のリスト [c0, c1, c2, ...]（c0が最も浅い=depth0）
    """
    xs = sorted(x0_list)
    if not xs:
        return []
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= gap:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    centers = [sum(c) / len(c) for c in clusters]
    return centers


def level_of(x0, centers):
    """x0 を最も近いクラスタ中心に割り当て、そのindex(=depth)を返す。"""
    if not centers:
        return 0
    best_i, best_d = 0, 1e9
    for i, c in enumerate(centers):
        d = abs(c - x0)
        if d < best_d:
            best_d, best_i = d, i
    return best_i
