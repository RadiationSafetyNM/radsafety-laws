#!/usr/bin/env python3
"""자체 개정 diff — 우리 저장소 안에서 조문 단위로 "무엇이 바뀌었나"를 뽑는다.

왜 필요한가: `법령MST` 변경은 "새 공포본이 나왔다"는 **플래그**일 뿐 내용을 말해주지 않는다.
반대로 `git diff` 는 글자가 달라졌다는 것만 알려줘 **미러 재가공(포맷·오타 수정)과 진짜 개정을
구별하지 못한다** — 실측: 2026-07-26 커밋은 원자력안전법 시행규칙 본문 116줄이 바뀌었지만
frontmatter MST 무변경, 즉 법은 그대로였다. 그래서 조문 단위로 쪼개 비교한다.

가능한 이유: legalize-kr 마크다운이 `##### 제N조` 로 구조화돼 있어 조 경계가 기계적으로 잡힌다.
실측(2026-07-19 개정, 원자력안전법 시행규칙): 830줄 diff 중 **실제 변경 조문은 2개**뿐이었다.

부칙은 조문과 분리한다 — 부칙은 마지막 조 뒤에 붙어 있어 그냥 파싱하면 직전 조(제147조 등)의
변경으로 잘못 귀속된다. 신구법비교 API 도 부칙을 조문으로 세지 않으므로, 분리해야 API 와
대조가 맞는다(→ _amend_audit.py).

사용:
  # git 두 리비전 비교 (CI·회고 분석)
  python3 _amend_selfdiff.py --git <old_rev> <new_rev> data/laws/원자력안전법_시행규칙.md
  # 작업트리 파일 두 개 비교
  python3 _amend_selfdiff.py --file old.md new.md
  # JSON 출력(다른 스크립트가 소비)
  python3 _amend_selfdiff.py --git HEAD~1 HEAD data/laws/... --json
"""
import sys, os, re, json, hashlib, difflib, subprocess, argparse

# `##### 제N조`·`### 제N조의M` 등 3~5단 헤딩. 조 제목 괄호는 선택.
ART_HEAD = re.compile(r'#{3,5}\s*(제\s*\d+조(?:의\s*\d+)?)\s*(?:\(([^)]*)\))?')
# 부칙 경계 — `## 부칙`, `부칙 <제2132호,2026.7.9>` 양쪽 형태.
ADDENDA = re.compile(r'(?:^#{1,6}\s*부\s*칙|^\s*부\s*칙\s*<)', re.M)


def _norm(no):
    """'제 121 조의 2' → '제121조의2' (공백 흔들림 흡수)."""
    return re.sub(r'\s+', '', no)


def split_articles(text):
    """마크다운 법령 본문 → (조문 dict, 부칙 텍스트).

    조문 dict = {조번호: {'title':조제목, 'body':본문, 'hash':md5}}
    부칙은 조문에서 떼어 별도로 돌려준다(위 docstring 참조).
    """
    m = ADDENDA.search(text)
    body, addenda = (text[:m.start()], text[m.start():]) if m else (text, '')

    arts, cur, buf = {}, None, []

    def flush():
        if cur:
            raw = '\n'.join(buf)
            arts[cur[0]] = {'title': cur[1] or '',
                            'body': raw,
                            'hash': hashlib.md5(raw.encode('utf-8')).hexdigest()}

    for line in body.splitlines():
        h = ART_HEAD.match(line)
        if h:
            flush()
            cur, buf = (_norm(h.group(1)), (h.group(2) or '').strip()), []
        buf.append(line)
    flush()
    return arts, addenda.strip()


def diff_articles(old_text, new_text):
    """두 본문을 조문 단위로 비교 → 변경·신설·삭제·부칙 판정."""
    old, old_add = split_articles(old_text)
    new, new_add = split_articles(new_text)
    changed = sorted([k for k in new if k in old and old[k]['hash'] != new[k]['hash']],
                     key=_sortkey)
    return {
        'changed': changed,
        'added': sorted([k for k in new if k not in old], key=_sortkey),
        'removed': sorted([k for k in old if k not in new], key=_sortkey),
        'addenda_changed': old_add != new_add,
        'counts': {'old': len(old), 'new': len(new)},
        '_old': old, '_new': new,
        '_addenda': {'old': old_add, 'new': new_add},
    }


def _sortkey(no):
    m = re.match(r'제(\d+)조(?:의(\d+))?', no)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def article_text_diff(res, no, context=0):
    """조문 하나의 라인 단위 diff 라인들 (사람이 읽을 용도)."""
    a = res['_old'].get(no, {}).get('body', '').splitlines()
    b = res['_new'].get(no, {}).get('body', '').splitlines()
    return [l for l in difflib.unified_diff(a, b, lineterm='', n=context)
            if not l.startswith(('---', '+++', '@@'))]


def _git_show(rev, path):
    p = subprocess.run(['git', 'show', f'{rev}:{path}'],
                       capture_output=True, text=True)
    if p.returncode:
        sys.exit(f'✗ git show {rev}:{path} 실패 — {p.stderr.strip()[:120]}')
    return p.stdout


def main():
    ap = argparse.ArgumentParser(description='조문 단위 자체 개정 diff')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--git', nargs=3, metavar=('OLD_REV', 'NEW_REV', 'PATH'),
                   help='git 두 리비전의 같은 파일 비교')
    g.add_argument('--file', nargs=2, metavar=('OLD', 'NEW'), help='파일 두 개 비교')
    ap.add_argument('--json', action='store_true', help='JSON 출력')
    ap.add_argument('--show', metavar='조번호', help='해당 조문의 라인 diff 출력')
    a = ap.parse_args()

    if a.git:
        old_rev, new_rev, path = a.git
        old_t, new_t, label = _git_show(old_rev, path), _git_show(new_rev, path), path
    else:
        old_t = open(a.file[0], encoding='utf-8').read()
        new_t = open(a.file[1], encoding='utf-8').read()
        label = a.file[1]

    res = diff_articles(old_t, new_t)

    if a.json:
        out = {k: v for k, v in res.items() if not k.startswith('_')}
        out['path'] = label
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print(f'📄 {label}')
    print(f'   조문 {res["counts"]["old"]} → {res["counts"]["new"]}개')
    print(f'   변경 {len(res["changed"])} · 신설 {len(res["added"])} · 삭제 {len(res["removed"])}'
          f' · 부칙 {"변경" if res["addenda_changed"] else "무변경"}')
    for k, label_ in (('changed', '변경'), ('added', '신설'), ('removed', '삭제')):
        if res[k]:
            for no in res[k]:
                t = (res['_new'] if k != 'removed' else res['_old']).get(no, {}).get('title', '')
                print(f'   [{label_}] {no}{f"({t})" if t else ""}')
    if a.show:
        no = _norm(a.show)
        print(f'\n───── {no} ─────')
        for line in article_text_diff(res, no):
            print(line[:200])


if __name__ == '__main__':
    main()
