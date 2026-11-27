#!/usr/bin/env python3
"""새 고시 후보 감지 — 감시 목록(data/admin-rules)에 없는데 제목이 방사선 분야인 행정규칙을 찾는다.

감시 목록은 사람이 고른 명시 목록이라 **새로 제정되는 고시는 잡지 못한다**(2026-11-27 Dr. Ben 지적).
_amend_watch.py 가 이미 받는 "기간 내 발령 행정규칙 전체"에서 목록 밖의 것을 버리기 전에 제목으로 한 번 거른다.
편입은 자동이 아니다 — 후보를 data/admrul-candidates.json 에 적어 두면 푸시 때 워크플로
(notify-candidates.yml)가 텔레그램으로 알리고, 넣을지는 Dr. Ben 이 정한다(목록 = watchlist.toml).

후보 규칙(둘 중 하나):
  ① 제목에 방사선 낱말(KW)     — '원자력'은 뺀다(원안위 내부 훈령·원전 고시가 연 40건 가까이 걸린다)
  ② 원자력안전위원회 '고시'의 제정 — 제목에 낱말이 없어도 본다(연 몇 건)
실측(2025-11-27~2026-11-27): 발령 7,021건 중 제목 낱말 46건(목록 4건), 의료 해당 신규 0건.

한 번 적힌 ID 는 다시 알리지 않는다(status: new → 사람이 dismissed/added 로 바꿔도 되고 그대로 둬도 된다).
종료 코드: 0 = 정상(새 후보 유무와 무관) / 2 = API 실패.
사용: python3 _admrul_new_watch.py [--days 14] [--dry-run]
전제: 등록 IP(일일 수집 서버). OC 는 env MOLEG_OC.
"""
import os, sys, json, glob, argparse, datetime
from _amend_watch import search, fm, ApiError, ROOT

OUT = os.path.join(ROOT, 'data', 'admrul-candidates.json')
KW = ('방사선', '방사성', '방사능', '핵의학', '피폭', '선량', '동위원소', '특수의료장비', '엑스선', '진단용')


def is_candidate(r):
    name = r.get('행정규칙명', '')
    if any(k in name for k in KW):
        return '제목 낱말'
    if ('원자력안전위원회' in r.get('소관부처명', '') and r.get('행정규칙종류') == '고시'
            and r.get('제개정구분명') in ('제정', '폐지제정')):
        return '원안위 고시 제정'
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    oc = os.environ.get('MOLEG_OC', 'benkorea.ai')
    today = datetime.date.today()
    rng = f"{(today - datetime.timedelta(days=a.days)):%Y%m%d}~{today:%Y%m%d}"
    try:
        rows, total = search(oc, 'admrul', 'admrul', prmlYd=rng)
    except ApiError as e:
        print(f'⛔ 새 고시 후보 조회 실패 — {e}', file=sys.stderr)
        return 2
    ours = {fm(p).get('행정규칙ID') for p in glob.glob(os.path.join(ROOT, 'data', 'admin-rules', '*.md'))}
    data = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {'items': []}
    seen = {it['id'] for it in data['items']}
    new = []
    for r in sorted(rows, key=lambda r: (r.get('발령일자', ''), r.get('행정규칙명', ''))):
        rid = r.get('행정규칙ID', '')
        why = is_candidate(r)
        if not rid or not why or rid in ours or rid in seen:
            continue
        seen.add(rid)
        d = r.get('발령일자', '')
        new.append({
            'id': rid, 'name': r.get('행정규칙명', ''), 'agency': r.get('소관부처명', ''),
            'type': r.get('행정규칙종류', ''), 'action': r.get('제개정구분명', ''),
            'issued': f'{d[:4]}-{d[4:6]}-{d[6:]}' if len(d) == 8 else d,
            'serial': r.get('행정규칙일련번호', ''), 'why': why,
            'link': f"https://www.law.go.kr/admRulLsInfoP.do?admRulSeq={r.get('행정규칙일련번호', '')}",
            'first_seen': f'{today:%Y-%m-%d}', 'status': 'new',
        })
    print(f'새 고시 후보 {len(new)}건 (기간 {rng} · 발령 {total}건 · 감시 목록 {len(ours)}건)')
    for it in new:
        print(f"  {it['issued']} {it['agency']} {it['type']} {it['action']} — {it['name']}")
    if new and not a.dry_run:
        data['items'] += new
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
