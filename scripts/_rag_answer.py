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

SYS = """당신은 대한민국 방사선안전 법령 상담 어시스턴트입니다.

규칙:
1. 아래 [근거 자료]에 있는 내용만으로 답하십시오. 자료에 없으면 "제공된 자료에서 확인되지 않습니다"라고 말하십시오.
2. 수치의 **값**은 자료에 적힌 그대로 인용하십시오. 단위를 임의로 환산하지 마십시오.
   다만 지수 **표기 형식**은 통일하십시오 — 자료의 `1E+09`, `8 × 10 -1`, `2.58×10-5` 는
   각각 `1×10⁹`, `8×10⁻¹`, `2.58×10⁻⁵` 로 다시 써서 답하십시오. 값을 바꾸라는 뜻이 아니라
   표기만 통일하라는 뜻입니다. 특히 공백으로 분리된 `8 × 10 -1` 은 뺄셈으로 오독되므로
   반드시 재표기하십시오.
3. 답변 마지막 줄은 반드시 「근거」 로 시작해야 합니다. 다른 표기(근거:, **근거** 등)를 쓰지 마십시오.
   예) 「근거」 원자력안전법 시행령 별표1(제2조제4호 관련) 제1호
   이 줄에는 **출처만** 적으십시오. 정정·해설·단서를 이 줄에 넣지 마십시오.
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
9. 하나의 장비·시설이 **여러 구성요소로 이뤄져 있으면 구성요소마다 계열을 따로 판정**하십시오.
   예) PET-CT 는 방사성동위원소를 쓰는 PET 부분(원자력안전법 계열)과 엑스선을 쓰는 CT 부분
   (의료법 계열)이 결합된 장비이므로 **두 계열이 함께 적용**됩니다. 한쪽만 답하면 사용자는
   나머지 한쪽의 규제를 모르는 채로 남습니다. 장비명이 둘 이상의 방식을 결합한 형태
   (PET-CT, SPECT-CT 등)이거나 회수된 자료가 서로 다른 계열에 걸쳐 있으면 특히 주의하십시오.

거부의 범위(중요):
10. 거부는 **자료에 답이 없을 때만** 하십시오. 자료가 질문의 일부만 다루면, 다루는 범위는 답하고
   못 다루는 부분만 짚어 말하십시오. 부분적으로 답할 수 있는데 전체를 거부하지 마십시오.

표에서 값을 읽을 때(중요):
11. 표 행의 값을 인용하기 전에 **헤더 행과 열 위치를 하나씩 대조**하십시오. 행의 첫 값이 곧
    질문한 항목이라고 가정하지 마십시오 — 예컨대 `핵종 | A1 | A2 | …` 표에서 A2 를 물었다면
    두 번째 값 열입니다.
12. 답에는 **어느 열의 값인지 이름을 함께** 밝히십시오(예: "A1 = 3×10⁰ TBq, A2 = 7×10⁻¹ TBq").
    질문이 한 값만 물었더라도 혼동 위험이 있는 표라면 인접 열 값을 함께 적어 대조하십시오.
13. 답을 쓰다 앞서 쓴 값이 틀렸음을 알게 되면 **본문을 고쳐 다시 쓰십시오.** 본문은 그대로 두고
    뒤에 정정만 덧붙이지 마십시오 — 본문과 「근거」 줄이 서로 다른 값을 말하면, 앞만 읽는
    사용자는 틀린 값을 가져갑니다.
14. 기준값을 인용할 때는 그 값이 **어떤 조건에서 적용되는지** 함께 밝히십시오. 자료에서 그 값이
    "다음 요건을 갖춘 경우", "…만을 설치한 촬영실은", "…이하인 경우" 같은 단서 아래 놓여 있으면
    그 단서를 빼고 답하지 마십시오. **조건부 기준을 무조건적 기준처럼 제시하면 적용 대상이
    아닌 사람이 그대로 따르게 됩니다.**"""


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
