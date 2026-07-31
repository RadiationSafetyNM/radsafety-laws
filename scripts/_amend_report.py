#!/usr/bin/env python3
"""주간 갱신분에서 **진짜 개정만** 골라 사람이 읽을 요약을 만든다 (CI 알림용).

문제: 주간 CI 는 매주 파일을 통째로 덮어써서 커밋한다. 그런데 파일이 바뀌었다고 법이
바뀐 건 아니다 — legalize-kr 이 본문을 재가공(포맷·오타·YAML 직렬화)해도 diff 가 생긴다.
실측(4주): 파일 변경 3주 / 실제 개정 1주. **알림을 파일 diff 에 걸면 2/3 가 헛알림**이다.

그래서 층마다 *노이즈에 안 흔들리는 신호*를 골라 쓴다 (→ docs/amendment-data-model.md):

  ① 법령 본문   — frontmatter `법령MST` 변경 → 조문 단위 diff
  ② 행정규칙    — frontmatter `행정규칙일련번호` 변경 → 조문 단위 diff
                  (고시·훈령·예규는 법규명령이 아니라 **별도 트랙**이라 필드 이름부터 다르다)
  ③ 별표·서식   — frontmatter 첨부 목록에서 **flSeq 를 뺀** (구분,번호,가지번호,제목) 비교
  ④ 별첨 파일   — **원본(hwp/hwpx)** 해시 A/M/D. PDF 는 제외

③④ 의 노이즈 제거가 핵심이다 (실측 근거):
  · `flSeq` 는 개정 때 **내용과 무관하게 전량 재발급**된다 → 넣으면 별표 166건이 통째로 울린다.
  · 별첨 M(수정) 21건이 **전부 PDF**였고 원본은 0건 — PDF 는 재생성만으로 바이트가 바뀐다.

③ 이 ④ 보다 **빠르다**: 2026-07-09 개정으로 신설된 건강진단표 서식은 frontmatter 에 7/19
(법령 개정 주)에 이미 나타났고, 실제 파일은 **7/26 에 도착**했다. 목록이 파일보다 일주일 앞선다.

**API 를 쓰지 않는다** — 신구법비교·신선도감사는 IP 등록이 필요해 CI 에서 못 돈다. 여기 신호는
전부 저장소 안에서 완결된다. 권위 대조는 고정 IP PC 에서 `_amend_audit.py` 로 사후 수행한다.

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

# 문서 두 갈래 — 법규명령(법령) / 행정규칙(고시·훈령·예규). 버전키·날짜 필드 이름이 다르다.
KINDS = [
    {'key': 'law',    'dir': 'data/laws',        'label': '법령',
     'ver': '법령MST',          'date': '공포일자'},
    {'key': 'admrul', 'dir': 'data/admin-rules', 'label': '행정규칙',
     'ver': '행정규칙일련번호',  'date': '발령일자'},
]
ATTACH_DIR = 'data/attachments'
SRC_EXT = ('.hwp', '.hwpx')     # 원본만 — PDF 는 재생성 노이즈라 제외


def sh(*args):
    p = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    return p.stdout


def _fm(text):
    m = re.match(r'---\n(.*?)\n---', text, re.S)
    return m.group(1) if m else ''


def fm_get(text, *keys):
    fm = _fm(text)
    out = []
    for k in keys:
        m = re.search(rf'^{k}:\s*[\'"]?(.+?)[\'"]?\s*$', fm, re.M)
        out.append(m.group(1) if m else '')
    return out


# 첨부 목록 — flSeq(파일링크·PDF링크)는 읽지 않는다. 개정마다 전량 재발급되는 노이즈.
ATTACH_RE = re.compile(
    r"- 별표번호:\s*'?(\d+)'?\s*\n\s+별표가지번호:\s*'?(\d+)'?\s*\n"
    r"\s+별표구분:\s*'?([^'\n]+?)'?\s*\n\s+제목:\s*'?([^'\n]+?)'?\s*$", re.M)


def attach_items(text):
    """{(구분, 번호, 가지번호): 제목}"""
    return {(c.strip(), a, b): d.strip() for a, b, c, d in ATTACH_RE.findall(_fm(text))}


def diff_attachments(old_t, new_t):
    """신설·삭제·개정(제목변경) 분류.

    (구분,번호,가지번호)로 먼저 짝을 맞춘다 — 그냥 집합 비교하면 '별표19 제목 변경'이
    '삭제 1 + 신설 1'로 나와 실제(그 서식의 개정)를 못 읽는다.
    """
    old, new = attach_items(old_t), attach_items(new_t)
    added   = [(k, new[k]) for k in new if k not in old]
    removed = [(k, old[k]) for k in old if k not in new]
    retitled = [(k, old[k], new[k]) for k in new if k in old and old[k] != new[k]]
    return {'added': added, 'removed': removed, 'retitled': retitled}


def label_attach(key):
    kind, no, branch = key
    n = f'별표{int(no)}' + (f'의{int(branch)}' if branch != '00' else '')
    return f'{n} [{kind}]'


def read(rev, path):
    if rev is None:
        p = os.path.join(ROOT, path)
        return open(p, encoding='utf-8').read() if os.path.exists(p) else ''
    return sh('git', 'show', f'{rev}:{path}')


def changed_paths(old_rev, new_rev, subdir, suffix='.md'):
    if new_rev is None:
        out = sh('git', 'status', '--porcelain', '--', subdir)
        paths = {ln[3:].strip().strip('"') for ln in out.splitlines()}
    else:
        out = sh('git', 'diff', '--name-only', f'{old_rev}..{new_rev}', '--', subdir)
        paths = {ln.strip().strip('"') for ln in out.splitlines()}
    return sorted(p for p in paths if p.endswith(suffix))


def collect_docs(old_rev='HEAD', new_rev=None):
    """법령·행정규칙 중 **버전키가 실제로 바뀐** 것만 → 조문·첨부목록 diff."""
    items = []
    for kind in KINDS:
        for path in changed_paths(old_rev, new_rev, kind['dir']):
            old_t, new_t = read(old_rev, path), read(new_rev, path)
            if not new_t:
                continue
            o_ver, o_date = fm_get(old_t, kind['ver'], kind['date'])
            n_ver, n_date = fm_get(new_t, kind['ver'], kind['date'])
            if not n_ver or o_ver == n_ver:
                continue                  # ← 재가공. 여기서 걸러진다.
            d = diff_articles(old_t, new_t)
            att = diff_attachments(old_t, new_t)
            items.append({
                'kind': kind['key'], 'kind_label': kind['label'], 'path': path,
                'name': os.path.basename(path)[:-3].replace('_', ' '),
                'ver': {'old': o_ver, 'new': n_ver},
                'date': {'old': o_date, 'new': n_date}, 'date_label': kind['date'],
                'changed': d['changed'], 'added': d['added'], 'removed': d['removed'],
                'addenda': d['addenda_changed'],
                'titles': {no: d['_new'].get(no, {}).get('title', '')
                           for no in d['changed'] + d['added']},
                'attach': att,
            })
    return items


def collect_files(old_rev='HEAD', new_rev=None):
    """별첨 **원본 파일**(hwp/hwpx)의 A/M/D. PDF 는 세지 않는다(재생성 노이즈)."""
    if new_rev is None:
        raw = sh('git', 'status', '--porcelain', '--', ATTACH_DIR)
        rows = [(ln[:2].strip().replace('??', 'A')[:1], ln[3:].strip().strip('"'))
                for ln in raw.splitlines() if ln.strip()]
    else:
        raw = sh('git', 'diff', '--name-status', f'{old_rev}..{new_rev}', '--', ATTACH_DIR)
        rows = []
        for ln in raw.splitlines():
            parts = ln.split('\t')
            if len(parts) >= 2:
                rows.append((parts[0][:1], parts[-1].strip().strip('"')))
    out = {'A': [], 'M': [], 'D': []}
    for st, path in rows:
        if not path.lower().endswith(SRC_EXT) or st not in out:
            continue
        out[st].append(os.path.basename(path))
    return out


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


def _att_lines(att):
    L = []
    for k, t in att['added']:
        L.append(f'{label_attach(k)} {t} — 신설')
    for k, o, n in att['retitled']:
        L.append(f'{label_attach(k)} 개정 — "{o}" → "{n}"')
    for k, t in att['removed']:
        L.append(f'{label_attach(k)} {t} — 삭제')
    return L


def subject(items, files):
    n_att = sum(len(_att_lines(i['attach'])) for i in items)
    if not items:
        return ('별첨 원본 변경 %d건' % sum(len(v) for v in files.values())
                if any(files.values()) else '법령·고시·별표 갱신 (개정 없음 — 재가공분)')
    if len(items) == 1:
        it = items[0]
        arts = _arts(it)
        head = ', '.join(arts[:2]) + (' 외' if len(arts) > 2 else '')
        tail = f' · 별표 {n_att}건' if n_att else ''
        return f'{it["name"]} {head or "부칙"} 개정{tail}'
    return (f'{len(items)}건 개정 — ' + ', '.join(i['name'].split()[0] for i in items[:3])
            + (f' · 별표 {n_att}건' if n_att else ''))


def markdown(items, files):
    has_file = any(files.values())
    if not items and not has_file:
        return '이번 갱신에 **개정 없음** — 파일 변경은 있었으나 버전키 무변경(미러 재가공).'
    L = []
    if items:
        L += ['## 이번 주 개정', '',
              f'**{len(items)}건** — 버전키(`법령MST`/`행정규칙일련번호`)가 실제로 바뀐 것만', '']
        for it in items:
            L.append(f'### [{it["kind_label"]}] {it["name"]}')
            L.append(f'- 버전 `{it["ver"]["old"]}` → `{it["ver"]["new"]}`  ·  '
                     f'{it["date_label"]} {it["date"]["old"]} → **{it["date"]["new"]}**')
            arts = _arts(it)
            L.append(f'- 개정 조문: {", ".join(arts) if arts else "(조문 변경 없음)"}')
            if it['addenda']:
                L.append('- 부칙 변경 있음')
            att = _att_lines(it['attach'])
            if att:
                L.append('- **별표·서식**')
                L += [f'  - {x}' for x in att]
            L.append(f'- `{it["path"]}`')
            L.append('')
    if has_file:
        L += ['## 별첨 원본 파일 (hwp/hwpx)', '',
              '> PDF 는 재생성만으로 바이트가 바뀌므로 세지 않는다.', '']
        for st, word in (('A', '신규'), ('M', '내용 수정'), ('D', '삭제')):
            for f in files[st][:12]:
                L.append(f'- **{word}** `{f}`')
            if len(files[st]) > 12:
                L.append(f'- … 외 {len(files[st]) - 12}건 {word}')
        L.append('')
    L += ['---', '',
          '> 자체 diff 기준입니다(CI 는 IP 등록이 안 돼 법제처 API 를 못 씁니다).',
          '> 법제처 신구법비교와의 교차검증은 고정 IP PC 에서:',
          '> ```',
          '> python3 scripts/_amend_audit.py --all --cache-dir out/moleg',
          '> ```']
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='주간 갱신분 개정 요약(버전키 변경분만)')
    ap.add_argument('--range', nargs=2, metavar=('OLD', 'NEW'), default=None)
    ap.add_argument('--format', choices=['text', 'md', 'json'], default='text')
    ap.add_argument('--subject', action='store_true', help='커밋 제목 한 줄만 출력')
    a = ap.parse_args()

    old_rev, new_rev = (a.range[0], a.range[1]) if a.range else ('HEAD', None)
    items = collect_docs(old_rev, new_rev)
    files = collect_files(old_rev, new_rev)

    if a.subject:
        print(subject(items, files))
    elif a.format == 'json':
        print(json.dumps({'docs': items, 'files': files}, ensure_ascii=False, indent=2))
    elif a.format == 'md':
        print(markdown(items, files))
    else:
        if not items and not any(files.values()):
            print('개정 없음 — 버전키 무변경(미러 재가공)')
        for it in items:
            print(f'📜 [{it["kind_label"]}] {it["name"]}  '
                  f'{it["ver"]["old"]}→{it["ver"]["new"]}  {it["date"]["new"]}')
            print(f'   조문: {", ".join(_arts(it)) or "(변경 없음)"}'
                  f'{" · 부칙 변경" if it["addenda"] else ""}')
            for x in _att_lines(it['attach']):
                print(f'   별표: {x}')
        for st, word in (('A', '신규'), ('M', '내용수정'), ('D', '삭제')):
            for f in files[st][:8]:
                print(f'📎 원본 {word}: {f}')
    sys.exit(0 if (items or any(files.values())) else 1)


if __name__ == '__main__':
    main()
