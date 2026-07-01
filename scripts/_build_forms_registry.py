#!/usr/bin/env python3
"""서식·별지 메타 레지스트리 생성 — 빈 양식 카탈로그(본문 파싱 ✗).

별표(실체 규칙 표)는 _parse_attachments.py 가 md 로 파싱한다. 이 스크립트는 나머지
서식·별지·붙임(신청서·보고서·등록증 등 빈 양식)을 **제목+근거법령+근거조+다운로드 링크**
만 담은 단일 레지스트리(markdown)로 만든다. 빈 양식 본문은 검색 노이즈라 임베딩하지 않고,
"무슨 서식으로 X 신고?" 질의에 제목·조문·링크로 답하기 위한 카탈로그.

수집기(_collect_attachments.py)와 동일 게이트(tier + RAD 필터)·동일 파일명 규칙을 써서
레지스트리 항목 ⟷ data/attachments 파일이 1:1 대응한다.

근거조: 파일명/제목에 '(제N조 관련)' 이 있으면 채우고, 없으면 공란(대부분 서식은 조문이
제목에 없음 — 조 본문의 '별지 제M호서식' 참조로 청킹 단계에서 보강).

사용: python3 scripts/_build_forms_registry.py [laws] [admin] [attachments] [out.md]
"""
import sys, os, re
from collections import defaultdict
from _watchlist import members

LAWS = sys.argv[1] if len(sys.argv) > 1 else 'data/laws'
ADMIN = sys.argv[2] if len(sys.argv) > 2 else 'data/admin-rules'
ATTACH = sys.argv[3] if len(sys.argv) > 3 else 'data/attachments'
OUT = sys.argv[4] if len(sys.argv) > 4 else 'data/attachments-forms-registry.md'

RAD = re.compile(r'방사선|방사성|동위원소|선량|피폭|핵종|방사능|진료환자|격리|퇴원|'
                 r'차폐|방어시설|치료용|진단용|핵의학|방호|누설|RI')
TIER = {f"{m['name']}_{m['type']}": m['tier'] for m in members()}


def frontmatter(path):
    t = open(path, encoding='utf-8').read()
    m = re.match(r'^---\n(.*?)\n---', t, re.S)
    return m.group(1) if m else ''


def attachments(fm):
    m = re.search(r'^첨부파일:\s*\n(.*)\Z', fm, re.S | re.M)
    if not m:
        return []
    items, cur = [], {}
    for ln in m.group(1).splitlines():
        if re.match(r'\s*-\s*별표번호', ln):
            if cur:
                items.append(cur)
                cur = {}
        for key in ('별표번호', '별표가지번호', '별표구분', '제목', '파일링크', 'PDF링크'):
            mm = re.search(rf'{key}:\s*[\'"]?(.+?)[\'"]?\s*$', ln)
            if mm:
                cur[key] = mm.group(1)
    if cur:
        items.append(cur)
    return items


def sanitize(s):
    return re.sub(r'[/\\:*?"<>|]', '_', s)[:120]


def num(a):
    try:
        n = str(int(a.get('별표번호', '0')))
    except ValueError:
        return ''
    g = a.get('별표가지번호', '00')
    return n + (f'의{int(g)}' if g not in ('00', '0', '') else '')


def articles(title):
    return ' '.join(dict.fromkeys(re.findall(r'제\d+조(?:제\d+항)?(?:제\d+호)?', title)))


# 수집기와 동일 게이트로 서식·별지·붙임 수집(별표 제외)
rows = defaultdict(list)   # parent → [row dict]
seen_url = set()
total = 0
for folder, is_admrule in ((LAWS, False), (ADMIN, True)):
    if not os.path.isdir(folder):
        continue
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith('.md'):
            continue
        parent = fn[:-3]
        collect_all = is_admrule or TIER.get(parent, 'peripheral') == 'core'
        for a in attachments(frontmatter(os.path.join(folder, fn))):
            gubun = a.get('별표구분', '')
            if gubun == '별표':                     # 별표는 md 파싱 대상 → 레지스트리 제외
                continue
            title = a.get('제목', '')
            fl, pl = a.get('파일링크', ''), a.get('PDF링크', '')
            if not fl and not pl:
                continue
            if not collect_all and not RAD.search(title):
                continue
            key_url = fl or pl
            if key_url in seen_url:
                continue
            seen_url.add(key_url)
            base = f'[{gubun}] {sanitize(title)}({sanitize(parent)})'
            # disk 상 실제 파일 확인(원본 확장자·PDF)
            orig = next((base + e for e in ('.hwpx', '.hwp', '.doc', '.docx')
                         if os.path.exists(os.path.join(ATTACH, base + e))), '')
            pdf = base + '.pdf' if os.path.exists(os.path.join(ATTACH, base + '.pdf')) else ''
            rows[parent].append({
                'gubun': gubun, 'num': num(a), 'title': title,
                'art': articles(title), 'orig_url': fl, 'pdf_url': pl,
                'orig': orig, 'pdf': pdf,
            })
            total += 1

# markdown 레지스트리 출력
lines = [
    '---', 'type: 서식레지스트리', 'generated_by: scripts/_build_forms_registry.py',
    f'count: {total}', '---', '',
    '# 서식·별지 레지스트리',
    '',
    '빈 양식(신청서·보고서·등록증·동의서 등). **본문 파싱 안 함** — 제목·근거법령·근거조·'
    '다운로드 링크만. 실체 규칙 표는 `attachments-parsed/` 의 별표 md 참조.',
    '',
    '- 근거조 공란 = 제목에 조문 미표기(대부분 서식). 조 본문 `별지 제N호서식` 참조로 청킹 시 보강.',
    '- 링크: 원본(HWP/HWPX)·PDF = law.go.kr 공개 flDownload.',
    '',
]
for parent in sorted(rows):
    lines.append(f'## {parent}')
    lines.append('')
    lines.append('| 구분 | 번호 | 제목 | 근거조 | 원본 | PDF |')
    lines.append('|---|---|---|---|---|---|')
    for r in sorted(rows[parent], key=lambda x: (x['gubun'], x['num'])):
        o = f'[원본]({r["orig_url"]})' if r['orig_url'] else ''
        p = f'[PDF]({r["pdf_url"]})' if r['pdf_url'] else ''
        t = r['title'].replace('|', '\\|')
        lines.append(f'| {r["gubun"]} | {r["num"]} | {t} | {r["art"]} | {o} | {p} |')
    lines.append('')

open(OUT, 'w', encoding='utf-8').write('\n'.join(lines))
print(f'서식 레지스트리 생성: {total}건 ({len(rows)}개 법령) → {OUT}')
