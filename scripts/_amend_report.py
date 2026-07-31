#!/usr/bin/env python3
"""주간 갱신분에서 **진짜 개정만** 골라 사람이 읽을 요약을 만든다 (CI 알림용).

문제: 주간 CI 는 매주 파일을 통째로 덮어써서 커밋한다. 그런데 파일이 바뀌었다고 법이
바뀐 건 아니다 — legalize-kr 이 본문을 재가공(포맷·오타)해도 diff 가 생긴다.
실측(4주): 파일 변경 3주 / 실제 개정 1주. **알림을 파일 diff 에 걸면 2/3 가 헛알림**이다.

그래서 두 단계로 거른다:
  ① frontmatter `법령MST` 가 실제로 바뀐 파일만 남긴다 (재가공 커밋을 통째로 탈락).
  ② 남은 것만 조문 단위로 diff 해서 "제N조가 바뀌었다"까지 뽑는다 (_amend_selfdiff).

**API 를 쓰지 않는다** — 신구법비교·신선도감사는 IP 등록이 필요해 CI(ubuntu-latest)에서
못 돈다. 자체 diff 는 저장소 안에서 완결되므로 CI 에서 돌아간다. 법제처 대조(_amend_audit)는
고정 IP PC 에서 사후 교차검증으로 돌린다 — 둘의 역할 분담이다.

기본 모드는 **워킹트리 ↔ HEAD** 비교다. CI 에서 fetch 직후·커밋 직전에 부르면 "이번 주에
무엇이 개정됐나"가 나온다.

사용:
  python3 _amend_report.py                      # 워킹트리 vs HEAD (CI 기본)
  python3 _amend_report.py --range A B          # 두 커밋 사이 (사후 분석)
  python3 _amend_report.py --format md          # 마크다운(이슈 본문용)
  python3 _amend_report.py --subject            # 커밋 제목 한 줄만
종료코드: 0 = 개정 있음 · 1 = 개정 없음(재가공뿐) — CI 분기용.
"""
import os, sys, re, json, argparse, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _amend_selfdiff import diff_articles                      # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
LAWS = 'data/laws'


def sh(*args):
    p = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    return p.stdout


def fm_mst(text):
    m = re.match(r'---\n(.*?)\n---', text, re.S)
    if not m:
        return '', ''
    fm = m.group(1)
    g = lambda k: (re.search(rf'^{k}:\s*[\'"]?(.+?)[\'"]?\s*$', fm, re.M) or [None, ''])[1]
    return g('법령MST'), g('공포일자')


def read(rev, path):
    """rev=None 이면 워킹트리 파일."""
    if rev is None:
        p = os.path.join(ROOT, path)
        return open(p, encoding='utf-8').read() if os.path.exists(p) else ''
    return sh('git', 'show', f'{rev}:{path}')


def changed_paths(old_rev, new_rev):
    if new_rev is None:            # 워킹트리 — 아직 커밋 안 된 변경
        out = sh('git', 'status', '--porcelain', '--', LAWS)
        return sorted({ln[3:].strip() for ln in out.splitlines() if ln[3:].strip().endswith('.md')})
    out = sh('git', 'diff', '--name-only', f'{old_rev}..{new_rev}', '--', LAWS)
    return sorted({ln.strip() for ln in out.splitlines() if ln.strip().endswith('.md')})


def collect(old_rev='HEAD', new_rev=None):
    """[{path, name, mst, promulgated, changed, added, removed, addenda}] — MST 변경분만."""
    items = []
    for path in changed_paths(old_rev, new_rev):
        old_t, new_t = read(old_rev, path), read(new_rev, path)
        if not new_t:
            continue
        o_mst, o_pub = fm_mst(old_t)
        n_mst, n_pub = fm_mst(new_t)
        if not n_mst or o_mst == n_mst:
            continue                      # ← 재가공. 여기서 걸러진다.
        d = diff_articles(old_t, new_t)
        items.append({
            'path': path,
            'name': os.path.basename(path)[:-3].replace('_', ' '),
            'mst': {'old': o_mst, 'new': n_mst},
            'promulgated': {'old': o_pub, 'new': n_pub},
            'changed': d['changed'], 'added': d['added'], 'removed': d['removed'],
            'addenda': d['addenda_changed'],
            'titles': {no: d['_new'].get(no, {}).get('title', '')
                       for no in d['changed'] + d['added']},
        })
    return items


def _arts(it):
    out = []
    for no in it['changed']:
        t = it['titles'].get(no) or ''
        out.append(f'{no}{f"({t})" if t else ""}')
    for no in it['added']:
        t = it['titles'].get(no) or ''
        out.append(f'{no}{f"({t})" if t else ""} 신설')
    out += [f'{no} 삭제' for no in it['removed']]
    return out


def subject(items):
    if not items:
        return '법령·고시·별표 갱신 (개정 없음 — 재가공분)'
    if len(items) == 1:
        it = items[0]
        arts = _arts(it)
        head = ', '.join(arts[:2]) + (' 외' if len(arts) > 2 else '')
        return f'{it["name"]} {head or "부칙"} 개정'
    return f'법령 {len(items)}건 개정 — ' + ', '.join(i['name'].split()[0] for i in items[:3])


def markdown(items):
    if not items:
        return '이번 갱신에 **법령 개정 없음** — 파일 변경은 있었으나 `법령MST` 무변경(미러 재가공).'
    L = ['## 이번 주 개정 법령', '',
         f'**{len(items)}건** — `법령MST` 가 실제로 바뀐 것만 (미러 재가공 제외)', '']
    for it in items:
        L.append(f'### {it["name"]}')
        L.append(f'- MST `{it["mst"]["old"]}` → `{it["mst"]["new"]}`  ·  '
                 f'공포 {it["promulgated"]["old"]} → **{it["promulgated"]["new"]}**')
        arts = _arts(it)
        L.append(f'- 개정 조문: {", ".join(arts) if arts else "(조문 변경 없음)"}')
        if it['addenda']:
            L.append('- 부칙 변경 있음')
        L.append(f'- `{it["path"]}`')
        L.append('')
    L += ['---', '',
          '> 자체 조문 diff 기준입니다(CI 는 IP 등록이 안 돼 법제처 API 를 못 씁니다).',
          '> 법제처 신구법비교와의 교차검증은 고정 IP PC 에서:',
          '> ```',
          '> python3 scripts/_amend_audit.py --all --cache-dir out/moleg',
          '> ```']
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='주간 갱신분 개정 요약(MST 변경분만)')
    ap.add_argument('--range', nargs=2, metavar=('OLD', 'NEW'), default=None)
    ap.add_argument('--format', choices=['text', 'md', 'json'], default='text')
    ap.add_argument('--subject', action='store_true', help='커밋 제목 한 줄만 출력')
    a = ap.parse_args()

    old_rev, new_rev = (a.range[0], a.range[1]) if a.range else ('HEAD', None)
    items = collect(old_rev, new_rev)

    if a.subject:
        print(subject(items))
    elif a.format == 'json':
        print(json.dumps(items, ensure_ascii=False, indent=2))
    elif a.format == 'md':
        print(markdown(items))
    else:
        if not items:
            print('개정 없음 — MST 무변경(미러 재가공)')
        for it in items:
            print(f'📜 {it["name"]}  MST {it["mst"]["old"]}→{it["mst"]["new"]}'
                  f'  공포 {it["promulgated"]["new"]}')
            print(f'   {", ".join(_arts(it)) or "(조문 변경 없음)"}'
                  f'{" · 부칙 변경" if it["addenda"] else ""}')
    sys.exit(0 if items else 1)


if __name__ == '__main__':
    main()
