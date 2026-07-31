#!/usr/bin/env python3
"""BM25 어휘 검색 — 벡터 검색과 융합(RRF)해 하이브리드로 쓰기 위한 최소 구현(2026-08-01 신설).

**왜**: 2026-08-01 triage 에서 검색 실패 5건이 전부 *법률 본문* 이었다(별표·고시는 전부 성공).
법률 조문은 문체가 추상적이라 임베딩이 질문과 잘 안 붙는데, 정작 그 문항들은 어휘가 잘 겹친다
— "방사선사의 업무 범위"↔의료기사법 제2조, "면허는 누가 부여"↔제4조. 어휘 신호를 따로 세면
벡터가 놓친 자리를 메울 수 있다는 가설이고, 이 모듈은 그 가설의 측정 도구다.

**임베딩 제공자와 독립**이라 Voyage/OpenAI 로 바꿔도 살아남고, Supabase pgvector 서빙에는
tsvector 로 그대로 이식된다.

한국어 토크나이저: 조사·어미가 붙어 어절 단위 일치가 깨지므로(방사선사↔방사선사의)
한글 구간은 **문자 2·3-gram** 으로 쪼갠다. 핵종·영문·숫자(I-131, Tc-99m, 0.58)는 원형 유지 —
이쪽은 정확 일치가 곧 신호다.
"""
import math
import re
from collections import Counter, defaultdict

# I-131 / Tc-99m 같은 핵종 토큰을 통째로 살린 뒤 영문·숫자·한글 순으로 집는다.
_TOK = re.compile(r'[A-Za-z]+-\d+[A-Za-z]*|[A-Za-z]{2,}|\d+(?:\.\d+)?|[가-힣]+')


def tokenize(text):
    out = []
    for m in _TOK.finditer(text or ''):
        w = m.group(0)
        if '가' <= w[0] <= '힣':          # 한글 구간 → 2·3-gram
            if len(w) <= 2:
                out.append(w)
                continue
            out += [w[i:i + 2] for i in range(len(w) - 1)]
            out += [w[i:i + 3] for i in range(len(w) - 2)]
        else:
            out.append(w.lower())
    return out


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.N = len(docs)
        self.post = defaultdict(list)             # token → [(doc, tf), ...]
        self.len = [0] * self.N
        for i, d in enumerate(docs):
            tf = Counter(tokenize(d))
            self.len[i] = sum(tf.values()) or 1
            for t, c in tf.items():
                self.post[t].append((i, c))
        self.avg = sum(self.len) / max(self.N, 1)
        self.idf = {t: math.log(1 + (self.N - len(p) + 0.5) / (len(p) + 0.5))
                    for t, p in self.post.items()}

    def scores(self, query):
        """질의 → 문서별 BM25 점수 리스트(길이 N)."""
        s = [0.0] * self.N
        for t, qc in Counter(tokenize(query)).items():
            p = self.post.get(t)
            if not p:
                continue
            idf = self.idf[t]
            for i, c in p:
                denom = c + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)
                s[i] += idf * (c * (self.k1 + 1)) / denom
        return s


def rrf(*rankings, k=60, weights=None):
    """Reciprocal Rank Fusion — 점수 스케일이 다른 랭킹을 순위만으로 합친다.
    벡터 유사도와 BM25 점수는 단위가 달라 가중합이 임의적이 된다. RRF 는 그 임의성을 피한다.

    weights 로 랭킹별 비중을 준다. **동일 가중은 이 코퍼스에서 해롭다**(2026-08-01 실측):
    어휘가 못 맞히는 문항에서 강한 벡터 순위까지 끌어내려 @5 가 84%→75% 로 떨어졌다.
    어휘는 벡터가 놓친 자리를 *보조* 하는 비중(<1)일 때만 이득이 남는다."""
    w = weights or [1.0] * len(rankings)
    score = defaultdict(float)
    for wi, order in zip(w, rankings):
        for rank, doc in enumerate(order):
            score[doc] += wi / (k + rank + 1)
    return [d for d, _ in sorted(score.items(), key=lambda kv: -kv[1])]
