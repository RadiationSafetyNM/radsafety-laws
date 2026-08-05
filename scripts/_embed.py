#!/usr/bin/env python3
"""임베딩 어댑터 — 코퍼스 인입과 서빙 질의가 **같은 좌표계**를 쓰도록 강제하는 한 곳.

이 파일의 존재 이유는 하나다: 임베딩은 코퍼스와 질의가 조금이라도 어긋나면
**검색이 조용히 망가진다**(에러가 안 난다 — 순위만 나빠진다). 그래서 백엔드·차원·절단·
정규화·프리픽스 규약을 흩어놓지 않고 여기 모았다.

어긋날 수 있는 축 5개 — 전부 여기서 고정된다:
  1. 모델        qwen3-embedding:8b (§결정 1.7)
  2. 백엔드      코퍼스·질의 = gateway / 평가 하네스 = ollama
                 ⚠️ 로컬 ollama 는 Q4_K_M 양자화라 호스팅과 벡터가 미세하게 다르다.
                 섞으면 안 된다. ollama 는 평가 전용으로만 남긴다.
  3. 차원        MRL 절단 1024 (pgvector 인덱스 한계 — sql/001_lawbot_chunks.sql 참조)
  4. 정규화      절단 **후** L2 재정규화 (절단만 하면 노름이 1이 아니다)
  5. 프리픽스    문서=없음 / 질의=INSTRUCT (qwen3-embedding 권장 규약)

‼️ `radsafety-pwa` 의 `/api/lawbot/ask` 는 질의 임베딩을 이 파일과 **동일하게** 해야 한다
   (같은 gateway 모델 슬러그 · 같은 1024 절단 · 절단 후 재정규화 · 같은 QUERY_INSTRUCT 문자열).
   TS 로 옮겨 적을 때 QUERY_INSTRUCT 는 개행까지 그대로 복사할 것.
"""
import os
import numpy as np
import requests

# ── 고정 규약 ──────────────────────────────────────────────────────────────
EMB_DIM = int(os.environ.get('EMB_DIM', '1024'))   # sql/001_lawbot_chunks.sql 의 vector(1024) 와 동기
NATIVE_DIM = 4096                                   # qwen3-embedding:8b 원본 차원(절단 전 검증용)
EMB_CAP = int(os.environ.get('EMB_CAP', '1800'))    # 임베딩에 넣는 본문 최대 길이

# ⚠️ EMB_CAP 은 평가 하네스(_rag_eval_ollama.py 의 CAP=1800)와 **같아야** 한다.
#    측정된 recall(@1 78%/@3 91%)은 1800자 절단 코퍼스에서 나온 값이다. 인입에서 이 값을
#    바꾸면 서빙 recall 이 하네스와 달라지는데, 그게 백엔드 문제인지 절단 길이 문제인지
#    구분할 수 없게 된다. 바꾸려면 하네스와 **동시에** 바꾸고 다시 측정할 것.

# 질의 프리픽스 — qwen3-embedding 은 쿼리에 instruct 프리픽스를 권장한다(문서는 그대로).
QUERY_INSTRUCT = ('Instruct: Given a Korean radiation-safety legal question, '
                  'retrieve the relevant statute articles and 별표(tables) that answer it\nQuery: ')

# ── 백엔드 ────────────────────────────────────────────────────────────────
BACKEND = os.environ.get('EMB_BACKEND', 'gateway')   # gateway(운영) | ollama(평가 전용)

GATEWAY_URL = os.environ.get('AI_GATEWAY_URL', 'https://ai-gateway.vercel.sh/v1')
# ⚠️ 슬러그 미확정 — 키 발급 후 `python3 scripts/_embed.py --list-models` 로 실제 값을 확인해
#    아래 기본값을 고칠 것. Gateway 는 `provider/model` 형식이고 업스트림은 DeepInfra 다.
GATEWAY_MODEL = os.environ.get('EMB_MODEL_GATEWAY', 'deepinfra/qwen3-embedding-8b')

OLLAMA = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen3-embedding:8b')


def _post_gateway(texts):
    key = os.environ.get('AI_GATEWAY_API_KEY')
    if not key:
        raise RuntimeError(
            'AI_GATEWAY_API_KEY 가 없습니다. Vercel AI Gateway 키를 발급해 .env 에 넣거나,\n'
            '  평가 목적이면 EMB_BACKEND=ollama 로 돌리십시오(⚠️ 서빙 인입에는 쓰지 말 것 — 양자화 차이).')
    r = requests.post(f'{GATEWAY_URL}/embeddings',
                      headers={'Authorization': f'Bearer {key}'},
                      json={'model': GATEWAY_MODEL, 'input': texts}, timeout=300)
    if not r.ok:
        raise RuntimeError(f'gateway {r.status_code}: {r.text[:400]}')
    return [d['embedding'] for d in r.json()['data']]


def _post_ollama(texts):
    r = requests.post(f'{OLLAMA}/api/embed',
                      json={'model': OLLAMA_MODEL, 'input': texts}, timeout=300)
    r.raise_for_status()
    return r.json()['embeddings']


def embed(texts, batch=64, progress=True, backend=None):
    """텍스트 리스트 → MRL 절단 + L2 정규화된 float32 행렬 (n, EMB_DIM)."""
    be = backend or BACKEND
    post = {'gateway': _post_gateway, 'ollama': _post_ollama}[be]
    out = []
    for i in range(0, len(texts), batch):
        out.extend(post(texts[i:i + batch]))
        if progress:
            print(f'  임베딩 {min(i + batch, len(texts))}/{len(texts)} [{be}]', end='\r', flush=True)
    if progress:
        print()
    e = np.array(out, dtype=np.float32)

    # 원본 차원 가드 — 모델이 바뀌면(슬러그 오타·업스트림 교체) 여기서 먼저 걸린다.
    # 이걸 놓치면 다른 모델의 벡터가 조용히 들어가 검색만 나빠진다.
    if e.shape[1] != NATIVE_DIM:
        raise RuntimeError(
            f'임베딩 차원이 {e.shape[1]} 입니다 — qwen3-embedding:8b 는 {NATIVE_DIM} 이어야 합니다.\n'
            f'  모델 슬러그를 확인하십시오(현재 {be}: '
            f'{GATEWAY_MODEL if be == "gateway" else OLLAMA_MODEL}).')

    if EMB_DIM < e.shape[1]:                 # Matryoshka 절단
        e = e[:, :EMB_DIM]
    e /= (np.linalg.norm(e, axis=1, keepdims=True) + 1e-9)   # 절단 후 재정규화
    return e


def embed_query(question, **kw):
    """질의 1건 → (EMB_DIM,) 벡터. 프리픽스는 여기서만 붙인다."""
    return embed([QUERY_INSTRUCT + question], progress=False, **kw)[0]


if __name__ == '__main__':
    import sys
    if '--list-models' in sys.argv:
        key = os.environ.get('AI_GATEWAY_API_KEY')
        if not key:
            sys.exit('AI_GATEWAY_API_KEY 없음 — 키 발급 후 실행하십시오.')
        r = requests.get(f'{GATEWAY_URL}/models', headers={'Authorization': f'Bearer {key}'}, timeout=60)
        r.raise_for_status()
        for m in r.json().get('data', []):
            mid = m.get('id', '')
            if 'embed' in mid.lower() or 'qwen' in mid.lower():
                print(mid)
    else:
        v = embed_query('의료기관 방사선관계종사자의 연간 유효선량한도는?')
        print(f'backend={BACKEND} dim={len(v)} norm={float(np.linalg.norm(v)):.6f}')
