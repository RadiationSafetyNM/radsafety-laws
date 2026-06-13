#!/usr/bin/env python3
"""admrule-kr(GitHub 미러) → 의료 방사선안전 삼원화 행정규칙(고시·예규) raw fetch.

구 OpenAPI 판(_collect_admrul_openapi.py)을 대체. 법제처 OpenAPI 는 호출 IP 등록이
필요했으나(고정 IP·Oracle VM 부담), admrule-kr 가 같은 데이터를 markdown+frontmatter+
git 으로 미러링하므로 GitHub 에서 raw fetch 한다 → **IP 무관, CI 자동화 가능**.
(admrule-kr 출처도 법제처 OpenAPI 이므로 권위 동일.)

소관 부처 디렉토리에서 방사선·의료 키워드 행정규칙만 골라 본문.md 를 받는다.
사용: python3 _collect_admrul.py [대상폴더(기본 data/admin-rules)]
환경: GITHUB_TOKEN(선택) — 없으면 60 req/hr, 있으면 5000. 디렉토리 listing 만 API,
      본문은 raw.githubusercontent(CDN, rate limit 무관)라 토큰 거의 불필요.
"""
import sys, os, re, json, urllib.request, urllib.parse

DST = sys.argv[1] if len(sys.argv) > 1 else 'data/admin-rules'
REPO = 'legalize-kr/admrule-kr'

# 의료 방사선안전 삼원화 소관 행정규칙 디렉토리 (원안위=핵의학·방종, 질병청=의료법/영상의학)
DIRS = [
    '국무총리/원자력안전위원회/고시',
    '보건복지부/질병관리청/고시',
    '보건복지부/질병관리청/예규',
]
# 의료 방사선 스코프 필터 (2단):
#  CORE = 방사선 핵심어 필수 → 감염병 '진단/의료' 오탐 차단 (질병청 디렉토리는 감염병 규칙 다수).
#  NEG  = 방사선이지만 *의료 무관*(원전·방사성폐기물 산업) 제외 → 핵의학·방종·영상의학에 집중.
CORE = re.compile(r'방사선|방사성|동위원소|피폭|선량|핵종|방사능')
NEG = re.compile(r'폐기물|발전용원자로|원자로|처분시설|운반용기|운송선박|소각|'
                 r'심층처분|천층처분|배출계획서|우주방사선|원자력사업자|원자력이용시설')
def wanted(nm):
    return bool(CORE.search(nm)) and not NEG.search(nm)
REV = re.compile(r'_\d{4}-\d+$')   # 개정 스냅샷 접미(_2026-256 등) → 현행(무접미)만

TOKEN = os.environ.get('GITHUB_TOKEN', '')

def api(path):
    url = f'https://api.github.com/repos/{REPO}/contents/' + urllib.parse.quote(path)
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                               'User-Agent': 'radsafety-laws'})
    if TOKEN:
        req.add_header('Authorization', f'Bearer {TOKEN}')
    return json.load(urllib.request.urlopen(req, timeout=40))

def raw(path):
    url = f'https://raw.githubusercontent.com/{REPO}/main/' + urllib.parse.quote(path)
    req = urllib.request.Request(url, headers={'User-Agent': 'radsafety-laws'})
    return urllib.request.urlopen(req, timeout=40).read()

os.makedirs(DST, exist_ok=True)
ok = 0; skip = 0
for d in DIRS:
    try:
        entries = api(d)
    except Exception as e:
        print(f'  ! 디렉토리 skip {d}: {e}', file=sys.stderr); continue
    for e in entries:
        nm = e.get('name', '')
        if e.get('type') != 'dir':
            continue
        if REV.search(nm):           # 개정 스냅샷 접미 제외
            continue
        if not wanted(nm):           # CORE 미충족(감염병 등) 또는 NEG(원전·폐기물) 제외
            skip += 1; continue
        try:
            body = raw(f'{d}/{nm}/본문.md')
        except Exception as ex:
            print(f'  ✗ {nm}: {ex}', file=sys.stderr); continue
        fn = re.sub(r'[/\\:]', '_', nm) + '.md'
        with open(os.path.join(DST, fn), 'wb') as fp:
            fp.write(body)
        ok += 1
        print(f'  ✓ {nm}')

print(f'고시·예규 수집(admrule-kr): {ok}건 → {DST} (방사선무관 {skip}건 제외)')
