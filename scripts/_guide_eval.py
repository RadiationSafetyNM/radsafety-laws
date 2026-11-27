#!/usr/bin/env python3
"""수검 가이드 답 평가 (2026-11-27). 평가셋 = vault lawbot-평가셋-수검가이드.yaml (채점 축은 그 파일 머리말).

가이드 답만 만든다(법령 답 생성 없음). ⚠️ 실결제 — 문항당 약 5원, repeat 만큼 곱.
사용: python3 scripts/_guide_eval.py [--only G1 N2] [--min 0.70]
"""
import argparse, os, re, sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ask                                    # noqa: E402
from _ask import ask_guide, qvec, guide_part, _rpc   # noqa: E402

VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
SET = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋-수검가이드.yaml'
sq = lambda s: re.sub(r'\s', '', str(s))


def grade(q, g, part):
    why = []
    if q['expect'] == 'none':
        return ['지침 칸이 떴다'] if g else []
    if not g:
        return ['지침 칸이 안 떴다']
    a = sq(g['answer'])
    if q.get('part') and part != q['part']:
        why.append(f"편 {part} ≠ {q['part']}")
    titles = sq(' '.join(s['title'] for s in g['sources']))
    if q.get('section_any') and not any(sq(x) in titles for x in q['section_any']):
        why.append(f"절 불일치 {[s['title'][-40:] for s in g['sources']]}")
    miss = [k for k in q.get('must_all', []) if sq(k) not in a]
    if miss:
        why.append(f'must_all 누락 {miss}')
    if q.get('must_any') and not any(sq(k) in a for k in q['must_any']):
        why.append(f"must_any 없음 {q['must_any']}")
    got = [c['cite'] for c in g['changed']]
    lack = [c for c in q.get('changed', []) if c not in got]
    if lack:
        why.append(f'바뀐 조문 표시 누락 {lack}')
    diff = sq(next((l for l in g['answer'].splitlines() if l.startswith('※')), ''))
    lack = [k for k in q.get('diff_all', []) if sq(k) not in diff]
    if lack:
        why.append(f'※ 줄에 없음 {lack}')
    return why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--min', type=float, help='GUIDE_MIN 을 바꿔 본다')
    a = ap.parse_args()
    if a.min is not None:
        _ask.GUIDE_MIN = a.min
    qs = yaml.safe_load(open(SET, encoding='utf-8'))['questions']
    if a.only:
        qs = [q for q in qs if q['id'] in a.only]
    ok = 0
    for q in qs:
        vec, part = qvec(q['question']), guide_part(q['question'])
        pool = [g for g in _rpc('lawbot_guide_match', {'query_embedding': vec, 'match_count': _ask.GUIDE_POOL})
                if g['metadata'].get('part', '').startswith(part)]
        top = pool[0]['similarity'] if pool else 0.0
        fails = []
        for i in range(q.get('repeat', 1)):
            g = ask_guide(q['question'], vec) if (q['expect'] == 'guide' or top >= _ask.GUIDE_MIN) else None
            w = grade(q, g, part)
            if w:
                fails.append((i + 1, w))
        ok += not fails
        print(f"[{q['id']}] {'PASS' if not fails else 'FAIL'} · 편 {part} · 편내 최고 유사도 {top:.3f} · {q['question']}")
        for i, w in fails:
            print(f'   ✗ ({i}회차) ' + ' / '.join(w))
    print(f'\n── {ok}/{len(qs)} 통과 (GUIDE_MIN={_ask.GUIDE_MIN})')


if __name__ == '__main__':
    main()
