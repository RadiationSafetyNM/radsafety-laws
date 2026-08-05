#!/usr/bin/env python3
"""인입 배치 — `data/chunks/law_chunks.jsonl` → 임베딩 → Supabase `lawbot_chunks` upsert.

경계(2026-06-27 §결정1): **데이터·임베딩 인입 = radsafety-laws** / 챗 런타임·UI = radsafety-pwa.
테이블 정의는 `sql/001_lawbot_chunks.sql`(먼저 Supabase SQL Editor 에서 실행해 둘 것).

사용:
    # ① 키 없이 지금 되는 것 — 코퍼스·메타·해시만 검증(임베딩·네트워크 호출 0)
    python3 scripts/_embed_upsert.py --dry-run

    # ② 키 발급 후 소량 시험 (좌표계·업서트 왕복 확인)
    python3 scripts/_embed_upsert.py --limit 50

    # ③ 전량 인입 (변경분만 — 이미 같은 content 인 청크는 임베딩조차 안 한다)
    python3 scripts/_embed_upsert.py

    # ④ 강제 전량 재임베딩 (모델·차원·CAP 을 바꿨을 때만)
    python3 scripts/_embed_upsert.py --force

필요 환경변수(`.env`):
    AI_GATEWAY_API_KEY          Vercel AI Gateway (업스트림 DeepInfra) — 임베딩
    SUPABASE_URL                dev 프로젝트 URL  ⚠️ §결정3 — 라이브 DB 에서 개발 금지
    SUPABASE_SERVICE_ROLE_KEY   쓰기는 service role 만(RLS 상 anon/authenticated 는 읽기 전용)

인입 직후 반드시 `scripts/_lawbot_verify.py` 로 대조 검증할 것 — 로컬 하네스와 같은
recall 이 안 나오면 좌표계가 어긋난 것이고, 이 단계를 건너뛰면 나중에 원인을 못 찾는다.
"""
import argparse, hashlib, json, os, re, sys
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _embed import embed, EMB_CAP, EMB_DIM, BACKEND   # noqa: E402

CHUNKS = 'data/chunks/law_chunks.jsonl'
UPSERT_BATCH = 200      # PostgREST 한 요청당 행 수. 1024차원 float 200행 ≈ 1.6MB.


def load_chunks():
    """평가 하네스와 **같은 규약**으로 코퍼스를 만든다(_rag_eval_ollama.py 의 units 와 동형).
    서빙은 항상 청크 모드 — 구 `RAG_ATT=md` 경로는 평가 전용이라 여기 없다."""
    rows = []
    for line in open(CHUNKS, encoding='utf-8'):
        r = json.loads(line)
        m = r['metadata']
        text = r['content'][:EMB_CAP]
        # 별표 번호는 메타 필터·채점 양쪽에서 쓰이므로 청크 메타에도 평탄화해 둔다.
        if m.get('document_type') == 'attachment':
            bm = re.match(r'별표(\d+)', m.get('attachment_no', ''))
            m = {**m, 'byeol': bm.group(1) if bm else '?'}
        rows.append({
            'chunk_id': r['chunk_id'],
            'content': r['content'],          # 저장은 전문(생성 모델이 읽는 본문)
            '_embed_text': text,              # 임베딩은 CAP 절단본 — 하네스와 동일 조건
            'metadata': {**m, 'content_sha': hashlib.sha256(text.encode()).hexdigest()[:16]},
        })
    return rows


def fetch_existing(url, key):
    """기존 행의 content_sha 를 전부 읽어온다 → 변경분만 재임베딩(개정 주기마다 CI 로 도는 것 전제)."""
    seen, offset, page = {}, 0, 1000
    while True:
        r = requests.get(f'{url}/rest/v1/lawbot_chunks', params={'select': 'chunk_id,metadata'},
                         headers={'apikey': key, 'Authorization': f'Bearer {key}',
                                  'Range-Unit': 'items', 'Range': f'{offset}-{offset + page - 1}'},
                         timeout=120)
        if r.status_code == 404:
            print('⚠ lawbot_chunks 테이블이 없습니다 — sql/001_lawbot_chunks.sql 을 먼저 실행하십시오.')
            return None
        r.raise_for_status()
        batch = r.json()
        for row in batch:
            seen[row['chunk_id']] = (row.get('metadata') or {}).get('content_sha')
        if len(batch) < page:
            return seen
        offset += page


def upsert(url, key, payload):
    r = requests.post(f'{url}/rest/v1/lawbot_chunks', params={'on_conflict': 'chunk_id'},
                      headers={'apikey': key, 'Authorization': f'Bearer {key}',
                               'Content-Type': 'application/json',
                               'Prefer': 'resolution=merge-duplicates,return=minimal'},
                      json=payload, timeout=300)
    if not r.ok:
        raise RuntimeError(f'upsert {r.status_code}: {r.text[:400]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='임베딩·업서트 없이 코퍼스만 검증')
    ap.add_argument('--limit', type=int, default=0, help='앞에서 N개만 처리(시험용)')
    ap.add_argument('--force', action='store_true', help='content_sha 가 같아도 전량 재임베딩')
    a = ap.parse_args()

    rows = load_chunks()
    if a.limit:
        rows = rows[:a.limit]

    kinds = {}
    for r in rows:
        kinds[r['metadata'].get('document_type', '?')] = kinds.get(r['metadata'].get('document_type', '?'), 0) + 1
    capped = sum(1 for r in rows if len(r['content']) > EMB_CAP)
    dup = len(rows) - len({r['chunk_id'] for r in rows})
    print(f'코퍼스 {len(rows)} 청크 · 종류 {kinds}')
    print(f'  임베딩 절단(CAP {EMB_CAP}) 적용 {capped}건 · chunk_id 중복 {dup}건 · 목표차원 {EMB_DIM}')
    if dup:
        sys.exit('⛔ chunk_id 가 중복입니다 — primary key 라 upsert 가 서로를 덮어씁니다. 청킹부터 고칠 것.')

    if a.dry_run:
        s = rows[0]
        print(f'\n[샘플] {s["chunk_id"]}\n  meta={json.dumps(s["metadata"], ensure_ascii=False)[:300]}')
        print(f'  content[:120]={s["content"][:120]!r}')
        print('\ndry-run — 임베딩·업서트는 하지 않았습니다. 실제 인입에는 '
              'AI_GATEWAY_API_KEY · SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY 가 필요합니다.')
        return

    url = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key:
        sys.exit('⛔ SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY 가 필요합니다 (§결정3: dev 프로젝트를 쓸 것).')
    if BACKEND != 'gateway':
        sys.exit(f'⛔ EMB_BACKEND={BACKEND} — 인입은 gateway 여야 합니다. ollama 는 양자화가 달라 '
                 '질의(gateway)와 좌표계가 어긋납니다(평가 하네스 전용).')

    existing = fetch_existing(url, key)
    if existing is None:
        return
    todo = rows if a.force else [r for r in rows
                                 if existing.get(r['chunk_id']) != r['metadata']['content_sha']]
    print(f'기존 {len(existing)}행 · 인입 대상 {len(todo)}행'
          f'{" (--force)" if a.force else " (변경분만)"}')
    if not todo:
        print('변경 없음 — 할 일이 없습니다.')
        return

    vecs = embed([r['_embed_text'] for r in todo])
    done = 0
    for i in range(0, len(todo), UPSERT_BATCH):
        part = todo[i:i + UPSERT_BATCH]
        payload = [{
            'chunk_id': r['chunk_id'], 'content': r['content'], 'metadata': r['metadata'],
            # pgvector 는 PostgREST 를 통과할 때 '[0.1,0.2,...]' 문자열 리터럴로 받아야 한다.
            # JSON 배열로 보내면 타입 캐스팅에서 막힌다.
            'embedding': '[' + ','.join(f'{x:.7f}' for x in vecs[i + j]) + ']',
        } for j, r in enumerate(part)]
        upsert(url, key, payload)
        done += len(part)
        print(f'  upsert {done}/{len(todo)}', end='\r', flush=True)
    print(f'\n✅ 인입 완료 {done}행. 다음: python3 scripts/_lawbot_verify.py (대조 검증 — 건너뛰지 말 것)')


if __name__ == '__main__':
    main()
