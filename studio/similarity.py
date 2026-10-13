"""similarity — 제안의 ‘수렴／변주／이상치’를 규칙으로 분류한다（LLM에게 판정시키지 않는다）.

Novel_4 검증에서 알게 된 것：
  - AI 심판은 중복 판정이 흔들린다（ARI 0.115~1.0）. 중복은 규칙으로 판정한다.
  - 기획은 유형으로 수렴한다（미스터리 5/5가 ‘화자가 가해자’, SF 4/5가 ‘AI가 관리하는 냉동 수면 우주선’）.
  - 수렴하는 것은 겉으로 드러난 낱말보다 ‘골격’（누가 속이나·무엇을 숨기나·어느 층에서 놀라게 하나·무엇이 뒤집히나）.
그래서 LLM에게는 골격 추출만 시키고（싼 모델로 충분）, 비슷한지 아닌지는
‘본문의 글자 2-gram（한국어는 한글 음절 단위）의 가까움’과 ‘골격 항목별 가까움’을 섞은 점수로 평균 연결 군집화에 건다.
문턱값은 화면에서 움직일 수 있고, LLM을 다시 부르지 않고도 다시 분류할 수 있다.
"""
import math
import re
from collections import Counter

SK_FIELDS = ["deceiver", "hidden", "layer", "flip", "stage", "ending"]
TEXT_FIELDS = ["title", "setting", "synopsis", "highlight"]


def norm(s):
    return re.sub(r"[\s　「」『』（）()、。・…―－\-!！?？\"'＊*#:：“”‘’,.·《》]", "", str(s or ""))


def grams(s, n=2):
    s = norm(s)
    return Counter(s[i:i + n] for i in range(max(0, len(s) - n + 1)))


def cos(a, b):
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))


def field_sim(x, y):
    x, y = norm(x), norm(y)
    if not x or not y:
        return 0.0
    if x == y or x in y or y in x:
        return 1.0
    return cos(grams(x, 1), grams(y, 1)) * 0.5 + cos(grams(x), grams(y)) * 0.5


def pair_score(p, q, sp, sq, w_text=0.4):
    t = cos(grams(" ".join(str(p.get(f, "")) for f in TEXT_FIELDS)), grams(" ".join(str(q.get(f, "")) for f in TEXT_FIELDS)))
    if sp and sq:
        k = sum(field_sim(sp.get(f), sq.get(f)) for f in SK_FIELDS) / len(SK_FIELDS)
        return w_text * t + (1 - w_text) * k, t, k
    return t, t, None


def classify(props, skeletons, threshold=0.5, w_text=0.4):
    """props: [{id, ...}], skeletons: {id: {...}}. 반환값：{labels:{id:{label,cluster,nearest,sim}}, clusters:[...], matrix:{}}"""
    ids = [p["id"] for p in props]
    P = {p["id"]: p for p in props}
    n = len(ids)
    S = {}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ids[i], ids[j]
            s, t, k = pair_score(P[a], P[b], skeletons.get(a), skeletons.get(b), w_text)
            S[(a, b)] = S[(b, a)] = round(s, 3)
    # 평균 연결 방식의 병합형 군집화
    clusters = [[x] for x in ids]

    def link(c1, c2):
        return sum(S[(a, b)] for a in c1 for b in c2) / (len(c1) * len(c2))
    while len(clusters) > 1:
        best, bi, bj = -1, -1, -1
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                v = link(clusters[i], clusters[j])
                if v > best:
                    best, bi, bj = v, i, j
        if best < threshold:
            break
        clusters[bi] = clusters[bi] + clusters[bj]
        del clusters[bj]
    clusters.sort(key=len, reverse=True)
    big = max(2, math.ceil(n * 0.3))
    labels, out_clusters = {}, []
    for ci, c in enumerate(clusters):
        if len(c) >= big:
            lab = "수렴"
        elif len(c) >= 2:
            lab = "변주"
        else:
            lab = None
        sk = [skeletons.get(x) or {} for x in c]
        common = {}
        for f in SK_FIELDS:
            vals = Counter(norm(s.get(f)) for s in sk if s.get(f))
            if vals:
                v, cnt = vals.most_common(1)[0]
                if cnt >= max(2, len(c) // 2 + 1) or len(c) == 1:
                    common[f] = next(s.get(f) for s in sk if norm(s.get(f)) == v)
        type_line = "／".join(f"{v}" for v in common.values()) or ""
        cohesion = round(sum(S[(a, b)] for a in c for b in c if a != b) / max(1, len(c) * (len(c) - 1)), 3) if len(c) > 1 else None
        out_clusters.append({"id": f"C{ci + 1}", "members": c, "label": lab, "type": type_line, "cohesion": cohesion,
                             "one_lines": [(skeletons.get(x) or {}).get("one_line", "") for x in c]})
        for x in c:
            others = [(S[(x, y)], y) for y in ids if y != x]
            sim, near = max(others) if others else (0.0, None)
            if lab is None:
                lab_x = "이상치" if sim < threshold * 0.8 else "변주"
            else:
                lab_x = lab
            labels[x] = {"label": lab_x, "cluster": f"C{ci + 1}", "nearest": near, "sim": sim}
        if lab is None:
            out_clusters[-1]["label"] = labels[c[0]]["label"]
    pairs = sorted(((S[(a, b)], a, b) for i, a in enumerate(ids) for b in ids[i + 1:]), reverse=True)
    mean = round(sum(s for s, _, _ in pairs) / len(pairs), 3) if pairs else None
    return {"labels": labels, "clusters": out_clusters, "threshold": threshold, "w_text": w_text,
            "mean_similarity": mean, "top_pairs": [{"a": a, "b": b, "sim": s} for s, a, b in pairs[:8]]}
