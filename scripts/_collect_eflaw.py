#!/usr/bin/env python3
"""시행일 기준 판본 수집 — 법제처 API 직접 → data/laws-effective/<법령>_<종류>@<시행일>.md

결정 4(2026-11-20): 법령마다 **현행본과 시행예정본을 따로** 보관하고, 조문마다 자기 시행일을
갖게 한다. 그 원천이 이 폴더다.

왜 미러(data/laws)로는 안 되는가:
  미러는 `target=law&MST=` 본, 즉 "가장 최근에 공포된 판본"을 준다.
  ① 아직 시행 전인 미래 판본이 현행처럼 들어온다(2026-11-20: 법률 4건이 2027 시행분).
  ② 한 공포본에 시행일이 여럿이면 그 본문은 **어느 날짜의 실제 상태와도 다르다**
     (의료기기법 MST 291321: 시행일 셋. MST 본은 세 스냅샷 어느 것과도 10여 조가 다름).
  법제처는 `target=eflaw&MST=&efYd=` 로 **그 날짜에 시행되는 본문**을 준다 — 이것만이 옳은 축이다.

무엇을 받는가 (워치리스트 법령마다):
  lawSearch target=eflaw 를 법령ID 로 걸러 `현행` 1행 + `시행예정` 전 행.
  각 행 = (MST, 시행일) = 스냅샷 하나. 연혁(과거)은 받지 않는다(Dr. Ben 결정 — 현행과 미래에 집중).

파일은 **지우지도 옮기지도 않는다.** "지금 현행이 어느 것인가"는 폴더가 아니라 날짜로 정한다
(시행일 ≤ 오늘 인 것 중 가장 늦은 것). 그래서 시행일 0시에 돌아야 하는 작업이 없다.
이미 받은 스냅샷은 건너뛴다(같은 MST·시행일) — 매일 돌려도 새 것만 받는다.

변환은 _pipeline.py (legalize-pipeline 판본 고정 차용). 직접 파싱하지 않는다.

사용:
  python3 _collect_eflaw.py                  # 수집 (새 스냅샷만)
  python3 _collect_eflaw.py --check-mirror   # 수집 없이, 미러(data/laws)와 대조만
  python3 _collect_eflaw.py --force          # 있는 것도 다시 받기
종료 코드: 0 정상 / 2 API 실패. 전제: 등록 IP + env MOLEG_OC.
"""
import os, re, sys, glob, argparse, datetime
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from _watchlist import members
import _pipeline

DRF = 'https://www.law.go.kr/DRF'
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'data', 'laws-effective')
MIRROR = os.path.join(ROOT, 'data', 'laws')
OC = os.environ.get('MOLEG_OC', 'benkorea.ai')


class ApiError(RuntimeError):
    pass


def _get(endpoint, **p):
    url = f'{DRF}/{endpoint}?' + urllib.parse.urlencode(dict(OC=OC, type='XML', **p))
    return urllib.request.urlopen(url, timeout=60).read()


def snapshots(member):
    """[(mst, efYd, code, pub, kind)] — 현행 1 + 시행예정 전부, 시행일 오름차순."""
    qname = f"{member['name']} {member['type']}" if member['type'] in ('시행령', '시행규칙') else member['name']
    law_id = member['law_id'].zfill(6)
    rows, page = [], 1
    while True:
        raw = _get('lawSearch.do', target='eflaw', display='100', page=str(page),
                   query=re.sub(r'\s+', '', qname))
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise ApiError(re.sub(r'<[^>]+>', ' ', raw.decode('utf-8', 'ignore'))[:160].strip())
        if (root.findtext('resultCode') or '').strip() != '00':
            raise ApiError(re.sub(r'\s+', ' ', ''.join(root.itertext()))[:160].strip())
        got = root.findall('law')
        rows += got
        if not got or page * 100 >= int(root.findtext('totalCnt') or 0):
            break
        page += 1
    out = set()
    for r in rows:
        if (r.findtext('법령ID') or '').strip().zfill(6) != law_id:
            continue
        code = (r.findtext('현행연혁코드') or '').strip()
        if code in ('현행', '시행예정'):
            out.add(((r.findtext('법령일련번호') or '').strip(), (r.findtext('시행일자') or '').strip(),
                     code, (r.findtext('공포일자') or '').strip(), (r.findtext('제개정구분명') or '').strip()))
    if not any(s[2] == '현행' for s in out):
        raise ApiError(f'{qname}: 현행 행이 없다 (법령ID {law_id}) — 조회명·ID 확인')
    # 같은 시행일에 MST 가 여럿 걸리는 경우가 있다(의료기기법 2027-12-31: 세 공포본).
    # 실측(2026-11-20): 본문이 서로 완전히 같다 — 법제처는 MST 와 무관하게 '그 날짜의 통합 본문'을
    # 준다. 그래서 시행일당 하나(가장 최근 공포본)만 남긴다.
    by_date = {}
    for s in sorted(out, key=lambda s: (s[1], int(s[0]))):
        by_date[s[1]] = s
    return list(by_date.values())


def fetch_markdown(mst, efyd):
    raw = _get('lawService.do', target='eflaw', MST=mst, efYd=efyd)
    if b'<\xec\xa1\xb0\xeb\xac\xb8\xeb\x8b\xa8\xec\x9c\x84' not in raw:   # <조문단위
        raise ApiError(f'MST {mst} efYd {efyd}: 본문 없음 — ' +
                       re.sub(r'<[^>]+>', ' ', raw.decode('utf-8', 'ignore'))[:120].strip())
    return _pipeline.law_xml_to_markdown(raw, mst)


def fname(member, efyd, mst):
    d = f'{efyd[:4]}-{efyd[4:6]}-{efyd[6:8]}'
    return f"{member['name']}_{member['type']}@{d}_{mst}.md"


def body_only(md):
    return re.sub(r'\A---\n.*?\n---\n', '', md, flags=re.S)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check-mirror', action='store_true')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y%m%d')
    new = kept = 0
    try:
        for m in members():
            if not (m['law_id'] or '').strip():
                continue
            label = f"{m['name']} {m['type']}"
            for mst, efyd, code, pub, kind in snapshots(m):
                path = os.path.join(OUT, fname(m, efyd, mst))
                state = '현행' if efyd <= today else '시행예정'
                if a.check_mirror:
                    mp = os.path.join(MIRROR, f"{m['name']}_{m['type']}.md")
                    if not (os.path.exists(path) and os.path.exists(mp)):
                        continue
                    ours, mir = open(path, encoding='utf-8').read(), open(mp, encoding='utf-8').read()
                    mm = re.search(r"^법령MST:\s*'?(\d+)", mir, re.M)
                    if not mm or mm.group(1) != mst:
                        continue    # 미러가 다른 공포본을 들고 있으면 대조 대상이 아니다
                    # eflaw XML 은 law XML 과 공백이 조금 다르다("삭제 <날짜>" 띄어쓰기·빈 줄).
                    # 글자는 같으므로 공백을 지우고 본문을 견준다. 머리말은 별표 링크(flSeq)가 매번 달라 뺀다.
                    sq = lambda t: re.sub(r'\s+', '', body_only(t))
                    same = '완전 일치' if ours == mir else ('본문 일치(공백·머리말 차이)' if sq(ours) == sq(mir) else '본문 다름')
                    print(f'{label:<34} @{efyd} MST {mst}  미러 대조: {same}')
                    continue
                if os.path.exists(path) and not a.force:
                    kept += 1
                    continue
                md = fetch_markdown(mst, efyd)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(md)
                new += 1
                print(f'+ {label:<34} 시행 {efyd} ({state}) · 공포 {pub} {kind} · MST {mst}')
    except (ApiError, OSError) as ex:
        print(f'⛔ 법제처 API 실패: {ex}\n   등록 IP 확인: curl -s https://api.ipify.org', file=sys.stderr)
        return 2
    if not a.check_mirror:
        total = len(glob.glob(os.path.join(OUT, '*.md')))
        print(f'새 스냅샷 {new} · 기존 유지 {kept} · 폴더 총 {total}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
