# -*- coding: utf-8 -*-
"""
検算エンジン。投入前の構造化データ(CorpData)に対し、会計恒等式・様式間突合・
計算書またぎ整合を全件自己検証する。1件でも不一致があれば取り込みを止める運用。

ここで検出できる主な転記ミス:
 - 縦の合計/差額の不整合（小区分和≠計、収支差額式の崩れ）
 - 2階層の小区分和≠親（明細の親子不整合）
 - 様式間の不一致（1-4→1-3→1-2→1-1 等）
 - 貸借バランス崩れ、当年度末−前年度末≠増減
 - 計算書またぎ（BS当期活動増減差額≠PL当期活動増減差額 等）
"""

V = lambda x: x or 0


def _g_houjin(data, code, mi):
    """法人単位様式: code -> tuple、mi番目のmetric値。"""
    t = data.get(code)
    return None if t is None else t[mi]


def validate(corp):
    """CorpData を検算。問題のリストを返す（空ならOK）。"""
    e = []
    cf, pl, bs = corp.cf, corp.pl, corp.bs

    # ===================== CF =====================
    if "1-1" in cf:
        d = lambda c: V(_g_houjin(cf["1-1"], c, 1))  # 決算
        b = lambda c: V(_g_houjin(cf["1-1"], c, 0))  # 予算
        for col, g in [("決算", d), ("予算", b)]:
            chk = g("CF-A-NET") == g("CF-A-INC-T") - g("CF-A-EXP-T")
            if not chk: e.append(f"CF 1-1[{col}] 事業活動差額")
            if g("CF-B-INC-T") - g("CF-B-EXP-T") != g("CF-B-NET"): e.append(f"CF 1-1[{col}] 整備差額")
            if g("CF-C-INC-T") - g("CF-C-EXP-T") != g("CF-C-NET"): e.append(f"CF 1-1[{col}] その他差額")
            tot = g("CF-A-NET") + g("CF-B-NET") + g("CF-C-NET") - g("CF-X-RSV")
            if tot != g("CF-X-TOT"): e.append(f"CF 1-1[{col}] 当期収支差額合計")
            if g("CF-X-BEG") + g("CF-X-TOT") != g("CF-X-END"): e.append(f"CF 1-1[{col}] 当期末残高")

    _segwari(e, cf.get("1-2"), corp, "CF 1-2", _cf_balance)
    _kyoten(e, cf.get("1-3"), corp, "CF 1-3", cf.get("1-2"), seg_col=0)
    _meisai(e, cf.get("1-4"), corp, "CF 1-4", cf.get("1-3"), _cf_balance_stem)

    # ===================== PL =====================
    _houjin_pl(e, pl.get("2-1"), "PL 2-1", mi=0)
    _segwari(e, pl.get("2-2"), corp, "PL 2-2", _pl_balance)
    _kyoten(e, pl.get("2-3"), corp, "PL 2-3", pl.get("2-2"), seg_col=0, identities=_pl_balance_loc)
    _meisai(e, pl.get("2-4"), corp, "PL 2-4", pl.get("2-3"), _pl_balance_stem)

    # ===================== BS =====================
    _houjin_bs(e, bs.get("3-1"), "BS 3-1")
    _segwari(e, bs.get("3-2"), corp, "BS 3-2", _bs_balance, also_corp_eq=bs.get("3-1"))
    _kyoten(e, bs.get("3-3"), corp, "BS 3-3", bs.get("3-2"), seg_col=0, identities=_bs_balance_loc)
    _meisai_flat(e, bs.get("3-4"), corp, "BS 3-4", bs.get("3-3"), _bs_balance_flat)

    # ============== 計算書またぎ（BS↔PL）==============
    if "3-1" in bs and "2-1" in pl:
        if V(_g_houjin(bs["3-1"], "BS-N-031", 0)) != V(_g_houjin(pl["2-1"], "PL-K-CY", 0)):
            e.append("BS↔PL 当期活動増減差額 不一致")
        if V(_g_houjin(bs["3-1"], "BS-N-030", 0)) != V(_g_houjin(pl["2-1"], "PL-K-NEXT", 0)):
            e.append("BS↔PL 次期繰越活動増減差額 不一致")

    return e


# ---------- CF balance helpers ----------
def _cf_balance(e, g, tag):
    if V(g("CF-A-INC-T")) - V(g("CF-A-EXP-T")) != V(g("CF-A-NET")): e.append(f"{tag} 事業活動差額")
    if V(g("CF-X-BEG")) + V(g("CF-X-TOT")) != V(g("CF-X-END")): e.append(f"{tag} 当期末残高")

def _cf_balance_stem(e, stem, tag):
    g = lambda c: V(stem.get(c))
    if g("CF-A-INC-T") - g("CF-A-EXP-T") != g("CF-A-NET"): e.append(f"{tag} 事業活動差額")
    if g("CF-B-INC-T") - g("CF-B-EXP-T") != g("CF-B-NET"): e.append(f"{tag} 整備差額")
    if g("CF-C-INC-T") - g("CF-C-EXP-T") != g("CF-C-NET"): e.append(f"{tag} その他差額")
    if g("CF-A-NET") + g("CF-B-NET") + g("CF-C-NET") - g("CF-X-RSV") != g("CF-X-TOT"): e.append(f"{tag} 当期収支合計")
    if g("CF-X-BEG") + g("CF-X-TOT") != g("CF-X-END"): e.append(f"{tag} 当期末残高")


# ---------- PL balance helpers ----------
def _pl_chain(e, g, tag):
    if V(g("PL-S-REV-T")) - V(g("PL-S-EXP-T")) != V(g("PL-S-NET")): e.append(f"{tag} S差額")
    if V(g("PL-SO-REV-T")) - V(g("PL-SO-EXP-T")) != V(g("PL-SO-NET")): e.append(f"{tag} SO差額")
    if V(g("PL-S-NET")) + V(g("PL-SO-NET")) != V(g("PL-KEI-NET")): e.append(f"{tag} 経常")
    if V(g("PL-T-REV-T")) - V(g("PL-T-EXP-T")) != V(g("PL-T-NET")): e.append(f"{tag} 特別差額")
    if V(g("PL-KEI-NET")) + V(g("PL-T-NET")) != V(g("PL-K-CY")): e.append(f"{tag} 当期活動")
    if V(g("PL-K-CY")) + V(g("PL-K-BF")) != V(g("PL-K-END")): e.append(f"{tag} 当期末繰越")
    if V(g("PL-K-END")) + V(g("PL-K-KT")) + V(g("PL-K-AT")) - V(g("PL-K-AA")) != V(g("PL-K-NEXT")): e.append(f"{tag} 次期繰越")

def _pl_balance(e, g, tag): _pl_chain(e, g, tag)
def _pl_balance_loc(e, g, tag): _pl_chain(e, g, tag)
def _pl_balance_stem(e, stem, tag): _pl_chain(e, lambda c: stem.get(c), tag)

def _houjin_pl(e, data, tag, mi=0):
    if not data: return
    _pl_chain(e, lambda c: _g_houjin(data, c, mi), tag)


# ---------- BS balance helpers ----------
def _bs_one(e, g, tag):
    if V(g("BS-A-T")) != V(g("BS-LN-T")): e.append(f"{tag} 資産=負債純資産")
    if V(g("BS-L-T")) + V(g("BS-N-T")) != V(g("BS-LN-T")): e.append(f"{tag} 負債+純資産")

def _bs_balance(e, g, tag): _bs_one(e, g, tag)
def _bs_balance_loc(e, g, tag): _bs_one(e, g, tag)
def _bs_balance_stem(e, stem, tag): _bs_one(e, lambda c: stem.get(c), tag)
def _bs_balance_flat(e, d, tag):
    g = lambda c: (d.get(c) or (None,))[0]
    _bs_one(e, g, tag)
    cy, py, dl = d.get("BS-A-T", (None, None, None))
    if None not in (cy, py, dl) and V(cy) - V(py) != V(dl): e.append(f"{tag} 資産増減")

def _houjin_bs(e, data, tag):
    if not data: return
    g = lambda c: _g_houjin(data, c, 0)
    _bs_one(e, g, tag)
    for code, t in data.items():
        if len(t) == 3 and None not in t:
            if V(t[0]) - V(t[1]) != V(t[2]): e.append(f"{tag} {code} 増減")


# ---------- 汎用: 事業区分別 (1-2/2-2/3-2) ----------
def _segwari(e, data, corp, tag, identities, also_corp_eq=None):
    if not data: return
    n = len(corp.seg2_order)
    for i, segnm in enumerate(corp.seg2_order):
        g = lambda c, i=i: (data[c][i] if c in data else None)
        identities(e, g, f"{tag}[{segnm}]")
    for code, vals in data.items():
        sw, pub, rev, tot, elim, comp = vals
        if None not in (sw, pub, rev, tot) and V(sw) + V(pub) + V(rev) != V(tot):
            e.append(f"{tag} {code} 区分和")
        if None not in (tot, comp) and V(tot) - V(elim) != V(comp):
            e.append(f"{tag} {code} 法人合計")
    if also_corp_eq is not None:
        for code in data:
            if code in also_corp_eq:
                if V(data[code][5]) != V(_g_houjin(also_corp_eq, code, 0)):
                    e.append(f"{tag}↔法人単位 {code}")


# ---------- 汎用: 拠点別 (1-3/2-3/3-3) ----------
def _kyoten(e, data, corp, tag, segwari_data, seg_col=0, identities=None):
    if not data: return
    locs_order = corp.kyoten_loc.get(_form_of(tag), corp.loc_order)
    for code, (locs, total, elim, segtot) in data.items():
        s = sum(V(x) for x in locs)
        if V(total) != s and total is not None: e.append(f"{tag} {code} Σ拠点≠合計")
        base = V(total) if total is not None else s
        if base - V(elim) != V(segtot): e.append(f"{tag} {code} 合計-消去≠区分計")
        if segwari_data is not None and code in segwari_data:
            if V(segtot) != V(segwari_data[code][seg_col]): e.append(f"{tag}↔区分別 {code}")
    if identities is not None:
        for i, loc in enumerate(locs_order):
            g = lambda c, i=i: (data[c][0][i] if c in data else None)
            identities(e, g, f"{tag}[{loc}]")
    else:
        # BS/CF default balance per loc if applicable handled by identities; skip otherwise
        pass


# ---------- 汎用: 拠点明細 (1-4/2-4) ----------
def _meisai(e, source, corp, tag, kyoten_data, stem_identities):
    if not source: return
    form = _form_of(tag)
    primary = 1 if form.startswith("CF") else 0   # CF:決算=idx1 / PL:当年度決算=idx0
    pcol = 2 + primary
    locs_order = corp.kyoten_loc.get(form, corp.loc_order)
    loc_idx = {l: i for i, l in enumerate(locs_order)}
    for loc, rows in source.items():
        # 親子整合の検算を文書順で行う（同名 depth1/depth2 や、depth2子が親と同名の
        # ケースに頑健。名前@loc キーは同一拠点内で衝突し得るため使わない）。
        #   - depth1群: 同一 stem 配下の depth1 値の和 == stem 値
        #   - depth2群: 同一 depth1 配下の depth2 値の和 == その depth1 値
        stem = {}                      # stem_code -> 値
        stem_d1_sum = {}               # stem_code -> 配下 depth1 値の和
        _stem_has_d1 = {}              # stem_code -> 配下に depth1 があるか
        cur_stem = None
        cur_d1_val = None              # 直近 depth1 の値
        cur_d1_name = None
        cur_d2_sum = None              # 直近 depth1 配下の depth2 和
        cur_d1_has_d2 = False

        def _close_d1():
            # 直近 depth1 の孫和検算を確定
            if cur_d1_name is not None and cur_d1_has_d2:
                if cur_d2_sum != V(cur_d1_val):
                    e.append(f"{tag}[{loc}] {cur_d1_name}@{loc} 孫和≠親")

        for row in rows:
            d = row[0]
            val = row[pcol]
            if d == 0:
                _close_d1()
                cur_d1_name = None; cur_d1_has_d2 = False
                cur_stem = row[1]
                stem[cur_stem] = val
                stem_d1_sum.setdefault(cur_stem, 0)
                _stem_has_d1.setdefault(cur_stem, False)
            elif d == 1:
                _close_d1()
                if cur_stem is not None:
                    stem_d1_sum[cur_stem] = stem_d1_sum.get(cur_stem, 0) + V(val)
                    _stem_has_d1[cur_stem] = True
                cur_d1_name = row[1]
                cur_d1_val = val
                cur_d2_sum = 0
                cur_d1_has_d2 = False
            else:  # depth2
                cur_d2_sum = (cur_d2_sum or 0) + V(val)
                cur_d1_has_d2 = True
        _close_d1()

        # depth1群 == stem の検算（stem 配下に depth1 がある場合のみ。元実装と同義）
        for sc, dsum in stem_d1_sum.items():
            if sc in stem and _stem_has_d1[sc]:
                if dsum != V(stem[sc]):
                    e.append(f"{tag}[{loc}] {sc} 小区分和≠親")

        stem_identities(e, stem, f"{tag}[{loc}]")
        if kyoten_data is not None and loc in loc_idx:
            li = loc_idx[loc]
            for code in stem:
                if code in kyoten_data:
                    if V(stem[code]) != V(kyoten_data[code][0][li]):
                        e.append(f"{tag}↔拠点別 [{loc}] {code}")


# ---------- 汎用: 拠点明細フラット (3-4) ----------
def _meisai_flat(e, source, corp, tag, kyoten_data, balance_flat):
    if not source: return
    locs_order = corp.kyoten_loc.get(_form_of(tag), corp.loc_order)
    loc_idx = {l: i for i, l in enumerate(locs_order)}
    for loc, d in source.items():
        balance_flat(e, d, f"{tag}[{loc}]")
        if kyoten_data is not None and loc in loc_idx:
            li = loc_idx[loc]
            for code, t in d.items():
                if code in kyoten_data:
                    if V(t[0]) != V(kyoten_data[code][0][li]):
                        e.append(f"{tag}↔拠点別 [{loc}] {code}")


def _form_of(tag):
    # "CF 1-3" -> "CF-1-3"
    parts = tag.split()
    return parts[0] + "-" + parts[1] if len(parts) >= 2 else tag
