#!/usr/bin/env python3
"""로컬 GPU RAG 검색 품질 검증 — Ollama 임베딩(bge-m3 등) 회수(recall) 테스트.

_rag_eval.py 의 코퍼스·매처 로직을 그대로 재사용하되 임베딩만 Ollama(GPU) 로 교체.
GPU 백엔드(Ollama)에서 fastembed CPU 대비 수십배 빠름. 생성 LLM 없음(순수 회수 측정).

  OLLAMA_MODEL 환경변수로 모델 교체(기본 bge-m3). 임베딩 캐시(/tmp/rag_emb_ollama_*.npy).
"""
import json, glob, os, re, hashlib, time
import numpy as np
import requests

VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
EVAL = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋.yaml'
CAP = 1800
MODEL = os.environ.get('OLLAMA_MODEL', 'bge-m3')
OLLAMA = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')


def norm(s):
    return re.sub(r'[\s_·ㆍ()]', '', s or '')


def subseq(a, b):
    """a 가 b 의 부분수열인가 — 공식 법령명에 낀 조사(의·에·관한 등) 삽입에 견디는 완화 매칭.
    예: '의료분야방사선안전관리기술기준' ⊆ '의료분야의방사선안전관리에관한기술기준'."""
    it = iter(b)
    return all(c in it for c in a)


def embed(texts, batch=64):
    """Ollama /api/embed 배치 호출 → L2 정규화된 float32 행렬."""
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        r = requests.post(f'{OLLAMA}/api/embed', json={'model': MODEL, 'input': chunk}, timeout=300)
        r.raise_for_status()
        out.extend(r.json()['embeddings'])
        print(f'  임베딩 {min(i + batch, len(texts))}/{len(texts)}', end='\r', flush=True)
    print()
    e = np.array(out, dtype=np.float32)
    e /= (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)
    return e


# ── 코퍼스 (원본 하네스와 동일) ──
units = []
# 별표 표현 방식 — 2026-07-31 별표가 청크로 승격되며 두 경로가 겹쳤다(_rag_eval.py 와 동일 규약).
#   RAG_ATT=chunk(기본): 청크의 attachment 사용  /  RAG_ATT=md: 구 방식(청크 attachment 제외 + parsed md 통짜)
ATT_MODE = os.environ.get('RAG_ATT', 'chunk')

for line in open('data/chunks/law_chunks.jsonl', encoding='utf-8'):
    r = json.loads(line)
    m = r['metadata']
    byeol = ''
    if m.get('document_type') == 'attachment':
        if ATT_MODE != 'chunk':
            continue
        bm = re.match(r'별표(\d+)', m.get('attachment_no', ''))
        byeol = bm.group(1) if bm else '?'
    units.append({'id': r['chunk_id'], 'text': r['content'][:CAP],
                  'law': norm(m['law_title']), 'art': m['article'], 'byeol': byeol,
                  'disp': f"{m['law_title']} {m['article']}{m['subunit']}"})

for mdp in (sorted(glob.glob('data/attachments-parsed/*.md')) if ATT_MODE == 'md' else []):
    t = open(mdp, encoding='utf-8').read()
    body = re.split(r'^---\s*$', t, maxsplit=2, flags=re.M)[-1].strip()
    if re.search(r'삭제\s*(&lt;|<)', body[:60]) or len(re.sub(r'\s', '', body)) < 40:
        continue
    parent = (re.search(r'parent_law:\s*"(.+?)"', t) or [None, ''])[1]
    bnum = re.search(r'\[별표\s*(\d+)', body)
    num = bnum.group(1) if bnum else '?'
    units.append({'id': os.path.basename(mdp)[:-3], 'text': body[:CAP],
                  'law': norm(parent), 'art': '', 'byeol': num,
                  'disp': f"[별표{num}] {os.path.basename(mdp)[4:46]}"})

print(f'코퍼스: {len(units)} 유닛 (삭제 별표 제외). 모델={MODEL}', flush=True)

# ── 임베딩(캐시) ──
# ⚠️ id 만 해싱하면 **청크 내용이 바뀌어도 캐시가 적중**해 낡은 임베딩으로 측정하게 된다
# (2026-08-01 별표 색인 추가 때 실제로 걸릴 뻔했다). 본문까지 넣어 내용 변경을 반영한다.
ids_hash = hashlib.md5(
    (MODEL + '|'.join(u['id'] + u['text'] for u in units)).encode()).hexdigest()[:12]
cache = f'/tmp/rag_emb_ollama_{ids_hash}.npy'
if os.path.exists(cache):
    emb = np.load(cache)
    print('임베딩 캐시 로드.', flush=True)
else:
    print('임베딩 계산 중(GPU)...', flush=True)
    t0 = time.time()
    emb = embed([u['text'] for u in units])
    np.save(cache, emb)
    print(f'임베딩 완료·캐시 저장. {len(units)}유닛 {time.time()-t0:.1f}s', flush=True)

import yaml
qs = yaml.safe_load(open(EVAL, encoding='utf-8'))['questions']
# qwen3-embedding 은 쿼리에 instruct 프리픽스 권장(문서는 그대로). bge-m3 는 프리픽스 불필요.
if 'qwen3' in MODEL:
    INSTRUCT = ('Instruct: Given a Korean radiation-safety legal question, '
                'retrieve the relevant statute articles and 별표(tables) that answer it\nQuery: ')
else:
    INSTRUCT = ''
qemb = embed([INSTRUCT + q['question'] for q in qs])

# ── 검색기 2종 — 벡터 단독 / 하이브리드(벡터+BM25 RRF). 한 번에 재서 A/B 를 만든다 ──
from _bm25 import BM25, rrf                                          # noqa: E402
print('BM25 색인 중...', flush=True)
bm = BM25([u['text'] for u in units])
# 어휘 가중치 스윕 — 동일 가중(1.0)이 해로운 게 실측돼(84%→75%) 비중을 훑어 최적점을 찾는다.
LEXW = [float(x) for x in os.environ.get('RAG_LEXW', '0.15,0.3,0.5,1.0').split(',')]
ORDERS = {}
for i, q in enumerate(qs):
    vec = list(np.argsort(-(emb @ qemb[i])))
    lex = list(np.argsort(-np.array(bm.scores(q['question']))))
    ORDERS[q['id']] = {'vector': vec}
    for w in LEXW:
        ORDERS[q['id']][f'hyb{w}'] = rrf(vec[:100], lex[:100], weights=[1.0, w])
RETRIEVERS = ['vector'] + [f'hyb{w}' for w in LEXW]


def match(exp_list, u):
    for e in exp_list:
        en = norm(e)
        mb = re.search(r'별표(\d+)', en)
        mj = re.search(r'(제\d+조(?:의\d+)?)', en)   # 제N조의M 형태(가지조문)까지 포착
        if mb:
            law = en[:mb.start()]
            if law and subseq(law, u['law']) and u['byeol'] == mb.group(1):
                return True
        elif mj:
            law = en[:mj.start()]
            if law and subseq(law, u['law']) and u['art'] == mj.group(1):
                return True
        else:                                       # 순수 법령명 → 접두 매칭(계열 허용, 오탐 방지)
            # subseq 는 '의료법'⊆'의료기기법' 오탐 → 접두로 제한. 계열(법→시행령/규칙)은 접두로 잡힘.
            if en and len(en) >= 2 and (u['law'].startswith(en) or en.startswith(u['law'])):
                return True
    return False


# 코퍼스 갭(정답 법령이 코퍼스에 없음) — MISS 가 올바른 동작(정직성 테스트). recall 분모에서 제외.
CORPUS_GAP = {14}   # Q14 수의사법 미수록

K = [1, 3, 5, 10]


def recall(subset, ret='vector'):
    hits = {k: 0 for k in K}
    for q in subset:
        order = ORDERS[q['id']][ret]
        best = next((j + 1 for j, idx in enumerate(order)
                     if match(q.get('expected_sources', []), units[idx])), None)
        for k in K:
            hits[k] += 1 if best and best <= k else 0
    n = len(subset)
    return ' · '.join(f'@{k}={hits[k]}/{n}({round(100*hits[k]/n) if n else 0}%)' for k in K)


for i, q in enumerate(qs):
    q['_i'] = i
    order = ORDERS[q['id']]['vector']
    exp = q.get('expected_sources', [])
    best = next((j + 1 for j, idx in enumerate(order) if match(exp, units[idx])), None)
    q['_best'] = best
    q['_best_hyb'] = next((j + 1 for j, idx in enumerate(ORDERS[q['id']][RETRIEVERS[1]])
                           if match(exp, units[idx])), None)
    prov = q.get('status') == 'provisional'
    gap = q['id'] in CORPUS_GAP
    tag = 'GAP' if gap else ('prov' if prov else 'ver ')
    verdict = f'회수@{best}' if best else ('MISS(정상=코퍼스갭)' if gap else 'MISS')
    hyb = q['_best_hyb']
    delta = ('' if hyb == best else
             f"  [hybrid {'MISS' if not hyb else '@' + str(hyb)}"
             + (' ↑' if hyb and (not best or hyb < best) else ' ↓') + ']')
    print(f"[Q{q['id']:>2} {q.get('type','?'):2} {tag}] {verdict:>16}{delta} | {q['question'][:38]}")
    print(f"      기대: {exp}")
    for idx in order[:10]:
        print(f"      {'✓' if match(exp, units[idx]) else ' '} {units[idx]['disp'][:60]}")

# ── answerable recall — "정답이 top-k 청크 **본문에** 실재하나" (2026-08-01 신설) ──
# 위 recall 은 기대출처 *문서* 만 본다. 그 문서의 다른 부분이 잡혀도 hit 이라, 답이 실제로
# 회수됐는지는 모른다. 2026-08-01 triage 에서 Q12·Q36 이 이 틈으로 빠져 검색 실패가
# 생성 실패로 오분류됐다. 여기서 갈라 본다.
#   strict = top-k 안의 **한 청크**가 정답키를 전부 담음 (생성이 한 자료로 답할 수 있음)
#   union  = top-k **전체**를 합치면 정답키가 다 나옴 (조립하면 답할 수 있음)
from _answer_keys import propose, has_all, numbers   # noqa: E402

keyed, unkeyed, autoq = [], [], []
for q in qs:
    if q['id'] in CORPUS_GAP:
        continue
    ks = q.get('answer_keys')
    if not ks:
        ks = propose(q.get('ground_truth', ''))
        if ks:
            autoq.append(q['id'])
    (keyed if ks else unkeyed).append((q, [str(k) for k in ks]))

corpus_nums = None
if keyed:
    corpus_text = ' '.join(u['text'] for u in units)
    corpus_nums = numbers(corpus_text)
    missing_keys = []
    for q, ks in keyed:
        for k in ks:
            if not has_all(corpus_text, [k]):
                missing_keys.append((q['id'], k))

    print('\n── answerable recall (정답키가 top-k 본문에 실재하나) ──')
    print(f'대상 {len(keyed)}문항 (키 없어 제외 {len(unkeyed)}문항: '
          f'{[q["id"] for q, _ in unkeyed]} · 코퍼스갭 {sorted(CORPUS_GAP)} 제외)')
    if autoq:
        print(f'⚠ 자동 제안 키로 잰 문항 {len(autoq)}개 {autoq} — 평가셋에 answer_keys 를 '
              '적어 큐레이션할 것(제안 키의 수치는 신뢰도 낮음)')
    if missing_keys:
        print(f'⛔ 코퍼스 어디에도 없는 정답키 {len(missing_keys)}개 — 키가 틀렸거나 '
              f'코퍼스 갭입니다: {missing_keys[:8]}')

    for ret in RETRIEVERS:
        for label, strict in (('strict(한 청크가 전부)', True), ('union(top-k 합쳐서)', False)):
            hits = {k: 0 for k in K}
            for q, ks in keyed:
                order = ORDERS[q['id']][ret]
                for k in K:
                    idxs = order[:k]
                    ok = (any(has_all(units[j]['text'], ks) for j in idxs) if strict
                          else has_all(' '.join(units[j]['text'] for j in idxs), ks))
                    hits[k] += 1 if ok else 0
            n = len(keyed)
            print(f'  [{ret:6}] {label:22} ' + ' · '.join(
                f'@{k}={hits[k]}/{n}({round(100 * hits[k] / n)}%)' for k in K))

    # 진단 — 출처는 맞췄는데 답은 못 가져온 문항(= Q12·Q36 류)
    for ret in RETRIEVERS:
        gapq = []
        for q, ks in keyed:
            order = ORDERS[q['id']][ret]
            best = next((j + 1 for j, idx in enumerate(order)
                         if match(q.get('expected_sources', []), units[idx])), None)
            if best and best <= 5 and not has_all(' '.join(units[j]['text'] for j in order[:5]), ks):
                gapq.append(q['id'])
        print(f'  [{ret:6}] ⚠ 출처는 @5 안인데 정답은 없는 문항 {len(gapq)}개: {gapq}')

verified = [q for q in qs if q.get('status') != 'provisional']                       # Q1~8
prov_in_corpus = [q for q in qs if q.get('status') == 'provisional' and q['id'] not in CORPUS_GAP]
in_corpus = [q for q in qs if q['id'] not in CORPUS_GAP]                             # 갭 제외 전체
print('\n── 진짜 recall (매처 부분수열 보정 + 코퍼스갭 분리) ──')
for ret in RETRIEVERS:
    print(f'[{ret}]')
    print(f'  verified {len(verified)}문항(신뢰 gold): {recall(verified, ret)}')
    print(f'  provisional {len(prov_in_corpus)}문항(코퍼스내): {recall(prov_in_corpus, ret)}')
    print(f'  전체 코퍼스내 {len(in_corpus)}문항(Q14 갭 제외): {recall(in_corpus, ret)}')
    print(f'  참고 — 전체 {len(qs)}문항(갭 포함): {recall(qs, ret)}')
print(f'코퍼스갭 Q14(수의사법 미수록): best={next(q["_best"] for q in qs if q["id"]==14)} (None=정상)')
