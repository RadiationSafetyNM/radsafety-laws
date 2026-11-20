#!/usr/bin/env python3
"""RAG 답변 생성 — 검색(검증됨) → 생성 → **사람 채점용** 리포트.

생성 백엔드는 갈아끼운다(`GEN_BACKEND=claude|gemini`). 로컬 모델은 쓰지 않는다 — 실전
서빙이 쓰지 않을 모델로 재면 실전과 다른 것을 재게 된다.

  claude  `claude -p` 구독 CLI. 추가과금 0 이지만 **Vercel 에서는 못 쓴다**(CLI 미실행).
  gemini  REST API. 실결제이며 AI Studio 프로젝트 `radsafety` 의 월 상한을 서빙 키와 공유.

2026-06-27 결정 1.5 는 "Claude 주력 + Gemini 폴백"이었으나, 그 근거였던 "Gemini 는 소액
하드캡 부적합"이 사실과 다름이 확인됐다(AI Studio 는 **프로젝트 단위 월 지출 상한**을 지원 —
2026-08-03 Dr. Ben 실사용). 가격은 Gemini 가 약 4배 유리하므로 주력 전환을 검토 중이고,
이 어댑터는 그 **품질 비교**와 **서빙 구현** 양쪽에 쓰인다.

**채점하지 않는다.** 수치 일치는 기계로 잡히지만 법적 해석의 타당성은 도메인 판단이라
Dr. Ben 이 직접 매긴다(2026-07-31 결정). 이 스크립트는 채점하기 좋은 형태로 늘어놓기만 한다:
질문 · 정답 · 모델 답변 · 인용 대조 · 근거 청크를 한 화면에.

검색 단계는 `_rag_eval_ollama.py` 와 같은 임베딩(qwen3-embedding:8b·GPU·같은 질의 프리픽스)·같은 코퍼스를 쓴다 —
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

from _answer_keys import has_all, normtext   # 채점용 발췌의 정답키·표기 정규화 매칭

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
EVAL = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋.yaml'
# 생성에 넘기는 청크 본문 상한. **1800 은 별표 표 청크를 행 중간에서 잘랐다**(2026-08-02):
# Q35 에서 `Tc-99m | 1 × 10 1 | 4 ` 까지만 넘어가 모델이 "A2 는 4까지만 표기되고 잘려 있다"고
# 정직하게 답했다 — 답이 틀린 게 아니라 우리가 답을 잘라 보낸 것이다. 141개 별표 청크가
# 1800자를 넘고 최대 8,243자다. 생성 모델의 컨텍스트는 1M 이므로 자를 이유가 없다.
CAP = 20000
# 2026-08-01 실측으로 bge-m3 → qwen3-embedding:8b 교체. answerable @5 84%→91%, @3 78%→91%.
# 하네스(_rag_eval_ollama.py)와 **같은 모델·같은 질의 프리픽스**를 써야 측정과 생성이 일치한다.
EMB_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen3-embedding:8b')
OLLAMA = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
# ── 생성 백엔드 — claude(구독 CLI) / gemini(종량 API) ─────────────────────────
# 평가는 지금까지 `claude -p`(Claude Code 구독)로 돌려 추가 과금이 0이었다. 서빙은 그 경로를
# 못 쓴다 — Vercel 함수에서 CLI 는 돌지 않으므로 API 키 기반이어야 한다. 그래서 이 어댑터는
# 백엔드 비교(2026-08-03 Gemini 선회 검토)와 서빙 구현 **양쪽에 필요**하다.
GEN_BACKEND = os.environ.get('GEN_BACKEND', 'claude')
_DEFAULT_MODEL = {'claude': 'sonnet', 'gemini': 'gemini-3.6-flash'}
GEN_MODEL = os.environ.get('GEN_MODEL', _DEFAULT_MODEL.get(GEN_BACKEND, 'sonnet'))


def load_dotenv(path=os.path.join(ROOT, '.env')):
    """의존성 없이 .env 를 읽는다(값은 셸 환경이 우선). 파일이 없으면 조용히 지나간다."""
    try:
        for ln in open(path, encoding='utf-8'):
            ln = ln.strip()
            if not ln or ln.startswith('#') or '=' not in ln:
                continue
            k, v = ln.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))
    except FileNotFoundError:
        pass


load_dotenv()

from _prompt import SYS   # noqa: E402  (서빙 _ask.py 와 공유)


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


# qwen3-embedding 은 질의에 instruct 프리픽스를 권장한다(문서는 그대로). 하네스와 동일 문구 —
# 다르면 측정과 생성이 서로 다른 검색을 하게 된다. bge-m3 는 프리픽스 불필요.
INSTRUCT = ('Instruct: Given a Korean radiation-safety legal question, '
            'retrieve the relevant statute articles and 별표(tables) that answer it\nQuery: '
            if 'qwen3' in EMB_MODEL else '')


def embed(texts, tag):
    # ⚠️ 캐시 키에 본문을 넣는다. 모델·태그·개수만 쓰면 **청크 내용이 바뀌어도 캐시가 적중**해
    # 낡은 임베딩으로 검색하게 된다(2026-08-01 별표 색인 추가 때 하네스에서 실제로 걸릴 뻔했다).
    key = hashlib.md5((EMB_MODEL + tag + '|'.join(texts)).encode()).hexdigest()[:12]
    cache = f'/tmp/rag_ans_{key}.npy'
    if os.path.exists(cache):
        return np.load(cache)
    # 배치 호출(/api/embed). 구 /api/embeddings 는 한 건씩이라 8B 모델·3천 청크에서 느리다 —
    # 두 엔드포인트의 벡터는 동일함을 확인했다(정규화 후 코사인 1.0, 최대차 7e-9).
    out, batch = [], 64
    for i in range(0, len(texts), batch):
        r = requests.post(f'{OLLAMA}/api/embed',
                          json={'model': EMB_MODEL, 'input': texts[i:i + batch]}, timeout=600)
        r.raise_for_status()
        out.extend(r.json()['embeddings'])
        print(f'  임베딩 {min(i + batch, len(texts))}/{len(texts)}', end='\r', flush=True)
    print()
    a = np.array(out, dtype=np.float32)
    a /= (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    np.save(cache, a)
    return a


def excerpt(text, keys, question, max_lines=6, width=190):
    """회수된 청크에서 **채점자가 볼 대목만** 잘라낸다(2026-08-02 신설).

    채점이 피로한 이유는 「근거」에 조항 *이름* 만 있어서, 검증하려면 별표를 열고 해당 행을
    찾아 올라가야 하기 때문이다. 정답키·질문의 고유 토큰(핵종·조문번호)이 걸리는 줄을 뽑고,
    표라면 헤더 행을 함께 붙여 **찾지 않고 읽기만 하면 되도록** 만든다.
    채점 보조용이므로 정답 위치가 드러나는 건 의도된 것이다(모델 입력이 아니다)."""
    lines = [ln for ln in text.split('\n') if ln.strip()]
    if not lines:
        return []
    header = next((ln for ln in lines if ln.count('|') >= 2), None)   # 표 헤더 행
    ents = [normtext(e) for e in
            re.findall(r'[A-Za-z]+-\d+[A-Za-z]*|제\d+조(?:의\d+)?|별표\s*\d+', question)]

    def score(ln):
        """질문의 고유 항목(핵종·조문)이 걸린 줄을 최우선. 숫자키만 걸린 줄은 다른 핵종
        행일 수 있어(같은 값이 여러 행에 나온다) 우선순위를 낮춘다."""
        s = 2 * sum(1 for e in ents if e and e in normtext(ln))
        if keys:
            s += 2 if all(has_all(ln, [k]) for k in keys) else (1 if any(has_all(ln, [k]) for k in keys) else 0)
        return s

    cand = [ln for ln in lines
            if ln is not header and not ln.lstrip().startswith('[수록 항목]')]
    ranked = sorted(((score(ln), i) for i, ln in enumerate(cand)), key=lambda t: (-t[0], t[1]))
    top = ranked[0][0] if ranked else 0
    if top >= 2:            # 질문의 항목이 실제로 있는 줄 — 그 줄들만 보인다
        keep = sorted(i for s, i in ranked if s >= 2)[:max_lines]
    else:                   # 없으면 이 청크는 헛짚은 것 — 맛보기 2줄만(자리 차지 방지)
        keep = sorted(i for s, i in ranked[:2] if s > 0)
    picked = [cand[i] for i in keep] or cand[:2]     # 아무것도 안 걸리면 앞부분이라도
    if header:
        picked = [header] + picked
    return [(ln[:width] + '…') if len(ln) > width else ln for ln in picked]


def ask_claude(prompt):
    """claude -p 헤드리스. 실패는 리포트에 그대로 남긴다(조용히 넘기지 않는다)."""
    p = subprocess.run(['claude', '-p', '--model', GEN_MODEL, prompt],
                       capture_output=True, text=True, timeout=300, cwd='/tmp')
    if p.returncode != 0:
        return f'⚠️ 생성 실패 (exit {p.returncode}): {p.stderr.strip()[:300]}'
    return p.stdout.strip()


GEMINI_EP = 'https://generativelanguage.googleapis.com/v1beta'


def gemini_models(key):
    """사용 가능한 생성 모델 id 목록 — 모델명이 틀렸을 때 진단에 쓴다."""
    try:
        r = requests.get(f'{GEMINI_EP}/models?key={key}', timeout=60)
        return [m['name'].split('/', 1)[-1] for m in r.json().get('models', [])
                if 'generateContent' in m.get('supportedGenerationMethods', [])]
    except Exception as e:
        return [f'(목록 조회 실패: {e})']


def ask_gemini(prompt):
    """Gemini REST(:generateContent). 키는 .env 의 GEMINI_API_KEY.

    ⚠️ **여기서부터는 실결제다.** claude 백엔드는 구독이라 0원이지만 이쪽은 호출마다 과금되고,
    AI Studio 프로젝트 `radsafety` 의 월 상한(₩10,000)을 **서빙 키와 공유**한다. 평가를 반복하면
    서빙 예산을 갉아먹으므로 필요한 만큼만 돌린다."""
    key = os.environ.get('GEMINI_API_KEY', '')
    if not key:
        return ('⚠️ 생성 실패: GEMINI_API_KEY 없음. `.env.example` 을 `.env` 로 복사해 '
                '`radsafety-lawbot-eval` 키 값을 넣으십시오.')
    try:
        r = requests.post(
            f'{GEMINI_EP}/models/{GEN_MODEL}:generateContent?key={key}',
            json={'contents': [{'parts': [{'text': prompt}]}]}, timeout=300)
        if r.status_code == 404:
            return (f'⚠️ 생성 실패: 모델 `{GEN_MODEL}` 을(를) 찾을 수 없습니다. '
                    f'사용 가능: {", ".join(gemini_models(key)[:12])}')
        r.raise_for_status()
        d = r.json()
        cand = (d.get('candidates') or [{}])[0]
        parts = cand.get('content', {}).get('parts') or []
        txt = ''.join(p.get('text', '') for p in parts).strip()
        # 빈 응답은 대개 안전필터·토큰상한이다. 조용히 넘기지 말고 이유를 리포트에 남긴다.
        return txt or f'⚠️ 생성 실패: 빈 응답 (finishReason={cand.get("finishReason")})'
    except Exception as e:
        return f'⚠️ 생성 실패: {type(e).__name__} {str(e)[:200]}'


def generate(prompt):
    return ask_gemini(prompt) if GEN_BACKEND == 'gemini' else ask_claude(prompt)


def main():
    ap = argparse.ArgumentParser(description='RAG 답변 생성(사람 채점용 리포트)')
    ap.add_argument('--topk', type=int, default=5)
    ap.add_argument('--ids', nargs='*', type=int, default=None)
    ap.add_argument('--out', default='out/rag_answers.md')
    a = ap.parse_args()

    units = load_units()
    print(f'코퍼스 {len(units)} 유닛 · 임베딩 {EMB_MODEL} · 생성 {GEN_BACKEND}:{GEN_MODEL}', flush=True)
    emb = embed([u['text'] for u in units], 'corpus')

    qs = yaml.safe_load(open(EVAL, encoding='utf-8'))['questions']
    if a.ids:
        qs = [q for q in qs if q['id'] in a.ids]
    qemb = embed([INSTRUCT + q['question'] for q in qs], 'q' + ','.join(str(q['id']) for q in qs))

    lines = ['# RAG 답변 리포트 — 사람 채점용', '',
             f'- 코퍼스: {len(units)} 청크 · 임베딩 `{EMB_MODEL}` · 생성 `{GEN_BACKEND} {GEN_MODEL}`',
             f'- 검색 top-{a.topk} 를 근거로 제공. **채점은 Dr. Ben 이 직접** (accuracy 3 · citation 1 · no_hallucination 1)',
             '- 기준선: `baseline_self` = 2026-06-14 수기 측정(별표 파싱·청킹 **이전**) 합 31/40', '']

    for i, q in enumerate(qs):
        order = np.argsort(-(emb @ qemb[i]))[:a.topk]
        ctx = '\n\n---\n\n'.join(f"[자료 {n + 1}] {units[j]['disp']}\n{units[j]['text']}"
                                 for n, j in enumerate(order))
        prompt = f'{SYS}\n\n[근거 자료]\n{ctx}\n\n[질문]\n{q["question"]}'
        print(f'  Q{q["id"]} 생성 중…', flush=True)
        ans = generate(prompt)

        base = q.get('baseline_self') or {}
        lines += [
            f'## Q{q["id"]} · {q.get("type", "")}{"/" + q["subtype"] if q.get("subtype") else ""}',
            '',
            f'**질문** {q["question"]}', '',
            f'**정답(ground_truth)** {q.get("ground_truth", "—")}', '',
            f'**기대 출처** `{", ".join(q.get("expected_sources", []) or ["—"])}`'
            + (f' · 2026-06 기준선 {base.get("total")}/5' if base else ''), '',
            '**모델 답변**', '', ans, '',
            # 회수 청크 id — 렌더링엔 안 보이지만 `_answer_audit.py` 가 이걸로 원문을 되찾는다.
            '<!-- chunks: %s -->' % ','.join(units[j]['id'] for j in order),
            '<details><summary>검색된 근거 top-%d</summary>' % a.topk, '',
        ]
        keys = list(q.get('answer_keys') or [])   # 리스트 키(대체표현) 보존 — str() 금지
        for n, j in enumerate(order):
            lines.append(f'**{n + 1}. `{units[j]["disp"]}`**')
            lines += ['', '```'] + excerpt(units[j]['text'], keys, q['question']) + ['```', '']
        lines += ['</details>', '',
                  '| accuracy(3) | citation(1) | no_hallucination(1) | 계 |',
                  '|---|---|---|---|', '|  |  |  |  |', '', '---', '']

    out = os.path.join(ROOT, a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'\n리포트 → {out}  ({len(qs)}문항)')


if __name__ == '__main__':
    main()
