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
for line in open('data/chunks/law_chunks.jsonl', encoding='utf-8'):
    r = json.loads(line)
    m = r['metadata']
    units.append({'id': r['chunk_id'], 'text': r['content'][:CAP],
                  'law': norm(m['law_title']), 'art': m['article'], 'byeol': '',
                  'disp': f"{m['law_title']} {m['article']}{m['subunit']}"})

for mdp in sorted(glob.glob('data/attachments-parsed/*.md')):
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
ids_hash = hashlib.md5((MODEL + '|'.join(u['id'] for u in units)).encode()).hexdigest()[:12]
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


def match(exp_list, u):
    for e in exp_list:
        en = norm(e)
        mb = re.search(r'별표(\d+)', en)
        mj = re.search(r'(제\d+조)', en)
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


def recall(subset):
    hits = {k: 0 for k in K}
    for q in subset:
        i = q['_i']
        order = np.argsort(-(emb @ qemb[i]))
        best = next((j + 1 for j, idx in enumerate(order)
                     if match(q.get('expected_sources', []), units[idx])), None)
        for k in K:
            hits[k] += 1 if best and best <= k else 0
    n = len(subset)
    return ' · '.join(f'@{k}={hits[k]}/{n}({round(100*hits[k]/n) if n else 0}%)' for k in K)


for i, q in enumerate(qs):
    q['_i'] = i
    order = np.argsort(-(emb @ qemb[i]))
    exp = q.get('expected_sources', [])
    best = next((j + 1 for j, idx in enumerate(order) if match(exp, units[idx])), None)
    q['_best'] = best
    prov = q.get('status') == 'provisional'
    gap = q['id'] in CORPUS_GAP
    tag = 'GAP' if gap else ('prov' if prov else 'ver ')
    verdict = f'회수@{best}' if best else ('MISS(정상=코퍼스갭)' if gap else 'MISS')
    print(f"[Q{q['id']:>2} {q.get('type','?'):2} {tag}] {verdict:>16} | {q['question'][:38]}")
    print(f"      기대: {exp}")
    for idx in order[:10]:
        print(f"      {'✓' if match(exp, units[idx]) else ' '} {units[idx]['disp'][:60]}")

verified = [q for q in qs if q.get('status') != 'provisional']                       # Q1~8
prov_in_corpus = [q for q in qs if q.get('status') == 'provisional' and q['id'] not in CORPUS_GAP]
in_corpus = [q for q in qs if q['id'] not in CORPUS_GAP]                             # 갭 제외 전체
print('\n── 진짜 recall (매처 부분수열 보정 + 코퍼스갭 분리) ──')
print(f'verified 8문항(Q1~8, 신뢰 gold): {recall(verified)}')
print(f'provisional 6문항(Q9~13,15, 코퍼스내): {recall(prov_in_corpus)}')
print(f'전체 코퍼스내 14문항(Q14 갭 제외):    {recall(in_corpus)}')
print(f'참고 — 전체 15문항(갭 포함, 옛 방식):  {recall(qs)}')
print(f'코퍼스갭 Q14(수의사법 미수록): best={next(q["_best"] for q in qs if q["id"]==14)} (None=정상)')
