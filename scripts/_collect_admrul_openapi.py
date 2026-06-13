#!/usr/bin/env python3
"""법제처 OpenAPI(admrul) → 의료 방사선안전 삼원화 행정규칙(고시) 수집.

검색(방사선 키워드) → 소관 필터(원자력안전위원회·질병관리청·보건복지부) → 현행 일련번호 →
본문(조문·부칙) → markdown. 일련번호는 개정 시 바뀌므로 매번 검색으로 현행을 잡는다.

소관별 정책:
  - 원자력안전위원회(핵의학·방종): 비의료 규칙(원전 등) 다수 → MED 키워드로 의료·방사선안전만 추림.
  - 질병관리청·보건복지부(의료법/영상의학): 의료방사선 전담 → 방사선 검색 게이트만으로 채택(MED 생략).
    (MED 정규식은 '방사선 안전관리'[공백]만 있어 '방사선안전관리규정'[무공백]을 놓치므로 의료 축엔 부적용)

사용: python3 _collect_admrul.py <OC> <대상폴더>
전제: 호출 IP 가 법제처에 등록돼야 함 (OC 만으로는 불가 — 실측). 미등록 시 '검증 실패'.
"""
import sys, os, re, urllib.request, urllib.parse, xml.etree.ElementTree as ET

OC, DST = sys.argv[1], sys.argv[2]
KW = ['원자력', '방사선', '방사성', '방사능', '원자력안전']
MED = re.compile(r'의료|진단|피폭|방사선방호|방사선 안전관리|방사선안전보고서|동위원소|'
                 r'생활주변|방사선원|방사선기기|방사선발생장치|업무대행|누설점검|'
                 r'보안관리|판매자|면허|우주방사선|비상진료')
# 의료 방사선안전 삼원화 소관 부처: 원안위(핵의학·방종) + 질병청·복지부(의료법/영상의학)
OG = {'원자력안전위원회', '질병관리청', '보건복지부'}

def get(endpoint, **params):
    url = f'https://www.law.go.kr/DRF/{endpoint}?' + urllib.parse.urlencode(params)
    return urllib.request.urlopen(url, timeout=40).read().decode('utf-8', 'ignore')

# 1) 검색 → 삼원화 소관(원안위·질병청·복지부) + 방사선 게이트 → {명: 현행 일련번호}
seen = {}
for kw in KW:
    x = get('lawSearch.do', OC=OC, target='admrul', type='XML', display='100', query=kw)
    if '검증에 실패' in x or '미신청' in x:
        sys.exit(f'권한/IP 오류 (검색 {kw}): {re.sub(r"<[^>]+>"," ",x)[:120]}')
    for a in ET.fromstring(x).findall('admrul'):
        og = (a.findtext('소관부처명', '') or '').strip()
        nm = (a.findtext('행정규칙명', '') or '').strip()
        sn = (a.findtext('행정규칙일련번호', '') or '').strip()
        if og not in OG:
            continue
        # 원안위만 MED 로 의료·방사선안전 추림. 질병청·복지부는 소관 자체가 의료 게이트.
        if og == '원자력안전위원회' and not MED.search(nm):
            continue
        seen[nm] = sn

# 2) 본문 수집 → markdown
os.makedirs(DST, exist_ok=True)
ok = 0
for nm, sn in sorted(seen.items()):
    x = get('lawService.do', OC=OC, target='admrul', type='XML', ID=sn)
    root = ET.fromstring(x); info = root.find('행정규칙기본정보')
    g = lambda t: (info.findtext(t, '') or '').strip() if info is not None else ''
    arts = [(e.text or '').strip() for e in root.findall('조문내용') if (e.text or '').strip()]
    bu = root.find('부칙')
    bc = '\n'.join((e.text or '').strip() for e in bu.findall('부칙내용')) if bu is not None else ''
    fm = (f"---\n행정규칙명: {g('행정규칙명')}\n종류: {g('행정규칙종류')}\n"
          f"발령번호: {g('발령번호')}\n발령일자: {g('발령일자')}\n시행일자: {g('시행일자')}\n"
          f"소관부처: {g('소관부처명')} ({g('담당부서기관명')})\n행정규칙일련번호: {sn}\n"
          f"현행여부: {g('현행여부')}\n출처: 법제처 국가법령정보 OpenAPI (admrul)\n---\n\n# {g('행정규칙명')}\n\n")
    body = '\n\n'.join(arts) + ('\n\n## 부칙\n\n' + bc if bc else '')
    open(os.path.join(DST, re.sub(r'[/\\:]', '_', nm) + '.md'), 'w', encoding='utf-8').write(fm + body + '\n')
    ok += 1
print(f'행정규칙 수집 완료: {ok}건 → {DST}')
