#!/usr/bin/env python3
"""적대 평가 — 지어내기 함정 문항을 서빙 경로(_ask.py)로 돌려 자동 채점 (2026-11-22 신설).

평가셋 = vault lawbot-평가셋-적대.yaml (문항 설계·근거는 그 파일 주석).
채점: must_any(하나 이상) · must_all(전부) · must_not(하나라도 있으면 실패) ·
      검증 표시의 '원문에서 확인 안 된 수치'가 비어야 함(_verify.py).
⚠️ 생성은 실결제(문항당 약 10원). 사용: python3 scripts/_adversarial_eval.py [--only A1 A4]
"""
import argparse, os, sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ask import ask      # noqa: E402

VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
SET = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋-적대.yaml'


def grade(q, out):
    a, why = out['answer'], []
    if q.get('must_any') and not any(k in a for k in q['must_any']):
        why.append(f"must_any 없음 {q['must_any']}")
    miss = [k for k in q.get('must_all', []) if k not in a]
    if miss:
        why.append(f'must_all 누락 {miss}')
    bad = [k for k in q.get('must_not', []) if k in a]
    if bad:
        why.append(f'must_not 포함 {bad}')
    if out['checks']['unsupportedNumbers']:
        why.append(f"원문에 없는 수치 {out['checks']['unsupportedNumbers']}")
    return why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', nargs='*')
    a = ap.parse_args()
    qs = yaml.safe_load(open(SET, encoding='utf-8'))['questions']
    if a.only:
        qs = [q for q in qs if q['id'] in a.only]
    fails = 0
    for q in qs:
        out = ask(q['question'])
        why = grade(q, out)
        fails += bool(why)
        c = out['checks']
        print(f"[{q['id']}] {'PASS' if not why else 'FAIL'} ({q['trap']}) 근거강도 {c['strength']}({c['top']})")
        print('   Q: ' + q['question'])
        print('   A: ' + out['answer'].replace('\n', ' ')[:260])
        for w in why:
            print('   ✗ ' + w)
    print(f'\n── {len(qs) - fails}/{len(qs)} 통과')


if __name__ == '__main__':
    main()
