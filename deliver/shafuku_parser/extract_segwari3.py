# -*- coding: utf-8 -*-
"""
様式タイプ3: 拠点別内訳表（1-3 / 2-3 / 3-3）。多ページ・多事業区分対応版。

タイプ2(事業区分別)との違い:
  - 列が法人別に可変。先頭列が「社会福祉法人○○本部拠点」(法人名融合・折返し)、
    続いて各拠点の「○○拠点区分」、末尾に 合計 / 内部取引消去 / 事業区分合計(計)。
  - 列ヘッダは左寄せラベルなので、値列の右端アンカーは「数値の右端x1クラスタ」から
    動的に検出する（ヘッダ位置からは取らない）。列数=拠点数+3（区分により可変）。
  - 1法人の1様式が複数の「事業区分ブロック」(社会福祉/公益/収益)に分かれ、各区分が
    さらに横分割(列がページ幅に収まらず右側列を次ページへ)・縦分割(行が次ページへ)で
    複数ページに跨ることがある。これらを統合する。
  - 3-3(BS)は小区分のインデント階層(全角字下げ)を持つ → depthを付与。
  - 1-3/2-3 はタイプ2と同じ縦書きマージン文字を持つ → タイプ2のクリーナを再利用。

戻り値:
  extract_segwari3(pdf) -> [ {kubun, colnames, rows}, ... ] 事業区分ごと
    kubun: '社会福祉事業区分' / '公益事業区分' / '収益事業区分' / None
    colnames: 列名リスト（index順）。例
      ['本部拠点','明神台保育園','合歓の木保育園','合計','内部取引消去','事業区分合計']
    rows: [(name, depth, {col_index: value}), ...]
      depth: インデント階層（0=大区分, 1=小区分…）。1-3/2-3は基本0、3-3でBS小区分が1。
      col_index 0..N-1 が colnames に対応。値の無い列はdictに現れない。

  神奈川厚生福祉会(3拠点・1ページ・社福のみ)と あしたか太陽の丘(11拠点・3〜4ページ・
  社福＋公益・横分割＋縦分割・折返し拠点名) の両方で全額一致を確認済み。

## ページ統合のロジック（このチャットで確定）
  - 各ページを独立に解析（kubun, 値列アンカー, 列名, 行）。
  - 連続ページを kubun でブロック化（バナー無しページは直前 kubun を継承）。
  - ブロック内でページを統合:
      * 自前の列ヘッダを持つページ = 新しい列群（横分割 or 新区分の先頭）→ 列を追加。
      * 列ヘッダを持たないページ = 縦連続 → 既存列群へ行を追記（列は増やさない）。
        アンカーが一部欠ける(空列)場合もサブセットとして最近傍へ対応付け。
      * 行は row top をキーに統合（横分割は同一 top の別列群を結合）。

改ページ・折返し拠点名・空列・公益区分の別レイアウトすべてに対応。
"""
import re
from collections import defaultdict, Counter
from . import layout, extract_segwari as _seg

DIGIT=set("0123456789,，△▲-−")
HALFDIG=set("0123456789,-")
def is_dig(t): return t in DIGIT or t.isdigit()

KUBUN_WORDS=("社会福祉事業区分","公益事業区分","収益事業区分")
TAIL_WORDS=("合計","内部取引消去","事業区分合計","事業区分計")

def build_numbers(chars,xmin):
    rows=defaultdict(list)
    for c in sorted([c for c in chars if is_dig(c["text"]) and c["x0"]>xmin],key=lambda c:(round(c["top"],1),c["x0"])):
        for k in list(rows):
            if abs(k-c["top"])<=2.0: rows[k].append(c); break
        else: rows[c["top"]].append(c)
    nums=[]
    for top,cs in rows.items():
        cs.sort(key=lambda c:c["x0"]); i=0
        while i<len(cs):
            j=i; run=[cs[i]]
            while j+1<len(cs) and (cs[j+1]["x0"]-cs[j]["x1"])<cs[j]["width"]*1.6: j+=1; run.append(cs[j])
            t="".join(c["text"] for c in run)
            if layout.to_int(t) is not None:
                nums.append({"text":t,"x0":run[0]["x0"],"x1":run[-1]["x1"],"top":top})
            i=j+1
    return nums

def cluster1d(vals,tol):
    vals=sorted(vals); cl=[]
    for v in vals:
        if cl and v-cl[-1][-1]<=tol: cl[-1].append(v)
        else: cl.append([v])
    return cl

def detect_anchors(nums,min_members=5,tol=5):
    cl=cluster1d([n["x1"] for n in nums],tol)
    return [round(sum(c)/len(c),1) for c in cl if len(c)>=min_members]

def snap_rows(tops,tol=2.5):
    tops=sorted(tops); cl=[]
    for t in tops:
        if cl and t-cl[-1][-1]<=tol: cl[-1].append(t)
        else: cl.append([t])
    return [sum(c)/len(c) for c in cl]

def page_kubun(words):
    for w in words:
        if w["text"] in KUBUN_WORDS: return w["text"]
    return None

def header_locs(words, first_edge):
    """拠点ヘッダ名と末尾固定列名を、ヘッダ帯から左→右に返す [(x0,name,kind)]."""
    out=[]
    for w in words:
        if w["top"]>=132: continue
        t=w["text"]
        if "本部拠点" in t: out.append((w["x0"],"本部拠点","loc"))
        elif "拠点区分" in t: out.append((w["x0"],re.sub(r"拠点区分$","",t),"loc"))
        elif t in TAIL_WORDS: out.append((w["x0"],t,"tail"))
    out.sort()
    return out

def extract_page(pg):
    """1ページを解析: (kubun, anchors, colnames, rows_by_top)
       rows_by_top: {row_top: (name, depth, {anchor_index: value})}"""
    words=pg.extract_words(use_text_flow=False, keep_blank_chars=False)
    kubun=page_kubun(words)
    nums_all=build_numbers(pg.chars,140)
    anchors=detect_anchors(nums_all)
    if len(anchors)<1: return kubun,[],[],{}
    first_edge=min(anchors)
    nums=[x for x in nums_all if x["x0"]>first_edge-40]
    # データ行top: 同一topで>=3アンカー(列が少なければ>=1)に乗る行
    by_top=defaultdict(set)
    for n in nums:
        for ai,a in enumerate(anchors):
            if abs(n["x1"]-a)<=6: by_top[round(n["top"],1)].add(ai); break
    strong=[t for t,s in by_top.items() if len(s)>=3] or list(by_top.keys())
    name_top_min=(min(strong)-4) if strong else 0
    body_cand=[round(w["x0"]) for w in words if w["x0"]<first_edge-25 and len(w["text"])>=4 and w["x0"]>=38 and w["top"]>=name_top_min]
    body_edge=Counter(body_cand).most_common(1)[0][0] if body_cand else 54
    row_tops=snap_rows([x["top"] for x in nums],2.5)
    name_chars=defaultdict(list)
    for c in pg.chars:
        cx=(c["x0"]+c["x1"])/2
        if cx>=first_edge-20 or c["top"]<name_top_min or c["text"] in HALFDIG: continue
        if not row_tops: continue
        best=min(row_tops,key=lambda rt:abs(rt-c["top"]))
        if abs(best-c["top"])<=4.5: name_chars[round(best,1)].append(c)
    rows={}
    for rt in sorted(set(round(t,1) for t in row_tops)):
        kept=_seg._clean_name_chars(name_chars.get(rt,[]),body_edge)
        depth=0
        while kept and kept[0]["text"] in ("\u3000"," ","\t"): depth+=1; kept=kept[1:]
        name="".join(c["text"] for c in kept).strip()
        if not name or _seg._skip(name): 
            name=""  # keep row slot for value-only pages? no—skip
        rownums=[x for x in nums if abs(x["top"]-rt)<=4.5]
        assign=layout.assign_columns([{"text":x["text"],"x1":x["x1"]} for x in rownums],anchors)
        # 縦連続ページの再割当用に、行の生数値(text,x1)も保持する。
        rawnums=[{"text":x["text"],"x1":x["x1"]} for x in rownums]
        rows[round(rt,1)]={"name":name,"depth":depth,"assign":assign,"rawnums":rawnums}
    # column names: map header locs to anchors by nearest (anchors are right edges, names left-aligned)
    locs=header_locs(words,first_edge)
    return kubun,anchors,locs,rows

def header_names_full(words, n_anchors, anchors, hdr_lo, hdr_hi):
    """拠点ヘッダ帯(top hdr_lo..hdr_hi)の語を各値列アンカーへ x位置で割り当て連結。
       hdr_lo/hi はページごとに動的算出（タイトル下端〜最初のデータ行上）。"""
    anc=sorted(anchors)
    bounds=[]; prev=140.0
    for a in anc:
        bounds.append((prev, a+6)); prev=a
    hb=[w for w in words if hdr_lo <= w["top"] <= hdr_hi and w["x0"] > 140
        and not any(ch.isdigit() for ch in w["text"])
        and "単位" not in w["text"] and "令和" not in w["text"]
        and "様式" not in w["text"] and "内訳表" not in w["text"]
        and w["text"] not in KUBUN_WORDS]
    buckets={i:[] for i in range(len(anc))}
    for w in hb:
        cx=(w["x0"]+w["x1"])/2
        bi=None
        for i,(lo,hi) in enumerate(bounds):
            if lo <= cx <= hi: bi=i; break
        if bi is None:
            bi=min(range(len(anc)), key=lambda i: abs(anc[i]-w["x1"]))
        buckets[bi].append(w)
    names=[]
    for i in range(len(anc)):
        ws=sorted(buckets[i],key=lambda w:(round(w["top"],1),w["x0"]))
        nm="".join(w["text"] for w in ws)
        nm=re.sub(r"拠点区分$","",nm)
        # 先頭列の法人名融合("社会福祉法人○○本部拠点")を "本部拠点" に正規化
        if "本部拠点" in nm:
            nm="本部拠点"
        names.append(nm if nm else f"列{i}")
    order=sorted(range(len(anchors)), key=lambda i: anchors[i])
    out=[None]*len(anchors)
    for rank,orig in enumerate(order):
        out[orig]=names[rank]
    return out

def extract_type3_multi(pdf):
    """戻り: [ {kubun, colnames, rows:[(name,depth,{col:val})]} ] 事業区分ごと。"""
    pages=[]
    last_kubun=None
    for pg in pdf.pages:
        kubun,anchors,locs,rows=extract_page(pg)
        if kubun is None: kubun=last_kubun
        else: last_kubun=kubun
        words=pg.extract_words(use_text_flow=False,keep_blank_chars=False)
        # 動的ヘッダ帯: バナー/タイトル下端 〜 最初の「データ行」上。
        # データ行 = 値列アンカーに乗る数値のみ（日付の数字を除外するため）。
        anc_set=anchors
        def near_anchor(x1):
            return any(abs(x1-a)<=6 for a in anc_set)
        # データ行 = 同一topで >=3 個の値列アンカーに数値が乗る行（日付行は除外）。
        by_top=defaultdict(set)
        for n in build_numbers(pg.chars,140):
            for ai,a in enumerate(anc_set):
                if abs(n["x1"]-a)<=6:
                    by_top[round(n["top"],1)].add(ai); break
        data_tops=[t for t,s in by_top.items() if len(s)>=3]
        if not data_tops:  # 単一拠点等で列が少ない場合のフォールバック
            data_tops=[t for t,s in by_top.items() if len(s)>=1]
        first_data_top=min(data_tops) if data_tops else 140
        banner_bottoms=[w["bottom"] for w in words
                        if (w["text"] in KUBUN_WORDS or "様式" in w["text"]
                            or "令和" in w["text"] or "内訳表" in w["text"])
                        and w["bottom"] < first_data_top]
        hdr_lo=(max(banner_bottoms)+0.5) if banner_bottoms else 88
        hdr_hi=first_data_top-1.5
        full_names=header_names_full(words,len(anchors),anchors,hdr_lo,hdr_hi)
        pages.append({"kubun":kubun,"anchors":anchors,"locs":locs,"rows":rows,"names":full_names})
    # group consecutive pages by kubun
    blocks=[]
    for p in pages:
        if blocks and blocks[-1]["kubun"]==p["kubun"]:
            blocks[-1]["pages"].append(p)
        else:
            blocks.append({"kubun":p["kubun"],"pages":[p]})
    results=[]
    for blk in blocks:
        global_cols=[]            # 列名
        col_anchor_groups=[]      # 既出のアンカー集合（横分割の各グループ）-> base index
        merged={}                 # row_top -> {name,depth,assign{globalidx}}
        gidx=0

        def match_group(anchors):
            """既出グループのアンカー集合に各アンカーが対応付けば縦連続とみなす。
               一部列が空(数値なし)でアンカーが欠けても、サブセットなら許容。"""
            best=None
            for grp in col_anchor_groups:
                ga=sorted(grp["anchors"])
                ok=all(any(abs(x-g)<=6 for g in ga) for x in anchors)
                if ok and len(anchors)<=len(ga):
                    if best is None or abs(len(ga)-len(anchors))<abs(len(best["anchors"])-len(anchors)):
                        best=grp
            return best

        for p in blk["pages"]:
            if not p["anchors"]:
                continue
            # 縦連続の判定: このページが「自前の列ヘッダを持たない」場合のみ縦連続。
            # 横分割ページ(内部取引消去/事業区分合計 等)は自前ヘッダを持つので新列群。
            has_headers = any(nm and not nm.startswith("列") for nm in (p["names"] or []))
            grp = None if has_headers else match_group(p["anchors"])
            if grp is not None:
                # 縦連続: 既存列に行を追記（列は増やさない）。
                base=grp["base"]
                ga=grp["anchors"]
                # 縦分割(縦連続)ページの行は「新しい勘定科目」であり、メインページの行と
                # 同じ top 座標を再利用しているだけなので、top でマージしてはいけない
                # （衝突すると科目が入れ替わる）。継続ページの行 top に十分大きなオフセットを
                # 与え、メインページの行群の後ろへ追記する。
                # オフセット: これまでに出た最大 top + ページ順による加算。
                top_off = (max(merged.keys()) + 1000.0) if merged else 0.0
                # 縦連続ページは「自前の(欠けた)アンカー」ではなく、グループの全アンカーで
                # 再割当する。これにより、このページで数値が少なくアンカー検出から漏れた
                # 列(例: 3拠点目に値が1つしか無い継続ページ)の値も正しい列へ入る。
                # （アンカーが一致する既存corps/ページでは結果不変＝非破壊）。
                for rt,r in p["rows"].items():
                    key=round(rt + top_off, 1)
                    if key not in merged:
                        merged[key]={"name":r["name"],"depth":r["depth"],"assign":{}}
                    if r["name"] and not merged[key]["name"]:
                        merged[key]["name"]=r["name"]; merged[key]["depth"]=r["depth"]
                    reassigned=layout.assign_columns(r.get("rawnums",[]), ga)
                    for ai,v in reassigned.items():
                        merged[key]["assign"][base+ai]=v
                continue
            else:
                # 横分割（新しい列群）: 列を追加
                ncol=len(p["anchors"])
                names=p["names"] if p["names"] else [f"列{i}" for i in range(ncol)]
                pnames=names[:ncol]+[f"列{i}" for i in range(len(names),ncol)]
                base=gidx
                for ci in range(ncol):
                    global_cols.append(pnames[ci])
                col_anchor_groups.append({"anchors":list(p["anchors"]),"base":base})
                remap={i:i for i in range(ncol)}
                gidx+=ncol
            for rt,r in p["rows"].items():
                key=round(rt,1)
                if key not in merged:
                    merged[key]={"name":r["name"],"depth":r["depth"],"assign":{}}
                if r["name"] and not merged[key]["name"]:
                    merged[key]["name"]=r["name"]; merged[key]["depth"]=r["depth"]
                for ai,v in r["assign"].items():
                    merged[key]["assign"][base+remap[ai]]=v
        rows_out=[]
        for rt in sorted(merged):
            m=merged[rt]
            if not m["name"]: continue
            rows_out.append((m["name"],m["depth"],m["assign"]))
        results.append({"kubun":blk["kubun"],"colnames":global_cols,"rows":rows_out})
    return results


def extract_segwari3_blocks(pdf):
    """別名（明示的）。"""
    return extract_type3_multi(pdf)


def extract_segwari3(pdf, statement=None):
    """タイプ3 抽出のエントリポイント。事業区分ごとのブロックを返す。"""
    return extract_type3_multi(pdf)
