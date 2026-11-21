#!/usr/bin/env python3
"""고시 시행일 기준 판본 수집 — admrule-kr 커밋 이력 → data/admin-rules-effective/<고시>@<시행일>_<일련번호>.md

결정 4(2026-11-20)의 고시판(2026-11-21). 법령은 _collect_eflaw.py 가 법제처 eflaw API 로
"그 날짜에 시행되는 본문"을 받지만, 그 API 는 등록 IP 가 필요하다. 고시는 다른 길이 있다:
admrule-kr 미러가 **발령마다 커밋 하나**를 남기고(커밋 메시지 "고시: <이름> (<발령번호>)"),
각 판본 본문.md 의 frontmatter 에 시행일자가 있다. 그래서 커밋 이력을 거꾸로 훑으면
IP 없이(GitHub API) 현행본과 시행예정본을 모두 얻는다 — CI 에서도 돈다.

무엇을 받는가 (watchlist.toml [[admrule]] 마다):
  최신 커밋부터 거슬러 올라가며 판본마다 시행일자를 읽는다.
  · 시행일자 > 기준일  → 시행예정본. 전부 받는다.
  · 시행일자 ≤ 기준일  → 처음 만난 것이 현행본. 받고 멈춘다(연혁은 받지 않는다 — 법령과 같은 결정).
  같은 시행일자가 둘 이상이면(정정 재발령 등) 더 최근 커밋 하나만 쓴다.

파일 규약은 data/laws-effective 와 같다 — 지우지도 옮기지도 않고, 현행은 날짜로 정한다
(_build_chunks.py 가 `@시행일_번호` 를 읽어 판본 연쇄를 만든다). 이미 있는 파일은 건너뛴다.

한계: admrule-kr 본문에는 부칙이 없어 **조문별 시행일**(부칙의 "제N조는 …부터 시행")을
알 수 없다. 판본 전체가 frontmatter 시행일자 하나로 움직인다고 본다. 2026-11-21 점검 시점의
26건은 모두 발령일=시행일이고 시행예정본이 없었다.

사용:
  python3 scripts/_collect_admrul_effective.py              # 기준일 = 한국 날짜 오늘
  python3 scripts/_collect_admrul_effective.py --as-of 2027-01-01
환경변수 GITHUB_TOKEN 이 있으면 쓴다(API 한도 60→5000/시간). 종료 코드: 0 정상 / 2 실패 있음.
"""
import argparse, datetime, json, os, re, sys, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _watchlist import admrules      # noqa: E402

REPO = 'legalize-kr/admrule-kr'
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.environ.get('ADMRUL_EFFECTIVE', os.path.join(ROOT, 'data', 'admin-rules-effective'))


def _get(url, accept='application/vnd.github+json'):
    h = {'User-Agent': 'radsafety-laws', 'Accept': accept}
    if os.environ.get('GITHUB_TOKEN'):
        h['Authorization'] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=60).read()


def commits(path):
    q = urllib.parse.urlencode({'path': path, 'per_page': 100})
    return json.loads(_get(f'https://api.github.com/repos/{REPO}/commits?{q}'))


def raw_at(sha, path):
    return _get(f'https://raw.githubusercontent.com/{REPO}/{sha}/' + urllib.parse.quote(path),
                accept='*/*')


def fm(body, key):
    m = re.search(rf"^{key}:\s*'?([^'\n]*)'?\s*$", body, re.M)
    return m.group(1).strip() if m else ''


def main():
    kst = datetime.timezone(datetime.timedelta(hours=9))
    ap = argparse.ArgumentParser()
    ap.add_argument('--as-of', default=datetime.datetime.now(kst).strftime('%Y-%m-%d'))
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    new = keep = fail = 0
    for r in admrules():
        path = f"{r['agency']}/{r['kind']}/{r['name']}/본문.md"
        stem = re.sub(r'[/\\:]', '_', r['name'])      # _collect_admrul.py 의 파일명과 같다
        try:
            seen_dates, got = set(), []
            for c in commits(path):                     # 최신 → 과거
                body = raw_at(c['sha'], path).decode('utf-8')
                ef, sn = fm(body, '시행일자'), fm(body, '행정규칙일련번호')
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', ef) or not sn.isdigit():
                    raise ValueError(f"{c['sha'][:8]} frontmatter 시행일자/일련번호 없음")
                if ef in seen_dates:
                    continue                            # 같은 시행일의 옛 커밋 — 최신 것만
                seen_dates.add(ef)
                got.append((ef, sn, body))
                if ef <= a.as_of:
                    break                               # 현행본을 만났다 — 연혁은 받지 않는다
            if not got or got[-1][0] > a.as_of:
                print(f'  ⚠ {stem}: 현행본 없음(시행예정본만 {len(got)}개)', file=sys.stderr)
            for ef, sn, body in got:
                fn = os.path.join(OUT, f'{stem}@{ef}_{sn}.md')
                if os.path.exists(fn):
                    keep += 1
                    continue
                with open(fn, 'w', encoding='utf-8') as fp:
                    fp.write(body)
                new += 1
                tag = '시행예정' if ef > a.as_of else '현행'
                print(f'  + {stem}@{ef} ({tag})')
        except Exception as ex:
            print(f'  ✗ {stem}: {ex}', file=sys.stderr)
            fail += 1
    print(f'고시 판본: 새로 {new} · 유지 {keep} · 실패 {fail} (기준일 {a.as_of}) → {os.path.relpath(OUT, ROOT)}')
    sys.exit(2 if fail else 0)


if __name__ == '__main__':
    main()
