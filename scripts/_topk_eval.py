#!/usr/bin/env python3
"""검색 개수(TOPK) 비교 — 법령 답 품질·비용 (2026-11-27). ⚠️ 실결제(문항당 약 10~20원 × 설정 수).

같은 문항을 TOPK 만 바꿔 서빙 경로(_ask.py, 지침 답 제외)로 돌리고 자동 신호를 견준다:
  · 정답 낱말(answer_keys)이 답에 다 있는가   · 원문에 없는 수치   · 검색 근거에 없는 인용   · 토큰
  · 적대 평가셋(지어내기 유도) 통과 수         · 다단계 문항: need 조문이 「근거」 줄에 인용됐는가
답 전문은 out/topk_<k>.json 에 남긴다(달라진 문항만 사람이 본다).
사용: python3 scripts/_topk_eval.py 5 8 10
"""
import json, os, re, sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _ask                                           # noqa: E402
from _answer_keys import has_all                      # noqa: E402
from _adversarial_eval import grade as adv_grade, SET as ADV   # noqa: E402

DIR = os.path.expanduser('~/projects/2nd-brain-vault/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot')
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sq = lambda s: re.sub(r'\s', '', s)


def run(k):
    _ask.TOPK = k
    out = {'k': k, 'main': {}, 'adv': {}, 'multi': {}}
    for q in yaml.safe_load(open(f'{DIR}/lawbot-평가셋.yaml', encoding='utf-8'))['questions']:
        o = _ask.ask(q['question'], guide=False)
        keys = q.get('answer_keys')
        out['main'][q['id']] = {'answer': o['answer'], 'keys_ok': has_all(o['answer'], keys) if keys else None,
                                'unsupported': o['checks']['unsupportedNumbers'], 'uncited': o['checks']['uncitedRefs'],
                                'in': o['usage']['in'], 'out': o['usage']['out']}
        print(f"k={k} Q{q['id']}", flush=True)
    for q in yaml.safe_load(open(ADV, encoding='utf-8'))['questions']:
        o = _ask.ask(q['question'], guide=False)
        out['adv'][q['id']] = {'answer': o['answer'], 'fail': adv_grade(q, o)}
    for q in yaml.safe_load(open(f'{DIR}/lawbot-평가셋-다단계.yaml', encoding='utf-8'))['questions']:
        o = _ask.ask(q['question'], guide=False)
        basis = sq(next((l for l in o['answer'].splitlines() if l.strip().startswith('「근거」')), ''))
        titles = sq(' '.join(s['title'] for s in o['sources']))
        need = q['need']
        art = lambda n: sq(n.rsplit(' ', 1)[1])
        out['multi'][q['id']] = {'answer': o['answer'], 'need': len(need),
                                 'retrieved': sum(sq(n) in titles for n in need),
                                 'cited': sum(art(n) in basis for n in need),
                                 'in': o['usage']['in'], 'out': o['usage']['out']}
        print(f"k={k} {q['id']}", flush=True)
    json.dump(out, open(f'{ROOT}/out/topk_{k}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return out


def main():
    ks = [int(x) for x in sys.argv[1:]] or [5, 8, 10]
    res = {k: run(k) for k in ks}
    print()
    for k, r in res.items():
        m, a, mu = r['main'], r['adv'], r['multi']
        keyed = [v for v in m.values() if v['keys_ok'] is not None]
        print(f"TOPK {k:>2} | 정답 낱말 {sum(v['keys_ok'] for v in keyed)}/{len(keyed)}"
              f" | 원문에 없는 수치 {sum(bool(v['unsupported']) for v in m.values())}문항"
              f" | 근거 밖 인용 {sum(bool(v['uncited']) for v in m.values())}문항"
              f" | 적대 {sum(not v['fail'] for v in a.values())}/{len(a)}"
              f" | 다단계: 검색 {sum(v['retrieved'] for v in mu.values())}/{sum(v['need'] for v in mu.values())}"
              f" · 인용 {sum(v['cited'] for v in mu.values())}/{sum(v['need'] for v in mu.values())}"
              f" | 입력 토큰 평균 {sum(v['in'] or 0 for v in m.values()) // len(m):,}")
    base = res[ks[0]]
    for k in ks[1:]:
        d = [(i, base['main'][i]['keys_ok'], v['keys_ok']) for i, v in res[k]['main'].items() if v['keys_ok'] != base['main'][i]['keys_ok']]
        print(f'TOPK {ks[0]}→{k} 정답 낱말 결과가 바뀐 문항(번호, 전, 후): {d}')
        d = [(i, base['adv'][i]['fail'], v['fail']) for i, v in res[k]['adv'].items() if bool(v['fail']) != bool(base['adv'][i]['fail'])]
        print(f'TOPK {ks[0]}→{k} 적대 결과가 바뀐 문항: {d}')


if __name__ == '__main__':
    main()
