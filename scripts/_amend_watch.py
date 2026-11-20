#!/usr/bin/env python3
"""일일 개정 감지 — 법제처 OpenAPI 를 공포일자 범위로 한 번 훑어 워치리스트와 교집합.

주간 CI(update-laws.yml)는 미러(legalize-kr·admrule-kr)를 받아 온 *뒤에* 바뀐 것을 알린다.
이 스크립트는 그 앞단이다 — **법제처가 공포했는데 우리 data/ 가 아직 모르는 개정**을 찾는다.
미러 반영 여부와 무관하게 권위측을 직접 본다.

  _freshness_audit.py 와의 차이: 감사는 워치리스트 법령을 하나씩 조회(17회)해 표를 그린다.
  이쪽은 기간 조회 1~2회(+페이지)로 끝나고, 고시 축도 본다. 매일 돌리는 용도.

방법:
  법령축  lawSearch target=eflaw  ancYd=<from>~<to>   → 법령ID 로 watchlist 와 교집합
          ※ target=law(현행)는 시행예정 개정이 빠진다 — eflaw 가 옳은 축.
          ※ eflaw 는 한 공포본이 시행일마다 여러 행으로 온다 → MST 로 접는다.
  고시축  lawSearch target=admrul prmlYd=<from>~<to>  → 행정규칙ID 로 data/admin-rules 와 교집합
          ※ 고시는 필드명이 다르다(발령일자·행정규칙일련번호).

판정(우리 frontmatter 의 일련번호와 비교):
  NEW   공포(발령)일이 우리보다 늦다, 또는 같은 날인데 일련번호가 더 크다 → 우리가 모르는 개정
  HAVE  일련번호 일치 → 이미 보유
  (그 밖 — 우리 것보다 앞선 공포본 — 은 버린다)

종료 코드:  0 = NEW 없음 / 10 = NEW 있음 / 2 = API 실패(IP 미등록·형식 변경 등)
  ⚠️ API 실패를 '개정 없음'으로 삼키지 않는다. 등록 IP 가 바뀌면 조용히 0건이 되는 게
     이 감시의 최악 실패다 — resultCode 와 총건수 0 을 모두 실패로 본다.

사용:
  python3 _amend_watch.py                 # 최근 14일
  python3 _amend_watch.py --days 30 --format md
  python3 _amend_watch.py --from 20261101 --to 20261120 --format json
전제: 호출 장비의 공인 IP 가 open.law.go.kr 에 등록돼 있어야 한다. OC 는 env MOLEG_OC.
"""
import os, re, sys, json, glob, argparse, datetime
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from _watchlist import members

BASE = 'https://www.law.go.kr/DRF/lawSearch.do'
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PAGE = 100


class ApiError(RuntimeError):
    pass


def fm(path):
    txt = open(path, encoding='utf-8').read()
    m = re.match(r'---\n(.*?)\n---', txt, re.S)
    d = {}
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r'([^:\s][^:]*):\s*(.*)', line)
            if mm:
                d[mm.group(1).strip()] = mm.group(2).strip().strip("'\"")
    return d


def search(oc, target, row_tag, **params):
    """기간 조회 전 페이지를 모아 행(dict) 리스트로. 실패는 ApiError."""
    rows, page, total = [], 1, None
    while True:
        q = dict(OC=oc, target=target, type='XML', display=str(PAGE), page=str(page), **params)
        raw = urllib.request.urlopen(BASE + '?' + urllib.parse.urlencode(q),
                                     timeout=40).read().decode('utf-8', 'ignore')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise ApiError(f'{target}: XML 아님 — ' + re.sub(r'<[^>]+>', ' ', raw)[:160].strip())
        if (root.findtext('resultCode') or '').strip() != '00':
            raise ApiError(f'{target}: ' + re.sub(r'\s+', ' ', ''.join(root.itertext()))[:160].strip())
        total = int(root.findtext('totalCnt') or 0)
        got = root.findall(row_tag)
        rows += [{c.tag: (c.text or '').strip() for c in r} for r in got]
        if not got or len(rows) >= total:
            break
        page += 1
    return rows, total


def watch_laws(oc, rng, lawdir):
    rows, total = search(oc, 'eflaw', 'law', ancYd=rng)
    wl = {(m['law_id'] or '').zfill(6): m for m in members() if (m['law_id'] or '').strip()}
    by_mst = {}
    for r in rows:
        m = wl.get(r.get('법령ID', '').zfill(6))
        if not m:
            continue
        e = by_mst.setdefault(r['법령일련번호'], dict(
            axis='법령', tier=m['tier'], name=r['법령명한글'], serial=r['법령일련번호'],
            pub=r['공포일자'], pub_no=r.get('공포번호', ''), kind=r.get('제개정구분명', ''),
            eff=[], watch=m['watch_articles'], member=m))
        e['eff'].append((r['시행일자'], r.get('현행연혁코드', '')))
    out = []
    for e in by_mst.values():
        m = e.pop('member')
        path = os.path.join(lawdir, f"{m['name']}_{m['type']}.md")
        ours = fm(path) if os.path.exists(path) else {}
        e['ours_serial'] = ours.get('법령MST', '')
        e['ours_pub'] = (ours.get('공포일자', '') or '').replace('-', '')
        e['collected'] = bool(ours)
        e['eff'] = sorted(set(e['eff']))
        out.append(e)
    return out, total


def watch_admrules(oc, rng, admdir):
    rows, total = search(oc, 'admrul', 'admrul', prmlYd=rng)
    ours = {}
    for p in glob.glob(os.path.join(admdir, '*.md')):
        d = fm(p)
        if d.get('행정규칙ID'):
            ours[d['행정규칙ID']] = d
    out = []
    for r in rows:
        d = ours.get(r.get('행정규칙ID', ''))
        if not d:
            continue
        out.append(dict(
            axis='고시', tier='core', name=r['행정규칙명'], serial=r['행정규칙일련번호'],
            pub=r['발령일자'], pub_no=r.get('발령번호', ''), kind=r.get('제개정구분명', ''),
            eff=[(r.get('시행일자', ''), r.get('현행연혁구분', ''))], watch=[],
            ours_serial=d.get('행정규칙일련번호', ''),
            ours_pub=(d.get('발령일자', '') or '').replace('-', ''), collected=True))
    return out, total


def judge(e):
    if e['serial'] == e['ours_serial']:
        return 'HAVE'
    if e['pub'] > e['ours_pub']:
        return 'NEW'
    # 같은 날 여러 공포본이 나온다(일부개정 + 타법개정). 미러는 그중 마지막을 갖는다.
    # 일련번호는 발급 순으로 커지므로, 같은 날짜면 우리 것보다 큰 번호만 새 개정이다.
    # (2026-10-20 약사법·의료법: 같은 날 두 본 — 앞 본을 NEW 로 오판하던 것을 막는다.)
    if e['pub'] == e['ours_pub'] and e['serial'].isdigit() and e['ours_serial'].isdigit() \
            and int(e['serial']) > int(e['ours_serial']):
        return 'NEW'
    return 'OLD'


dfmt = lambda s: f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s) == 8 and s.isdigit() else (s or '-')


def render(items, rng, totals, fmt):
    new = [e for e in items if e['verdict'] == 'NEW']
    have = [e for e in items if e['verdict'] == 'HAVE']
    if fmt == 'json':
        return json.dumps(dict(range=rng, scanned=totals, new=new, have=have),
                          ensure_ascii=False, indent=2)
    L = []
    h = '## ' if fmt == 'md' else ''
    L.append(f"{h}일일 개정 감지 — 공포·발령 {rng.replace('~', ' ~ ')}")
    L.append(f"법제처 조회 {totals['법령']}행(법령)·{totals['고시']}건(고시) 중 "
             f"워치리스트 해당 {len(items)}건 — 미반영 {len(new)} / 보유 {len(have)}")
    L.append('')
    for title, group in (('미반영 개정 (우리 data/ 가 아직 모름)', new), ('이미 보유', have)):
        if not group:
            continue
        L.append(f"{'### ' if fmt == 'md' else '['}{title}{'' if fmt == 'md' else ']'}")
        for e in sorted(group, key=lambda x: (x['pub'], x['name'])):
            eff = ', '.join(f"{dfmt(d)}{'(' + c + ')' if c else ''}" for d, c in e['eff'])
            tag = '' if e['tier'] == 'core' else ' · 주변법(방사선 조 영향 확인 要)'
            L.append(f"- **{e['name']}** {e['kind']} · {e['axis']}{tag}")
            L.append(f"  공포 {dfmt(e['pub'])} 제{e['pub_no']}호 · 시행 {eff}")
            L.append(f"  일련번호 {e['ours_serial'] or '(미수집)'} → {e['serial']}")
            if e['watch']:
                L.append(f"  감시 조항: {'; '.join(e['watch'])}")
        L.append('')
    return '\n'.join(L).rstrip() + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--days', type=int, default=14, help='오늘부터 거슬러 볼 일수(기본 14)')
    ap.add_argument('--from', dest='frm', help='YYYYMMDD (--days 대신)')
    ap.add_argument('--to', help='YYYYMMDD (기본 오늘)')
    ap.add_argument('--format', choices=('text', 'md', 'json'), default='text')
    ap.add_argument('--lawdir', default=os.path.join(ROOT, 'data', 'laws'))
    ap.add_argument('--admdir', default=os.path.join(ROOT, 'data', 'admin-rules'))
    a = ap.parse_args()
    oc = os.environ.get('MOLEG_OC', 'benkorea.ai')
    kst = datetime.timezone(datetime.timedelta(hours=9))
    today = datetime.datetime.now(kst).date()
    to = a.to or today.strftime('%Y%m%d')
    frm = a.frm or (today - datetime.timedelta(days=a.days)).strftime('%Y%m%d')
    rng = f'{frm}~{to}'
    try:
        laws, n_law = watch_laws(oc, rng, a.lawdir)
        adms, n_adm = watch_admrules(oc, rng, a.admdir)
        # 전국에서 2주간 공포 0건은 있을 수 없다 → 0 이면 조회가 깨진 것으로 본다.
        if n_law == 0 or n_adm == 0:
            raise ApiError(f'총건수 0 (법령 {n_law}·고시 {n_adm}) — 기간 {rng} 조회가 비정상')
    except (ApiError, OSError) as ex:
        print(f'⛔ 법제처 API 실패: {ex}\n   등록 IP 확인: curl -s https://api.ipify.org', file=sys.stderr)
        return 2
    items = laws + adms
    for e in items:
        e['verdict'] = judge(e)
    items = [e for e in items if e['verdict'] != 'OLD']
    sys.stdout.write(render(items, rng, {'법령': n_law, '고시': n_adm}, a.format))
    return 10 if any(e['verdict'] == 'NEW' for e in items) else 0


if __name__ == '__main__':
    sys.exit(main())
