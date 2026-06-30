#!/usr/bin/env python3
"""admrule-kr(GitHub 미러) → watchlist.toml [[admrule]] *명시 목록* 의 고시·예규·훈령 raw fetch.

구 버전은 소관 디렉토리에서 방사선 키워드로 sweep 했으나, 고시 선별에서 키워드는
동음이의(방호=경비·소방호스, 방재=재난·지방"재"정)로 노이즈가 구조적으로 커 폐기했다.
대신 watchlist.toml 의 명시 [[admrule]] 목록(단일 권위·사람 큐레이션)을 읽어 그것만 받는다.
(키워드는 후보 *발견*에만 보조로 쓰고, 최종 선별은 명시 목록이다. → docs/watchlist-classification.md)

admrule-kr 출처도 법제처 OpenAPI 이므로 권위 동일. raw fetch 라 IP 무관·CI 자동화 가능.
경로 = <agency>/<kind>/<name>/본문.md
사용: python3 _collect_admrul.py [대상폴더(기본 data/admin-rules)]
"""
import sys, os, re, urllib.request, urllib.parse
from _watchlist import admrules

DST = sys.argv[1] if len(sys.argv) > 1 else 'data/admin-rules'
REPO = 'legalize-kr/admrule-kr'


def raw(path):
    url = f'https://raw.githubusercontent.com/{REPO}/main/' + urllib.parse.quote(path)
    req = urllib.request.Request(url, headers={'User-Agent': 'radsafety-laws'})
    return urllib.request.urlopen(req, timeout=40).read()


os.makedirs(DST, exist_ok=True)
ok = 0
fail = 0
for a in admrules():
    path = f"{a['agency']}/{a['kind']}/{a['name']}/본문.md"
    try:
        body = raw(path)
    except Exception as ex:
        print(f'  ✗ [{a["kind"]}] {a["name"]}: {ex}', file=sys.stderr)
        fail += 1
        continue
    fn = re.sub(r'[/\\:]', '_', a['name']) + '.md'
    with open(os.path.join(DST, fn), 'wb') as fp:
        fp.write(body)
    ok += 1
    print(f'  ✓ [{a["kind"]}] {a["name"]}')

print(f'고시·예규·훈령 수집(admrule-kr, 명시목록): {ok}건 성공, {fail}건 실패 → {DST}')
sys.exit(1 if fail else 0)
