#!/usr/bin/env python3
"""대조 검증 — 서빙(Supabase `lawbot_match`)이 로컬 하네스와 **같은 recall** 을 내는지 확인.

인입(`_embed_upsert.py`) 직후 반드시 돌린다. 검색이 어긋나도 에러는 안 난다 — 순위만
나빠진다. 그래서 "인입했으니 됐겠지" 로 넘어가면 나중에 앱이 이상할 때 원인을 좌표계까지
되짚을 수 없다. 여기서 한 번 재현을 확인해 두면 그 이후의 이상은 전부 *다른* 원인이다.

기준선 (로컬 하네스 `_rag_eval_ollama.py`, qwen3-embedding:8b · CAP 1800):
    출처 recall  @1 78% · @3 91%          |  answerable @5 ≈ 90%
같은 평가셋·같은 매처(`_match.py`)로 재므로 숫자가 직접 비교된다.

사용:
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... AI_GATEWAY_API_KEY=... \
        python3 scripts/_lawbot_verify.py            # 전 문항
    python3 scripts/_lawbot_verify.py --show 5       # 문항별 top-5 까지 출력

판정:
    · @1·@3 이 기준선 ±1문항 안 → 좌표계 일치. 서빙 착수해도 된다.
    · 눈에 띄게 낮다 → 인입과 질의가 어긋난 것. 의심 순서는
      ① gateway 모델 슬러그가 다름 ② 절단/재정규화 불일치 ③ CAP 불일치
      ④ 인입이 덜 됨(행 수 확인) ⑤ 질의 프리픽스 누락.
"""
import argparse, os, re, sys
import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _embed import embed_query   # noqa: E402
from _match import norm, match   # noqa: E402

VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
EVAL = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋.yaml'

# 하네스와 동일한 제외 규약 — 기준선과 분모를 맞추기 위한 것이다(임의로 바꾸면 비교 불가).
CORPUS_GAP = {14}       # 수의사법 미수록 — MISS 가 정답(정직성 테스트)
METRIC_EXCLUDE = {15}   # 기대출처가 "2026 범부처 공동개정" — 문서가 아닌 결함 문항
K = [1, 3, 5, 10]


def unit_of(row):
    """RPC 결과 행 → 매처가 먹는 형태. 인입 때 평탄화한 metadata 를 그대로 쓴다."""
    m = row.get('metadata') or {}
    byeol = m.get('byeol', '')
    if not byeol and m.get('attachment_no'):
        bm = re.match(r'별표(\d+)', m['attachment_no'])
        byeol = bm.group(1) if bm else '?'
    return {'law': norm(m.get('law_title', '')), 'art': m.get('article', ''), 'byeol': byeol,
            'disp': f"{m.get('law_title','?')} {m.get('article','')}{m.get('subunit','')}"}


def rpc_match(url, key, vec, k):
    r = requests.post(f'{url}/rest/v1/rpc/lawbot_match',
                      headers={'apikey': key, 'Authorization': f'Bearer {key}',
                               'Content-Type': 'application/json'},
                      json={'query_embedding': '[' + ','.join(f'{x:.7f}' for x in vec) + ']',
                            'match_count': k}, timeout=120)
    if not r.ok:
        raise RuntimeError(f'lawbot_match {r.status_code}: {r.text[:400]}')
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--show', type=int, default=0, help='문항별 top-N 회수 결과 출력')
    a = ap.parse_args()

    url = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
    key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key:
        sys.exit('⛔ SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY 가 필요합니다.')

    cnt = requests.get(f'{url}/rest/v1/lawbot_chunks', params={'select': 'chunk_id'},
                       headers={'apikey': key, 'Authorization': f'Bearer {key}',
                                'Prefer': 'count=exact', 'Range': '0-0'}, timeout=60)
    total = cnt.headers.get('content-range', '?').split('/')[-1]
    print(f'lawbot_chunks 행 수: {total}  (기준: 청크 3,234 — 크게 적으면 인입이 덜 된 것)')

    qs = yaml.safe_load(open(EVAL, encoding='utf-8'))['questions']
    scored = [q for q in qs if q['id'] not in CORPUS_GAP and q['id'] not in METRIC_EXCLUDE]

    hits = {k: 0 for k in K}
    misses = []
    for q in scored:
        rows = rpc_match(url, key, embed_query(q['question']), max(K))
        exp = q.get('expected_sources', [])
        best = next((i + 1 for i, row in enumerate(rows) if match(exp, unit_of(row))), None)
        for k in K:
            hits[k] += 1 if best and best <= k else 0
        if not best:
            misses.append(q['id'])
        if a.show:
            print(f"[Q{q['id']:>2}] {'회수@' + str(best) if best else 'MISS':>8} | {q['question'][:44]}")
            for row in rows[:a.show]:
                mk = '✓' if match(exp, unit_of(row)) else ' '
                print(f"      {mk} {row.get('similarity', 0):.3f} {unit_of(row)['disp'][:58]}")

    n = len(scored)
    print(f'\n── 서빙 recall ({n}문항 · 코퍼스갭 {sorted(CORPUS_GAP)} · 결함 {sorted(METRIC_EXCLUDE)} 제외) ──')
    print('  ' + ' · '.join(f'@{k}={hits[k]}/{n}({round(100 * hits[k] / n)}%)' for k in K))
    print(f'  MISS 문항: {misses}')
    print('\n기준선(로컬 하네스): @1 78% · @3 91%')
    print('  ±1문항 안이면 좌표계 일치. 크게 낮으면 ①모델 슬러그 ②절단/재정규화 '
          '③CAP ④인입 누락 ⑤질의 프리픽스 순으로 의심할 것.')


if __name__ == '__main__':
    main()
