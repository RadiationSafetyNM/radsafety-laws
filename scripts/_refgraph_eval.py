#!/usr/bin/env python3
"""참조 그래프가 검색에 보태는 것을 잰다 (2026-11-27). 생성 호출 없음(임베딩·검색만).

문항마다 상위 k 조각 + 그 조각이 가리키는 조문(나가는 간선)·그 조각을 가리키는 조문(들어오는 간선)을
더했을 때 기대 출처가 잡히는지, 그리고 더해지는 조문 수(= 늘어나는 프롬프트)를 센다.
"""
import json, os, re, sys
from collections import defaultdict
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _embed import embed_query                       # noqa: E402
from _match import norm, match                       # noqa: E402
from _query import expand                            # noqa: E402
from _lawbot_verify import unit_of, rpc_match, EVAL, CORPUS_GAP, METRIC_EXCLUDE   # noqa: E402
from _build_refgraph import node_of, OUT             # noqa: E402

g = json.load(open(OUT, encoding='utf-8'))
rev = defaultdict(set)
for k, vs in g.items():
    for v in vs:
        rev[v].add(k)


def unit_of_node(n):
    law, x = n.split('#')
    b = re.match(r'별표(\d+)', x)
    return {'law': norm(law), 'art': '' if b else x, 'byeol': b.group(1) if b else ''}


def main():
    url, key = os.environ['SUPABASE_URL'].rstrip('/'), os.environ['SUPABASE_SERVICE_ROLE_KEY']
    qs = [q for q in yaml.safe_load(open(EVAL, encoding='utf-8'))['questions']
          if q['id'] not in CORPUS_GAP and q['id'] not in METRIC_EXCLUDE]
    cache = {q['id']: rpc_match(url, key, embed_query(expand(q['question'])), 10) for q in qs}
    print(f'{len(qs)}문항')
    for k in (1, 3, 5):
        base = out = both = 0
        add_out, add_both, gained = [], [], []
        for q in qs:
            rows, exp = cache[q['id']][:k], q.get('expected_sources', [])
            hit = any(match(exp, unit_of(r)) for r in rows)
            have = {node_of(r['metadata']) for r in rows}
            o = set().union(*[set(g.get(n, [])) for n in have if n]) - have
            i = set().union(*[rev.get(n, set()) for n in have if n]) - have
            h_out = hit or any(match(exp, unit_of_node(n)) for n in o)
            h_both = h_out or any(match(exp, unit_of_node(n)) for n in i)
            base += hit; out += h_out; both += h_both
            add_out.append(len(o)); add_both.append(len(o | i))
            if h_out and not hit:
                gained.append(q['id'])
        n = len(qs)
        print(f'  상위 {k}: 그대로 {base}/{n} · +가리키는 조문 {out}/{n} (새로 잡힌 문항 {gained}, 더해지는 조문 평균 {sum(add_out)/n:.1f}·최대 {max(add_out)})'
              f' · +양방향 {both}/{n} (평균 {sum(add_both)/n:.1f}·최대 {max(add_both)})')


if __name__ == '__main__':
    main()
