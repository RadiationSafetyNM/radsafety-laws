#!/usr/bin/env python3
"""평가셋 정답 재확인 — 시행일 판본 기준으로 정답이 바뀐 문항 찾기 (2026-11-21 신설).

왜: 평가셋(lawbot-평가셋.yaml)의 ground_truth·answer_keys 는 판본 구분이 없던 미러 기준으로
쓰였다. DB 가 조문 판본(effective_from/to)을 갖게 되면서 같은 조문이라도 "지난 판본·현행·
시행예정"이 갈린다. 검색 recall(_lawbot_verify.py)은 출처 *문서* 만 보므로 정답 *값* 이
바뀐 걸 못 잡는다. 여기서는 문항마다 기대출처에 해당하는 판본 행을 모아, 정답키가
각 판본 본문에 실재하는지 본다. LLM 호출 없음(DB 읽기만).

판정:
  OK        현행 판본에 정답키 전부 있음 (지난 판본이 있어도 값은 그대로)
  CHANGED   지난 판본엔 있었는데 현행엔 없음 → 정답 갱신 필요
            (⚠️ 2026-11-21 현재 DB 는 현행·시행예정만 싣고 지난 판본은 0행 — 이 판정은 지난 판본을
             싣게 될 때를 위한 자리. 지금은 '현행에 키가 있나'와 UPCOMING 이 실질 검사다)
  MISSING   현행 판본에 키가 없음(지난 판본에도 없음) → 키·출처 결함 의심, 사람이 확인
  NO-SOURCE 기대출처에 해당하는 현행 행이 없음(삭제·이름 변경)
  NO-KEYS   answer_keys 없음 — 값 비교 불가
  + UPCOMING  시행예정 판본에서 키가 사라짐 → 그 날짜에 정답이 바뀐다

사용:
  python3 scripts/_eval_recheck.py                  # 오늘(한국) 기준
  python3 scripts/_eval_recheck.py --as-of 2027-01-01
"""
import argparse, datetime as dt, os, re, sys
import requests
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _match import norm, match          # noqa: E402
from _answer_keys import has_all        # noqa: E402

VAULT = os.path.expanduser('~/projects/2nd-brain-vault')
EVAL = f'{VAULT}/knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/lawbot-평가셋.yaml'


def unit_of(row):
    """_lawbot_verify.unit_of 와 같다(매처 입력)."""
    m = row.get('metadata') or {}
    byeol = m.get('byeol', '')
    if not byeol and m.get('attachment_no'):
        bm = re.match(r'별표(\d+)', m['attachment_no'])
        byeol = bm.group(1) if bm else '?'
    return {'law': norm(m.get('law_title', '')), 'art': m.get('article', ''), 'byeol': byeol}


def fetch_all(url, key):
    rows, off = [], 0
    while True:
        r = requests.get(f'{url}/rest/v1/lawbot_chunks', timeout=120,
                         params={'select': 'chunk_id,metadata,content,effective_from,effective_to',
                                 'order': 'chunk_id,effective_from', 'offset': off, 'limit': 1000},
                         headers={'apikey': key, 'Authorization': f'Bearer {key}'})
        r.raise_for_status()
        b = r.json()
        rows += b
        if len(b) < 1000:
            return rows
        off += 1000


def state(row, as_of):
    if row['effective_from'] > as_of:
        return 'future'
    if row.get('effective_to') and row['effective_to'] <= as_of:
        return 'past'
    return 'current'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--as-of', default=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime('%Y-%m-%d'))
    a = ap.parse_args()
    url = os.environ['SUPABASE_URL'].rstrip('/')
    key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
    rows = fetch_all(url, key)
    for r in rows:
        r['_u'] = unit_of(r)
    qs = yaml.safe_load(open(EVAL, encoding='utf-8'))['questions']
    print(f'기준일 {a.as_of} · DB {len(rows)}행 · 평가셋 {len(qs)}문항\n')

    tally, notes = {}, []
    for q in qs:
        exp, keys = q.get('expected_sources') or [], q.get('answer_keys') or []
        hit = [r for r in rows if exp and match(exp, r['_u'])]
        by = {s: [r for r in hit if state(r, a.as_of) == s] for s in ('past', 'current', 'future')}
        txt = {s: '\n'.join(r['content'] for r in v) for s, v in by.items()}
        if not exp or not by['current']:
            v = 'NO-SOURCE'
        elif not keys:
            v = 'NO-KEYS'
        elif has_all(txt['current'], keys):
            v = 'OK'
        elif by['past'] and has_all(txt['past'], keys):
            v = 'CHANGED'
        else:
            v = 'MISSING'
        up = ''
        if keys and by['future'] and v == 'OK':
            # 시행예정 판본이 현행을 대체하면 그 본문으로 다시 본다(바뀌지 않는 행은 현행 유지)
            fut_ids = {r['chunk_id'] for r in by['future']}
            after = [r for r in by['current'] if r['chunk_id'] not in fut_ids] + by['future']
            if not has_all('\n'.join(r['content'] for r in after), keys):
                up = f" + UPCOMING({min(r['effective_from'] for r in by['future'])})"
        tally[v] = tally.get(v, 0) + 1
        ver = f"판본 지난{len(by['past'])}·현행{len(by['current'])}·예정{len(by['future'])}"
        line = f"[Q{q['id']:>2}] {v:<9}{up} | {ver} | {q['question'][:40]}"
        if v != 'OK' or up or by['past'] or by['future']:
            notes.append(line)
    for n in notes:
        print(n)
    print('\n── 합계: ' + ' · '.join(f'{k} {v}' for k, v in sorted(tally.items())))
    print('   (OK 이면서 판본 변동이 없는 문항은 생략)')


if __name__ == '__main__':
    main()
