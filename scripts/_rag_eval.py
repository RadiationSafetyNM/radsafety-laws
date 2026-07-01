#!/usr/bin/env python3
"""로컬 RAG 검색 품질 검증 — 임베딩 회수(recall) 테스트(생성 LLM 없음).

코퍼스 = 법령 조 청크 + 별표 md(삭제분 제외). 임베딩 캐시(/tmp) → 매처 반복 빠름.
EMB_MODEL 환경변수로 모델 교체(기본 e5-large).
"""
import json, glob, os, re, hashlib
import numpy as np
from fastembed import TextEmbedding

VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
EVAL = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋.yaml'
CAP = 1800
MODEL = os.environ.get('EMB_MODEL', 'intfloat/multilingual-e5-large')
E5 = 'e5' in MODEL
PPFX, QPFX = ('passage: ', 'query: ') if E5 else ('', '')


def norm(s):
    return re.sub(r'[\s_·ㆍ()]', '', s or '')


# ── 코퍼스 ──
units = []   # {id, text, law, art, byeol, disp}
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
        continue                                    # 삭제/빈 별표 제외
    parent = (re.search(r'parent_law:\s*"(.+?)"', t) or [None, ''])[1]
    bnum = re.search(r'\[별표\s*(\d+)', body)
    num = bnum.group(1) if bnum else '?'
    units.append({'id': os.path.basename(mdp)[:-3], 'text': body[:CAP],
                  'law': norm(parent), 'art': '', 'byeol': num,
                  'disp': f"[별표{num}] {os.path.basename(mdp)[4:46]}"})

print(f'코퍼스: {len(units)} 유닛 (삭제 별표 제외). 모델={MODEL}', flush=True)

# ── 임베딩(캐시) ──
ids_hash = hashlib.md5((MODEL + '|'.join(u['id'] for u in units)).encode()).hexdigest()[:12]
cache = f'/tmp/rag_emb_{ids_hash}.npy'
if os.path.exists(cache):
    emb = np.load(cache)
    print('임베딩 캐시 로드.', flush=True)
else:
    print('임베딩 계산 중...', flush=True)
    model = TextEmbedding(MODEL)
    emb = np.array(list(model.embed([PPFX + u['text'] for u in units])), dtype=np.float32)
    emb /= (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    np.save(cache, emb)
    print('임베딩 완료·캐시 저장.', flush=True)

import yaml
qs = yaml.safe_load(open(EVAL, encoding='utf-8'))['questions']
qmodel = TextEmbedding(MODEL)
qemb = np.array(list(qmodel.embed([QPFX + q['question'] for q in qs])), dtype=np.float32)
qemb /= (np.linalg.norm(qemb, axis=1, keepdims=True) + 1e-9)


def match(exp_list, u):
    for e in exp_list:
        en = norm(e)
        mb = re.search(r'별표(\d+)', en)
        mj = re.search(r'(제\d+조)', en)
        if mb:
            law = en[:mb.start()]
            if law and law in u['law'] and u['byeol'] == mb.group(1):
                return True
        elif mj:
            law = en[:mj.start()]
            if law and law in u['law'] and u['art'] == mj.group(1):
                return True
        else:                                       # 순수 법령명 → law-level 매칭
            if en and (en in u['law'] or u['law'] in en) and len(u['law']) > 3:
                return True
    return False


K = [1, 3, 5, 10]
hits = {k: 0 for k in K}
bytype = {}
for i, q in enumerate(qs):
    order = np.argsort(-(emb @ qemb[i]))
    exp = q.get('expected_sources', [])
    ranks = [j + 1 for j, idx in enumerate(order) if match(exp, units[idx])]
    best = ranks[0] if ranks else None
    for k in K:
        hits[k] += 1 if best and best <= k else 0
    t = q.get('type', '?')
    bytype.setdefault(t, [0, 0])
    bytype[t][1] += 1
    bytype[t][0] += 1 if best and best <= 5 else 0
    print(f"[Q{q['id']:>2} {t:2}] {'회수@'+str(best) if best else 'MISS':>7} | {q['question'][:40]}")
    for idx in order[:3]:
        print(f"      {'✓' if match(exp, units[idx]) else ' '} {units[idx]['disp'][:56]}")

n = len(qs)
print('\n검색 recall:', ' · '.join(f'@{k}={hits[k]}/{n}({100*hits[k]//n}%)' for k in K))
print('유형별 @5:', {t: f'{v[0]}/{v[1]}' for t, v in bytype.items()})
