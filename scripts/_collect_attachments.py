#!/usr/bin/env python3
"""법령·고시 frontmatter 의 첨부파일(별표·별지) 중 방사선 관련만 PDF 다운로드.

legalize-kr/admrule-kr 미러는 별표 *파일* 은 저장하지 않지만 frontmatter 에
별표마다 제목 + law.go.kr flDownload PDF 링크를 담는다. 그 링크는 OpenAPI 가 아니라
공개 파일 다운로드(IP 무관)이므로 CI 에서 자동 수집 가능.

스코프(옵션2): 제목이 방사선·의료 키워드인 별표만 → 약사법·의료기기법 등 비방사선 서식 배제.
사용: python3 _collect_attachments.py [laws폴더] [admin폴더] [대상폴더]
"""
import sys, os, re, urllib.request

LAWS = sys.argv[1] if len(sys.argv) > 1 else 'data/laws'
ADMIN = sys.argv[2] if len(sys.argv) > 2 else 'data/admin-rules'
DST = sys.argv[3] if len(sys.argv) > 3 else 'data/attachments'

# 방사선·의료 별표 제목 키워드 (약사법·의료기기 일반서식 배제)
RAD = re.compile(r'방사선|방사성|동위원소|선량|피폭|핵종|방사능|진료환자|격리|퇴원|'
                 r'차폐|방어시설|치료용|진단용|핵의학|방호|누설|RI')
# 제목이 너무 일반적이라 키워드를 못 잡지만 부모가 *방사선 전용* 문서면 포함
RAD_PARENT = re.compile(r'진단용방사선|특수의료장비|생활주변방사선|방사선및방사성|의료분야의 방사선|'
                        r'진단용 방사선|질병관리청 방사선|방사선안전|피폭|선량|동위원소|방사성동위원소|'
                        r'방사선방호|방사선기기|방사선발생장치|방사선원')

def frontmatter(path):
    t = open(path, encoding='utf-8').read()
    m = re.match(r'^---\n(.*?)\n---', t, re.S)
    return m.group(1) if m else ''

def attachments(fm):
    """첨부파일 블록 → [(별표구분, 제목, PDF링크)]"""
    # 첨부파일: 은 frontmatter 의 마지막 키 → 그 줄 다음부터 끝까지 캡처(빈 '[]'는 매칭 안 됨)
    m = re.search(r'^첨부파일:\s*\n(.*)\Z', fm, re.S | re.M)
    if not m:
        return []
    block, items, cur = m.group(1), [], {}
    for ln in block.splitlines():
        if re.match(r'\s*-\s*별표번호', ln):
            if cur:
                items.append(cur); cur = {}
        for key in ('별표구분', '제목', 'PDF링크'):
            mm = re.search(rf'{key}:\s*[\'"]?(.+?)[\'"]?\s*$', ln)
            if mm:
                cur[key] = mm.group(1)
    if cur:
        items.append(cur)
    return items

def sanitize(s):
    return re.sub(r'[/\\:*?"<>|]', '_', s)[:120]

os.makedirs(DST, exist_ok=True)
seen_url = set()
ok = 0; skip = 0
for folder in (LAWS, ADMIN):
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith('.md'):
            continue
        parent = fn[:-3]
        rad_parent = bool(RAD_PARENT.search(parent))
        for a in attachments(frontmatter(os.path.join(folder, fn))):
            title, url, gubun = a.get('제목', ''), a.get('PDF링크', ''), a.get('별표구분', '별표')
            if not url:
                continue
            if not (RAD.search(title) or rad_parent):   # 방사선 별표만
                skip += 1; continue
            if url in seen_url:
                continue
            seen_url.add(url)
            out = os.path.join(DST, f'[{gubun}] {sanitize(title)}({sanitize(parent)}).pdf')
            if os.path.exists(out):
                ok += 1; continue
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'radsafety-laws'})
                data = urllib.request.urlopen(req, timeout=40).read()
                if not data[:4] == b'%PDF':
                    print(f'  ? 비PDF skip: {title}', file=sys.stderr); continue
                open(out, 'wb').write(data); ok += 1
                print(f'  ✓ [{gubun}] {title} ← {parent}')
            except Exception as e:
                print(f'  ✗ {title}: {e}', file=sys.stderr)

print(f'별표 수집: {ok}건 → {DST} (비방사선 제목 {skip}건 제외)')
