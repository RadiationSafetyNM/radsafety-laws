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


MULTI = os.path.join(os.path.dirname(EVAL), 'lawbot-평가셋-다단계.yaml')


def need_node(s):
    """'원자력안전법 시행령 제82조의4' · '… 별표5의2' → 그래프 마디 이름."""
    law, x = s.rsplit(' ', 1)
    return f'{law}#{x}'


def multihop(k=5):
    """다단계 문항 — need(필요한 조문 전부) 중 몇 개가 근거로 잡히는가. 그대로 / +가리키는 조문 / +양방향."""
    url, key = os.environ['SUPABASE_URL'].rstrip('/'), os.environ['SUPABASE_SERVICE_ROLE_KEY']
    qs = yaml.safe_load(open(MULTI, encoding='utf-8'))['questions']
    nodes = set(g) | set(rev)
    tot = {'base': [0, 0], 'out': [0, 0], 'both': [0, 0]}     # [잡힌 need 수, 전부 잡힌 문항 수]
    need_n, added = 0, {'out': [], 'both': []}
    for q in qs:
        need = [need_node(x) for x in q['need']]
        bad = [n for n in need if n not in nodes]
        if bad:
            print(f"  ⚠ {q['id']} 그래프에 없는 마디 {bad}")
        rows = rpc_match(url, key, embed_query(expand(q['question'])), k)
        have = {node_of(r['metadata']) for r in rows} - {None}
        o = set().union(*[set(g.get(n, [])) for n in have]) - have
        i = set().union(*[rev.get(n, set()) for n in have]) - have
        sets = {'base': have, 'out': have | o, 'both': have | o | i}
        got = {m: [n for n in need if n in s_] for m, s_ in sets.items()}
        need_n += len(need)
        for m in tot:
            tot[m][0] += len(got[m]); tot[m][1] += len(got[m]) == len(need)
        added['out'].append(len(o)); added['both'].append(len(o | i))
        miss = [n.split('#')[1] for n in need if n not in sets['base']]
        print(f"  {q['id']:>3} 그대로 {len(got['base'])}/{len(need)} · +가리키는 {len(got['out'])}/{len(need)} · +양방향 {len(got['both'])}/{len(need)}"
              f" · 더해짐 {len(o)}/{len(o | i)} · 그대로일 때 빠진 것 {miss}")
    n = len(qs)
    for m, label in (('base', '그대로'), ('out', '+가리키는 조문'), ('both', '+양방향')):
        extra = f" · 더해지는 조문 평균 {sum(added[m]) / n:.1f}·최대 {max(added[m])}" if m in added else ''
        print(f'── {label}: 필요한 조문 {tot[m][0]}/{need_n} · 전부 잡힌 문항 {tot[m][1]}/{n}{extra}')


if __name__ == '__main__':
    multihop() if '--multihop' in sys.argv else main()
