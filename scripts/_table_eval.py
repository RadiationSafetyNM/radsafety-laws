#!/usr/bin/env python3
"""표 값 조회 평가 (2026-11-27). 평가셋 = vault lawbot-평가셋-표값.yaml. 서빙 경로(_ask.py, 지침 답 제외).
⚠️ 실결제(문항당 약 10~15원). 사용: python3 scripts/_table_eval.py [--only T1 T4] [--tag before]
"""
import json, os, re, sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ask import ask      # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SET = os.path.expanduser('~/projects/2nd-brain-vault/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋-표값.yaml')
sq = lambda s: re.sub(r'\s', '', str(s))


def main():
    a = sys.argv[1:]
    only = a[a.index('--only') + 1:] if '--only' in a else None
    tag = a[a.index('--tag') + 1] if '--tag' in a else 'run'
    qs = [q for q in yaml.safe_load(open(SET, encoding='utf-8'))['questions'] if not only or q['id'] in only]
    ok, log = 0, {}
    for q in qs:
        o = ask(q['question'], guide=False)
        ans = o['answer']
        why = [f'값 없음 {k}' for k in q['keys'] if sq(k) not in sq(ans)]
        why += [f'잘못 짝지음 /{w}/' for w in q.get('wrong', []) if re.search(w, ans)]
        got = any(sq(q['src']) in sq(s['title']) for s in o['sources'])
        if o['checks']['unsupportedNumbers']:
            why.append(f"원문에 없는 수치 {o['checks']['unsupportedNumbers']}")
        ok += not why
        log[q['id']] = {'answer': ans, 'why': why, 'src_retrieved': got}
        print(f"[{q['id']}] {'PASS' if not why else 'FAIL'} · 해당 별표 검색됨 {'O' if got else 'X'} · {q['question']}")
        print('   A: ' + ans.replace('\n', ' ')[:230])
        for w in why:
            print('   ✗ ' + w)
    json.dump(log, open(f'{ROOT}/out/table_eval_{tag}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\n── {ok}/{len(qs)} 통과 · 해당 별표 검색 {sum(v["src_retrieved"] for v in log.values())}/{len(qs)} ({tag})')


if __name__ == '__main__':
    main()
