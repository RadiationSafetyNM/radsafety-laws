#!/usr/bin/env python3
"""법령·고시 frontmatter 의 첨부파일(별표·별지) 원본+PDF 다운로드 — tier-aware.

legalize-kr/admrule-kr 미러는 별표 *파일* 은 저장하지 않지만 frontmatter 에
별표마다 제목 + law.go.kr flDownload 링크 2종을 담는다:
  · 파일링크: **원본**(HWP/HWPX — 부처 저작 편집 마스터, 표 구조 보존). 별표 전량(100%) 존재.
  · PDF링크:  원본에서 파생한 배포·인쇄본(레이아웃 고정, 구조 소실). 일부(≈12%) 누락.
둘 다 OpenAPI 가 아니라 공개 파일 다운로드(IP 무관)이므로 CI 에서 자동 수집 가능.
2026-07-01 이후 **원본+PDF 병행 수집** — 원본은 파싱 충실도(표), PDF 는 비전 대조·폴백용.
동일 별표의 원본·PDF 는 같은 stem, 확장자만 다르게 나란히 저장(companion).

수집 범위 (2026-07-01 tier-aware 개정):
  · 고시(admin) + core 법령 = watchlist 로 이미 *큐레이션된 방사선 문서* → 별표 *전량* 수집.
    (구 키워드필터는 '안전관리규정 작성지침' 등 *이름에 방사선 없는* 고시의 별표를 탈락시켰음 → 폐기.)
  · peripheral 법령(의료법·약사법·의료기기법·의료기사법) = 모법이 일반이라 비방사선 서식 다수
    → 별표 *제목*이 방사선 키워드일 때만 수집(비방사선 서식 배제).
사용: python3 _collect_attachments.py [laws폴더] [admin폴더] [대상폴더]
"""
import sys, os, re, urllib.request
from urllib.parse import unquote
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
        for key in ('별표구분', '제목', '파일링크', 'PDF링크'):
            mm = re.search(rf'{key}:\s*[\'"]?(.+?)[\'"]?\s*$', ln)
            if mm:
                cur[key] = mm.group(1)
    if cur:
        items.append(cur)
    return items


def sanitize(s):
    return re.sub(r'[/\\:*?"<>|]', '_', s)[:120]


ALLOWED_EXT = {'pdf', 'hwp', 'hwpx', 'doc', 'docx'}


def pick_ext(resp, data):
    """Content-Disposition 파일명 확장자 우선, 실패 시 매직바이트로 판별."""
    cd = unquote(resp.headers.get('Content-Disposition', ''))
    m = re.search(r'\.([A-Za-z0-9]+)"?\s*$', cd)
    if m and m.group(1).lower() in ALLOWED_EXT:
        return m.group(1).lower()
    if data[:4] == b'%PDF':
        return 'pdf'
    if data[:4] == b'PK\x03\x04':          # zip 기반 = hwpx/docx
        return 'hwpx'
    if data[:4] == b'\xd0\xcf\x11\xe0':      # OLE/CFB = 구 바이너리 hwp/doc
        return 'hwp'
    return None


def download(url, base, expect_pdf):
    """url → DST/{base}.{ext} 저장. 반환: 'saved:<ext>' | 'exist' | None(skip/err).
    idempotent: 이미 파일 있으면 재다운로드 안 함."""
    # PDF 는 확장자 확정(.pdf) → 사전 존재검사로 재다운 회피
    if expect_pdf:
        out = os.path.join(DST, f'{base}.pdf')
        if os.path.exists(out):
            return 'exist'
    else:  # 원본은 hwp/hwpx 중 하나 — 둘 중 하나라도 있으면 skip
        for e in ('hwp', 'hwpx', 'doc', 'docx'):
            if os.path.exists(os.path.join(DST, f'{base}.{e}')):
                return 'exist'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'radsafety-laws'})
        resp = urllib.request.urlopen(req, timeout=40)
        data = resp.read()
    except Exception as e:
        print(f'  ✗ {base}: {e}', file=sys.stderr)
        return None
    ext = pick_ext(resp, data)
    if expect_pdf and ext != 'pdf':
        print(f'  ? 비PDF skip: {base}', file=sys.stderr)
        return None
    if ext is None:
        print(f'  ? 미확인형식 skip: {base}', file=sys.stderr)
        return None
    out = os.path.join(DST, f'{base}.{ext}')
    if os.path.exists(out):
        return 'exist'
    open(out, 'wb').write(data)
    return f'saved:{ext}'


os.makedirs(DST, exist_ok=True)
seen_url = set()
ok_pdf = 0
ok_orig = 0
from collections import Counter
orig_ext = Counter()
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
            gubun = a.get('별표구분', '별표')
            pdf_url = a.get('PDF링크', '')
            orig_url = a.get('파일링크', '')      # 원본(HWP/HWPX)
            if not pdf_url and not orig_url:
                continue
            if not collect_all and not RAD.search(title):   # peripheral 법령: 방사선 별표만
                skip += 1
                continue
            base = f'[{gubun}] {sanitize(title)}({sanitize(parent)})'
            # 원본(HWP/HWPX) — 파싱 충실도의 1차 소스
            if orig_url and orig_url not in seen_url:
                seen_url.add(orig_url)
                r = download(orig_url, base, expect_pdf=False)
                if r == 'exist':
                    ok_orig += 1
                elif r and r.startswith('saved:'):
                    ok_orig += 1
                    orig_ext[r.split(':', 1)[1]] += 1
                    print(f'  ✓ [{gubun}·원본] {title} ← {parent}')
            # PDF — 비전 대조·폴백
            if pdf_url and pdf_url not in seen_url:
                seen_url.add(pdf_url)
                r = download(pdf_url, base, expect_pdf=True)
                if r == 'exist':
                    ok_pdf += 1
                elif r and r.startswith('saved:'):
                    ok_pdf += 1
                    print(f'  ✓ [{gubun}·PDF] {title} ← {parent}')

print(f'별표 수집(tier-aware): 원본 {ok_orig}건 {dict(orig_ext)} + PDF {ok_pdf}건 '
      f'→ {DST} (peripheral 비방사선 서식 {skip}건 제외)')
