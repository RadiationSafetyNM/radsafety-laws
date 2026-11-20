#!/usr/bin/env python3
"""질문 1건 → 검색 → 답변. **서빙 경로의 기준 구현**(챗 런타임이 TS 로 옮겨 적을 원본).

흐름 (결정 4 — 현행으로 답하고, 바뀔 예정이면 덧붙인다):
  1. 질의 임베딩            _query.expand(용어 병기) → _embed.embed_query  (gemini-embedding-001 · RETRIEVAL_QUERY · 1024)
  2. 현행 검색              RPC lawbot_match(as_of)      — 기준일에 시행 중인 판본만
  3. 시행예정 조회          RPC lawbot_upcoming(chunk_ids) — 회수된 조문의 미래 판본
  4. 생성                   Gemini (결정 1.6) — _prompt.SYS + SYS_UPCOMING
  5. 개정 추적 대장 매칭    data/amendment-tracker.yaml — 공포 전 동향. **생성에 넘기지 않고** 따로 반환
  반환: 답변 · 근거 조문(시행일) · 시행예정 조문(시행일) · 입법 진행 중(확정 아님)

사용:
  python3 scripts/_ask.py "방사선작업종사자의 선량한도는?"
  python3 scripts/_ask.py --as-of 2027-01-01 "..."     # 그 날짜에 물었다면
  python3 scripts/_ask.py --json "..."                 # 런타임이 돌려줄 형태 그대로
전제: .env 의 GEMINI_API_KEY · SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY.
⚠️ 생성은 실결제(질문 1건 ≈ $0.007). AI Studio 프로젝트 `radsafety` 월 상한을 서빙과 공유.
"""
import argparse, json, os, sys
import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _embed import embed_query            # noqa: E402  (.env 로드 포함)
from _prompt import SYS, SYS_UPCOMING     # noqa: E402
from _query import expand                 # noqa: E402

GEN_MODEL = os.environ.get('GEN_MODEL', 'gemini-3.6-flash')
GEMINI_EP = 'https://generativelanguage.googleapis.com/v1beta'
TOPK = 5
CAP = 20000          # 근거 청크 본문 상한 — _rag_answer.py 와 같다(별표 표를 행 중간에서 자르지 않게)
UP_MAX, UP_CAP = 8, 6000   # 시행예정 자료는 덧붙임용이라 근거보다 좁게 준다


def _rpc(name, body):
    url, key = os.environ['SUPABASE_URL'].rstrip('/'), os.environ['SUPABASE_SERVICE_ROLE_KEY']
    r = requests.post(f'{url}/rest/v1/rpc/{name}', json=body, timeout=60,
                      headers={'apikey': key, 'Authorization': f'Bearer {key}'})
    if not r.ok:
        raise RuntimeError(f'{name} {r.status_code}: {r.text[:300]}')
    return r.json()


def disp(row):
    m = row.get('metadata') or {}
    return f"{m.get('law_title', '?')} {m.get('article', '')}{m.get('subunit', '')}".strip()


def kdate(d):
    y, m, dd = d.split('-')
    return f'{y}년 {int(m)}월 {int(dd)}일'


def retrieve(question, as_of=None, k=TOPK):
    vec = '[' + ','.join(f'{x:.7f}' for x in embed_query(expand(question))) + ']'   # 검색에만 용어 병기
    hits = _rpc('lawbot_match', {'query_embedding': vec, 'match_count': k, 'as_of': as_of})
    up = _rpc('lawbot_upcoming', {'chunk_ids': [h['chunk_id'] for h in hits], 'as_of': as_of}) if hits else []
    return hits, up[:UP_MAX]


def build_prompt(question, hits, up, as_of):
    parts = []
    for n, h in enumerate(hits, 1):
        note = f" (시행 {h['effective_from']}" + (f", {h['effective_to']} 삭제 또는 개정 예정" if h.get('effective_to') else '') + ')'
        parts.append(f"[자료 {n}] {disp(h)}{note}\n{h['content'][:CAP]}")
    p = f"{SYS}\n{SYS_UPCOMING}\n\n기준일: {as_of or '오늘'}\n\n[근거 자료]\n" + '\n\n---\n\n'.join(parts)
    if up:
        p += '\n\n[시행예정 자료]\n' + '\n\n---\n\n'.join(
            f"[예정 {n}] {disp(u)} — {kdate(u['effective_from'])}부터 시행\n{u['content'][:UP_CAP]}"
            for n, u in enumerate(up, 1))
    return p + f'\n\n[질문]\n{question}'


def generate(prompt):
    r = requests.post(f'{GEMINI_EP}/models/{GEN_MODEL}:generateContent',
                      headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']},
                      json={'contents': [{'parts': [{'text': prompt}]}],
                            'generationConfig': {'temperature': 0}}, timeout=300)
    if not r.ok:
        raise RuntimeError(f'gemini {r.status_code}: {r.text[:300]}')
    d = r.json()
    cand = (d.get('candidates') or [{}])[0]
    txt = ''.join(p.get('text', '') for p in cand.get('content', {}).get('parts') or []).strip()
    if not txt:
        raise RuntimeError(f'gemini 빈 응답 (finishReason={cand.get("finishReason")})')
    return txt, d.get('usageMetadata', {})


TRACKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'amendment-tracker.yaml')


def pending(question, hits):
    """개정 추적 대장에서 이 질문·근거에 걸리는 열린 항목. 런타임(tracker.ts)과 같은 규칙."""
    items = (yaml.safe_load(open(TRACKER, encoding='utf-8')) or {}).get('items', [])
    got = []
    for it in items:
        if it.get('closed'):
            continue
        by_kw = any(k in question for k in it.get('keywords', []))
        by_src = any((h.get('metadata') or {}).get('law_title') == it['law'] and
                     ((h['metadata'].get('attachment_no') in it.get('targets', [])) or
                      (h['metadata'].get('article') in it.get('targets', [])))
                     for h in hits)
        if by_kw or by_src:
            got.append(it)
    return got


def ask(question, as_of=None):
    hits, up = retrieve(question, as_of)
    answer, usage = generate(build_prompt(question, hits, up, as_of))
    return {
        'question': question, 'as_of': as_of, 'answer': answer,
        'sources': [{'chunk_id': h['chunk_id'], 'title': disp(h), 'effective_from': h['effective_from'],
                     'effective_to': h.get('effective_to'), 'similarity': round(h['similarity'], 3)} for h in hits],
        'upcoming': [{'chunk_id': u['chunk_id'], 'title': disp(u), 'effective_from': u['effective_from']} for u in up],
        'pending': pending(question, hits),
        'usage': {'in': usage.get('promptTokenCount'), 'out': usage.get('candidatesTokenCount')},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('question')
    ap.add_argument('--as-of', help='YYYY-MM-DD (기본: 한국 날짜 오늘)')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    out = ask(a.question, a.as_of)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print(out['answer'])
    print('\n── 검색된 현행 조문')
    for s in out['sources']:
        tail = f" → {s['effective_to']} 변경" if s['effective_to'] else ''
        print(f"  {s['similarity']:.3f} {s['title']} (시행 {s['effective_from']}{tail})")
    if out['upcoming']:
        print('── 시행예정 판본')
        for u in out['upcoming']:
            print(f"  {u['effective_from']} {u['title']}")
    for it in out['pending']:
        print(f"── 입법 진행 중(확정 아님): {it['title']} — {it['current']} → {it['proposed']} "
              f"· {it['status']} · 시행 목표 {it.get('expected_effective', '미정')} · {it['confidence']}")
    print(f"── 토큰 입력 {out['usage']['in']} · 출력 {out['usage']['out']}")


if __name__ == '__main__':
    main()
