#!/usr/bin/env python3
"""법령·고시 frontmatter 의 첨부파일(별표·별지) PDF 다운로드 — tier-aware.

legalize-kr/admrule-kr 미러는 별표 *파일* 은 저장하지 않지만 frontmatter 에
별표마다 제목 + law.go.kr flDownload PDF 링크를 담는다. 그 링크는 OpenAPI 가 아니라
공개 파일 다운로드(IP 무관)이므로 CI 에서 자동 수집 가능.

수집 범위 (2026-07-01 tier-aware 개정):
  · 고시(admin) + core 법령 = watchlist 로 이미 *큐레이션된 방사선 문서* → 별표 *전량* 수집.
    (구 키워드필터는 '안전관리규정 작성지침' 등 *이름에 방사선 없는* 고시의 별표를 탈락시켰음 → 폐기.)
  · peripheral 법령(의료법·약사법·의료기기법·의료기사법) = 모법이 일반이라 비방사선 서식 다수
    → 별표 *제목*이 방사선 키워드일 때만 수집(비방사선 서식 배제).
사용: python3 _collect_attachments.py [laws폴더] [admin폴더] [대상폴더]
"""
import sys, os, re, urllib.request
from _watchlist import members

LAWS = sys.argv[1] if len(sys.argv) > 1 else 'data/laws'
ADMIN = sys.argv[2] if len(sys.argv) > 2 else 'data/admin-rules'
DST = sys.argv[3] if len(sys.argv) > 3 else 'data/attachments'

# peripheral 법령 별표 제목 필터 (비방사선 서식 배제용)
RAD = re.compile(r'방사선|방사성|동위원소|선량|피폭|핵종|방사능|진료환자|격리|퇴원|'
                 r'차폐|방어시설|치료용|진단용|핵의학|방호|누설|RI')

# 법령 파일(<name>_<type>.md) → tier. 고시(admin)는 전부 core 취급(큐레이션 완료).
TIER = {f"{m['name']}_{m['type']}": m['tier'] for m in members()}


def frontmatter(path):
    t = open(path, encoding='utf-8').read()
    m = re.match(r'^---\n(.*?)\n---', t, re.S)
    return m.group(1) if m else ''


def attachments(fm):
    """첨부파일 블록 → [(별표구분, 제목, PDF링크)]"""
    m = re.search(r'^첨부파일:\s*\n(.*)\Z', fm, re.S | re.M)
    if not m:
        return []
    block, items, cur = m.group(1), [], {}
    for ln in block.splitlines():
        if re.match(r'\s*-\s*별표번호', ln):
            if cur:
                items.append(cur)
                cur = {}
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
ok = 0
skip = 0
for folder, is_admrule in ((LAWS, False), (ADMIN, True)):
    if not os.path.isdir(folder):
        continue
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith('.md'):
            continue
        parent = fn[:-3]
        # 고시 = 전량 · core 법령 = 전량 · peripheral 법령 = 제목필터
        collect_all = is_admrule or TIER.get(parent, 'peripheral') == 'core'
        for a in attachments(frontmatter(os.path.join(folder, fn))):
            title = a.get('제목', '')
            url = a.get('PDF링크', '')
            gubun = a.get('별표구분', '별표')
            if not url:
                continue
            if not collect_all and not RAD.search(title):   # peripheral 법령: 방사선 별표만
                skip += 1
                continue
            if url in seen_url:
                continue
            seen_url.add(url)
            out = os.path.join(DST, f'[{gubun}] {sanitize(title)}({sanitize(parent)}).pdf')
            if os.path.exists(out):
                ok += 1
                continue
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'radsafety-laws'})
                data = urllib.request.urlopen(req, timeout=40).read()
                if not data[:4] == b'%PDF':
                    print(f'  ? 비PDF skip: {title}', file=sys.stderr)
                    continue
                open(out, 'wb').write(data)
                ok += 1
                print(f'  ✓ [{gubun}] {title} ← {parent}')
            except Exception as e:
                print(f'  ✗ {title}: {e}', file=sys.stderr)

print(f'별표 수집(tier-aware): {ok}건 → {DST} (peripheral 비방사선 서식 {skip}건 제외)')
