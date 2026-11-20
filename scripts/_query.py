"""검색용 질의 다듬기 — 사용자 용어를 법령 용어와 병기한다. 정본 사전 = data/query-terms.yaml.

런타임(radsafety-lawbot src/lib/query.ts)과 같은 규칙이어야 한다. 한쪽을 고치면 다른 쪽도 고친다.
생성 모델에는 원문 질문을 넘기고, 이 함수의 결과는 임베딩에만 쓴다.
"""
import os
import yaml

TERMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'query-terms.yaml')
_cache = None


def _terms():
    global _cache
    if _cache is None:
        _cache = (yaml.safe_load(open(TERMS, encoding='utf-8')) or {}).get('terms', [])
    return _cache


def expand(question):
    """'방사선사 면허는…' → '방사선사(의료기사) 면허는…'. 해당 없으면 원문 그대로."""
    q = question
    for t in _terms():
        term, legal = t['term'], t['legal']
        if term in q and legal not in q:
            i = q.index(term) + len(term)
            q = f'{q[:i]}({legal}){q[i:]}'
    return q
