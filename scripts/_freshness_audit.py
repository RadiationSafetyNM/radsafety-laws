#!/usr/bin/env python3
"""법령 신선도 감사 — law.go.kr OpenAPI(권위) ↔ radsafety-laws frontmatter(우리/legalize-kr) 비교.

목적: "법 개정 공포 후 우리 데이터(=legalize-kr 미러)가 뒤처지는가" 를 권위측(법제처
OpenAPI)으로 직접 검증. legalize-mcp·admrule-kr 은 둘 다 legalize-kr 미러라 독립검증
불가 → law.go.kr OpenAPI 직접 호출만이 권위측. (호출 IP 등록 필요 → CLAUDE.md 참조.)

감시 대상은 watchlist.toml(단일 권위)에서 읽는다 — _watchlist.py 로더 경유. 법령ID 도
watchlist 가 제공하므로 frontmatter 의 법령ID 에 의존하지 않는다. 우리쪽 비교값
(법령MST·공포일자)만 data/laws 의 동반 .md frontmatter 에서 읽는다.

방법(워치리스트 멤버별):
  1. data/laws/<name>_<type>.md frontmatter 의 법령MST·공포일자 읽기.
  2. OpenAPI target=eflaw(시행일법령) 로 watchlist 의 법령ID 의 **모든 공포 버전** 조회.
  3. 그중 *최신 공포일자* 버전(law.go.kr 가 아는 가장 최근 개정)을 우리 것과 비교.
       - 우리 MST == 최신 공포 MST           → SYNC   (우리가 law.go.kr 최신을 보유)
       - 최신 공포일자 > 우리 공포일자          → STALE  (진짜 상류 지연 — 우리가 놓친 개정)
       - lawService MST=우리MST 가 '없습니다'   → MISSING(우리 MST 가 권위측에 부재 — 이상)
  ※ '현행' 비교가 아니라 '최신 공포' 비교다. lawSearch 현행은 시행 중 버전만 줘서
    시행예정(공포됐으나 미시행) 개정을 STALE 로 오판한다 — eflaw 가 옳은 축.

사용: python3 _freshness_audit.py <OC> [data/laws 경로(기본 data/laws)]
전제: 호출 PC 의 공인 IP 가 open.law.go.kr 에 등록돼야 함.
"""
import sys, os, re, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from _watchlist import members

OC = sys.argv[1]
LAWDIR = sys.argv[2] if len(sys.argv) > 2 else 'data/laws'

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

def api(endpoint, **p):
    url = f'https://www.law.go.kr/DRF/{endpoint}?' + urllib.parse.urlencode(p)
    return urllib.request.urlopen(url, timeout=40).read().decode('utf-8', 'ignore')

def latest_promulgated(name, law_id):
    """eflaw 에서 law_id 의 최신 공포 버전 (mst, 공포일자, 시행일자, 연혁코드) 반환."""
    raw = api('lawSearch.do', OC=OC, target='eflaw', type='XML', display='100',
              query=re.sub(r'\s+', '', name))
    if '<resultCode>00' not in raw:
        return None, re.sub(r'<[^>]+>', ' ', raw)[:120]
    best = None
    for law in ET.fromstring(raw).findall('law'):
        if (law.findtext('법령ID', '') or '').strip() != law_id:
            continue
        pub = (law.findtext('공포일자', '') or '').strip()
        rec = dict(mst=(law.findtext('법령일련번호', '') or '').strip(), pub=pub,
                   eff=(law.findtext('시행일자', '') or '').strip(),
                   code=(law.findtext('현행연혁코드', '') or '').strip())
        if best is None or pub > best['pub']:
            best = rec
    return best, None

def api_has_mst(mst):
    raw = api('lawService.do', OC=OC, target='law', type='XML', MST=mst)
    return '일치하는 법령이 없습니다' not in raw and '<법령' in raw

dfmt = lambda s: f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s) == 8 and s.isdigit() else (s or '-')

print(f"{'법령':<32}{'우리MST':>8}{'API최신MST':>9}{'우리공포':>12}{'API최신공포':>12}  판정")
print('-' * 100)
sync = stale = other = 0
for m in members():
    label = f"{m['name']} {m['type']}".replace(' 법률', '')
    path = os.path.join(LAWDIR, f"{m['name']}_{m['type']}.md")
    law_id = (m['law_id'] or '').zfill(6)
    if not os.path.exists(path):
        print(f"{label[:31]:<32}{'-':>8}{'-':>9}{'-':>12}{'-':>12}  파일없음 ✗ {path}")
        other += 1
        continue
    d = fm(path)
    our_mst = d.get('법령MST', '')
    our_pub = (d.get('공포일자', '') or '').replace('-', '')
    # eflaw 조회는 법령 전체명으로 (act명만 쓰면 가족 전 시행일행이 display 한도를 넘겨 truncate).
    # 시행령·시행규칙은 act명 뒤에 종류를 붙여야 정확한 법령명, 부령은 name 자체가 전체명.
    qname = f"{m['name']} {m['type']}" if m['type'] in ('시행령', '시행규칙') else m['name']
    best, err = latest_promulgated(qname, law_id)
    if best is None:
        verdict, api_mst, api_pub = f'조회실패 {err or "ID미매칭"}', '?', ''
        other += 1
    elif best['mst'] == our_mst:
        verdict, api_mst, api_pub = 'SYNC ✓', best['mst'], best['pub']
        sync += 1
    elif best['pub'] > our_pub:
        tag = f"({best['code']})" if best['code'] else ''
        verdict = f"STALE ⚠ API 최신 미보유 {tag}"
        api_mst, api_pub = best['mst'], best['pub']
        stale += 1
    else:
        # API 최신이 우리보다 오래됨 → 우리 MST 가 권위측에 실재하는지 확인
        has = api_has_mst(our_mst)
        verdict = 'SYNC ✓ (우리=시행예정 최신)' if has else 'MISSING ✗ 우리MST 부재'
        api_mst, api_pub = best['mst'], best['pub']
        if has: sync += 1
        else: other += 1
    print(f"{label[:31]:<32}{our_mst:>8}{api_mst:>9}{dfmt(our_pub):>12}{dfmt(api_pub):>12}  {verdict}")
print('-' * 100)
print(f"합계 {sync+stale+other}건 — SYNC {sync} / STALE {stale} / 기타 {other}")
