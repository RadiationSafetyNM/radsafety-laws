#!/usr/bin/env python3
"""사람 채점용 리포트 — 평가셋 전 문항을 **서빙 경로(_ask.py)** 로 돌려 늘어놓는다 (2026-11-27 신설).

`_rag_answer.py` 는 2026-08 의 로컬 검색기(qwen3·파일 코퍼스) 위에서 돈다 — 지금 서빙(DB·gemini 임베딩·
시행일 판본·용어 사전·검증 표시)과 다른 시스템이다. 채점은 실제로 서비스되는 답으로 해야 한다.
채점하지 않는다(법적 해석의 타당성은 Dr. Ben 판단). ⚠️ 생성은 실결제(문항당 약 10원).

사용: python3 scripts/_grade_report.py [--ids 1 2] [--out <경로>]
"""
import argparse, datetime, os, sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ask import ask      # noqa: E402

VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
DIR = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot'
SET = f'{DIR}/lawbot-평가셋.yaml'
# 채점 시 유의 문항(2026-08-02 채점용 리포트의 정답 결함 메모 승계)
FLAG = {15: '기대 출처가 문서가 아닌 결함 문항 — **채점 제외**',
        32: '별표3 은 화학 형태별로 여러 행 — 여러 값을 답해도 감점 말고 표시만',
        33: '별표3 은 화학 형태별로 여러 행 — 여러 값을 답해도 감점 말고 표시만',
        34: '별표3 은 화학 형태별로 여러 행 — 여러 값을 답해도 감점 말고 표시만'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ids', nargs='*', type=int)
    ap.add_argument('--out', default=f'{DIR}/{datetime.date.today()}_RAG답변리포트_채점용.md')
    a = ap.parse_args()
    qs = yaml.safe_load(open(SET, encoding='utf-8'))['questions']
    if a.ids:
        qs = [q for q in qs if q['id'] in a.ids]
    today = datetime.date.today()
    L = ['---', f'title: RAG 답변 리포트 {len(qs)}문항 — 채점용 (서빙 경로 {today})',
         'source: radsafety-laws scripts/_grade_report.py · 서빙 경로(_ask.py)', f'date: {today}',
         'tags: [project, RadSafety-lawbot, RAG, 채점, 평가]', '---', '',
         '[허브](RadSafety-lawbot.md) · 이전 판(없어진 구성): [2026-08-02](2026-08-02_RAG답변리포트_채점용.md)', '',
         '**채점 방법**: 문항마다 `채점:` 줄에 정확성 0~3 · 인용 0~1 · 지어냄없음 0~1 을 적고, '
         '`메모:` 에 실패 유형을 한 줄로. 점수보다 **반복되는 실패 유형**이 목적입니다.', '']
    tin = tout = 0
    for q in qs:
        try:
            o = ask(q['question'])
        except Exception as e:           # 한 문항 실패로 전체를 버리지 않는다
            L += [f"## Q{q['id']} · {q.get('type', '')}", '', f"**질문** {q['question']}", '', f'⚠️ 생성 실패: {e}', '']
            print(f"Q{q['id']} 실패 {e}", flush=True)
            continue
        c, u = o['checks'], o['usage']
        tin += u['in'] or 0
        tout += u['out'] or 0
        L += [f"## Q{q['id']} · {q.get('type', '')} · {q.get('status', 'verified')}", '']
        if q['id'] in FLAG:
            L += [f"> ⚠️ {FLAG[q['id']]}", '']
        L += [f"**질문** {q['question']}", '', f"**정답** {q.get('ground_truth', '')}", '',
              f"**기대 출처** {' · '.join(q.get('expected_sources', []))}", '', '**모델 답변**', '', o['answer'], '',
              f"**검증 표시** 근거 강도 {c['strength']}({c['top']}) · 원문에 없는 수치 {c['unsupportedNumbers'] or '없음'}"
              f" · 검색 근거에 없는 인용 {c['uncitedRefs'] or '없음'}", '', '**근거 카드**', '']
        L += [f"{i}. {s['title']} ({s['similarity']:.3f})" for i, s in enumerate(o['sources'], 1)]
        L += ['', '채점: 정확성 /3 · 인용 /1 · 지어냄없음 /1', '', '메모: ', '']
        print(f"Q{q['id']} ok", flush=True)
    L[10:10] = [f'토큰 합계: 입력 {tin:,} · 출력 {tout:,}', '']
    open(a.out, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print(a.out)


if __name__ == '__main__':
    main()
