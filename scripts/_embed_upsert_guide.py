#!/usr/bin/env python3
"""지침 인입 — data/chunks/guide_chunks.jsonl → 임베딩 → Supabase `lawbot_guide_chunks` (2026-11-27).

법령 인입(_embed_upsert.py)과 같은 임베딩 규약(_embed.py)을 쓴다. 표가 다르다(sql/002_lawbot_guide.sql) —
지침은 판본 연쇄가 없고 행이 적어, 바뀐 행만 다시 임베딩하고 파일에 없는 행은 지운다.
사용: python3 scripts/_embed_upsert_guide.py [--dry-run] [--force]
"""
import hashlib, json, os, sys
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _embed import embed, EMB_CAP, EMB_DIM   # noqa: E402

CHUNKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'chunks', 'guide_chunks.jsonl')
TABLE = 'lawbot_guide_chunks'


def main():
    dry, force = '--dry-run' in sys.argv, '--force' in sys.argv
    rows = []
    for line in open(CHUNKS, encoding='utf-8'):
        r = json.loads(line)
        text = r['content'][:EMB_CAP]
        rows.append({'chunk_id': r['chunk_id'], 'content': r['content'], '_t': text,
                     # 지문에 metadata 도 넣는다 — 본문은 그대로인데 인용 조문의 개정 여부(cites_changed)만 바뀌는 날이 있다
                     'metadata': {**r['metadata'], 'content_sha': hashlib.sha256(
                         (text + json.dumps(r['metadata'], ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:16]}})
    ids = [r['chunk_id'] for r in rows]
    assert len(ids) == len(set(ids)), 'chunk_id 중복'
    print(f'지침 청크 {len(rows)} · 임베딩 절단 {EMB_CAP}자 초과 {sum(len(r["content"]) > EMB_CAP for r in rows)}건')
    if dry:
        return 0
    url, key = (os.environ.get('SUPABASE_URL') or '').rstrip('/'), os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key:
        sys.exit('SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY 가 필요합니다(.env).')
    h = {'apikey': key, 'Authorization': f'Bearer {key}'}
    r = requests.get(f'{url}/rest/v1/{TABLE}', params={'select': 'chunk_id,metadata->>content_sha'}, headers=h, timeout=60)
    if r.status_code == 404:
        sys.exit(f'⚠ {TABLE} 테이블이 없습니다 — sql/002_lawbot_guide.sql 을 먼저 실행하십시오.')
    r.raise_for_status()
    have = {x['chunk_id']: x['content_sha'] for x in r.json()}
    todo = [x for x in rows if force or have.get(x['chunk_id']) != x['metadata']['content_sha']]
    gone = [c for c in have if c not in set(ids)]
    print(f'DB {len(have)}행 · 새로 넣거나 바뀐 것 {len(todo)} · 파일에 없어 지울 것 {len(gone)}')
    if todo:
        vecs = embed([x['_t'] for x in todo])
        assert vecs.shape[1] == EMB_DIM
        payload = [{'chunk_id': x['chunk_id'], 'content': x['content'], 'metadata': x['metadata'],
                    'embedding': '[' + ','.join(f'{v:.7f}' for v in vec) + ']'} for x, vec in zip(todo, vecs)]
        r = requests.post(f'{url}/rest/v1/{TABLE}', params={'on_conflict': 'chunk_id'}, json=payload, timeout=120,
                          headers={**h, 'Prefer': 'resolution=merge-duplicates,return=minimal'})
        if not r.ok:
            sys.exit(f'upsert 실패 {r.status_code}: {r.text[:300]}')
    for c in gone:
        requests.delete(f'{url}/rest/v1/{TABLE}', params={'chunk_id': f'eq.{c}'}, headers=h, timeout=60).raise_for_status()
    n = requests.get(f'{url}/rest/v1/{TABLE}', params={'select': 'chunk_id'}, headers={**h, 'Prefer': 'count=exact', 'Range': '0-0'}, timeout=60)
    print('완료 · DB 행 수', n.headers.get('content-range', '?').split('/')[-1])
    return 0


if __name__ == '__main__':
    sys.exit(main())
