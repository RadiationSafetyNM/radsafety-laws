#!/usr/bin/env python3
"""RAG 답변 생성 — 검색(검증됨) → Claude Sonnet 생성 → **사람 채점용** 리포트.

왜 Sonnet 인가: 운영 lawbot 의 생성 모델이 Claude Sonnet 으로 이미 결정돼 있다
(2026-06-27 결정 1.5 — Claude 주력 + Gemini Flash 폴백). 로컬 모델로 재면 실전과
다른 것을 재게 된다. 평가는 '로컬·개발' 활동이라 Claude Code 구독으로 돌린다(추가과금 0).

**채점하지 않는다.** 수치 일치는 기계로 잡히지만 법적 해석의 타당성은 도메인 판단이라
Dr. Ben 이 직접 매긴다(2026-07-31 결정). 이 스크립트는 채점하기 좋은 형태로 늘어놓기만 한다:
질문 · 정답 · 모델 답변 · 인용 대조 · 근거 청크를 한 화면에.

검색 단계는 `_rag_eval_ollama.py` 와 같은 임베딩(bge-m3·GPU)·같은 코퍼스를 쓴다 —
회수율이 이미 측정된 그 검색기 위에서 생성만 얹는 구조.

사용:
  python3 scripts/_rag_answer.py                       # 전 문항
  python3 scripts/_rag_answer.py --ids 32 33 34        # 일부만
  python3 scripts/_rag_answer.py --topk 5 --out out/rag_answers.md
"""
import json, glob, os, re, hashlib, argparse, subprocess, sys, html
import numpy as np
import requests
import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
EVAL = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋.yaml'
CAP = 1800
EMB_MODEL = os.environ.get('OLLAMA_MODEL', 'bge-m3')
OLLAMA = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
GEN_MODEL = os.environ.get('GEN_MODEL', 'sonnet')

SYS = """당신은 대한민국 방사선안전 법령 상담 어시스턴트입니다.

규칙:
1. 아래 [근거 자료]에 있는 내용만으로 답하십시오. 자료에 없으면 "제공된 자료에서 확인되지 않습니다"라고 말하십시오.
2. 수치는 자료에 적힌 그대로 인용하십시오. 단위를 임의로 환산하지 마십시오.
3. 답변 마지막 줄은 반드시 「근거」 로 시작해야 합니다. 다른 표기(근거:, **근거** 등)를 쓰지 마십시오.
   예) 「근거」 원자력안전법 시행령 별표1(제2조제4호 관련) 제1호
4. 추측·일반상식으로 메우지 마십시오. 모르면 모른다고 하십시오.
5. 간결하게 답하십시오(6문장 이내 + 근거 줄).

관할 분기(중요):
6. 의료 방사선은 소관 법이 갈립니다. 같은 질문이라도 대상이 무엇이냐에 따라 근거 법령이 달라집니다.
   · 방사성동위원소·방사선발생장치(핵의학·방사선치료) → 원자력안전법 계열(원자력안전위원회)
   · 진단용 방사선 발생장치(영상의학 X선·CT 등) → 의료법 제37조 및 그 위임 규칙·고시 계열
   먼저 질문 대상이 어느 계열인지 판정하고, 그 계열의 근거로 답하십시오.
7. 계열이 다른 자료를 같은 급의 근거로 섞지 마십시오. 대조가 필요하면 "참고"로 구분해 덧붙이십시오.
8. **계열 판정이 애매하다는 이유로 답을 거부하지 마십시오.** 두 계열 모두 성립할 수 있으면
   계열별로 나누어 답하고, 각각의 적용 대상을 밝히십시오.

거부의 범위(중요):
9. 거부는 **자료에 답이 없을 때만** 하십시오. 자료가 질문의 일부만 다루면, 다루는 범위는 답하고
   못 다루는 부분만 짚어 말하십시오. 부분적으로 답할 수 있는데 전체를 거부하지 마십시오."""


def norm(s):
    return re.sub(r'[\s_·ㆍ()]', '', s or '')


def load_units():
    units = []
    for line in open(os.path.join(ROOT, 'data/chunks/law_chunks.jsonl'), encoding='utf-8'):
        r = json.loads(line)
        m = r['metadata']
        att = m.get('document_type') == 'attachment'
        units.append({
            'id': r['chunk_id'], 'text': r['content'][:CAP],
            'disp': (f"[{m.get('attachment_no')}] {m['law_title']} {m['subunit']}" if att
                     else f"{m['law_title']} {m['article']}{m['subunit']}"),
        })
    return units


def embed(texts, tag):
    key = hashlib.md5((EMB_MODEL + tag + str(len(texts))).encode()).hexdigest()[:12]
    cache = f'/tmp/rag_ans_{key}.npy'
    if os.path.exists(cache):
        return np.load(cache)
    out = []
    for i, t in enumerate(texts):
        r = requests.post(f'{OLLAMA}/api/embeddings',
                          json={'model': EMB_MODEL, 'prompt': t}, timeout=120)
        r.raise_for_status()
        out.append(r.json()['embedding'])
        if (i + 1) % 200 == 0:
            print(f'  임베딩 {i + 1}/{len(texts)}', flush=True)
    a = np.array(out, dtype=np.float32)
    a /= (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    np.save(cache, a)
    return a


def ask_claude(prompt):
    """claude -p 헤드리스. 실패는 리포트에 그대로 남긴다(조용히 넘기지 않는다)."""
    p = subprocess.run(['claude', '-p', '--model', GEN_MODEL, prompt],
                       capture_output=True, text=True, timeout=300, cwd='/tmp')
    if p.returncode != 0:
        return f'⚠️ 생성 실패 (exit {p.returncode}): {p.stderr.strip()[:300]}'
    return p.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description='RAG 답변 생성(사람 채점용 리포트)')
    ap.add_argument('--topk', type=int, default=5)
    ap.add_argument('--ids', nargs='*', type=int, default=None)
    ap.add_argument('--out', default='out/rag_answers.md')
    a = ap.parse_args()

    units = load_units()
    print(f'코퍼스 {len(units)} 유닛 · 임베딩 {EMB_MODEL} · 생성 {GEN_MODEL}', flush=True)
    emb = embed([u['text'] for u in units], 'corpus')

    qs = yaml.safe_load(open(EVAL, encoding='utf-8'))['questions']
    if a.ids:
        qs = [q for q in qs if q['id'] in a.ids]
    qemb = embed([q['question'] for q in qs], 'q' + ','.join(str(q['id']) for q in qs))

    lines = ['# RAG 답변 리포트 — 사람 채점용', '',
             f'- 코퍼스: {len(units)} 청크 · 임베딩 `{EMB_MODEL}` · 생성 `claude {GEN_MODEL}`',
             f'- 검색 top-{a.topk} 를 근거로 제공. **채점은 Dr. Ben 이 직접** (accuracy 3 · citation 1 · no_hallucination 1)',
             '- 기준선: `baseline_self` = 2026-06-14 수기 측정(별표 파싱·청킹 **이전**) 합 31/40', '']

    for i, q in enumerate(qs):
        order = np.argsort(-(emb @ qemb[i]))[:a.topk]
        ctx = '\n\n---\n\n'.join(f"[자료 {n + 1}] {units[j]['disp']}\n{units[j]['text']}"
                                 for n, j in enumerate(order))
        prompt = f'{SYS}\n\n[근거 자료]\n{ctx}\n\n[질문]\n{q["question"]}'
        print(f'  Q{q["id"]} 생성 중…', flush=True)
        ans = ask_claude(prompt)

        base = q.get('baseline_self') or {}
        lines += [
            f'## Q{q["id"]} · {q.get("type", "")}{"/" + q["subtype"] if q.get("subtype") else ""}',
            '',
            f'**질문** {q["question"]}', '',
            f'**정답(ground_truth)** {q.get("ground_truth", "—")}', '',
            f'**기대 출처** `{", ".join(q.get("expected_sources", []) or ["—"])}`'
            + (f' · 2026-06 기준선 {base.get("total")}/5' if base else ''), '',
            '**모델 답변**', '', ans, '',
            '<details><summary>검색된 근거 top-%d</summary>' % a.topk, '',
        ]
        lines += [f'{n + 1}. `{units[j]["disp"]}`' for n, j in enumerate(order)]
        lines += ['', '</details>', '',
                  '| accuracy(3) | citation(1) | no_hallucination(1) | 계 |',
                  '|---|---|---|---|', '|  |  |  |  |', '', '---', '']

    out = os.path.join(ROOT, a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'\n리포트 → {out}  ({len(qs)}문항)')


if __name__ == '__main__':
    main()
