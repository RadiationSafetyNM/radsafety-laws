#!/usr/bin/env python3
"""별표 원본(HWP/HWPX) → 구조보존 markdown 파싱.

파이프라인 산출물(재현 가능) — 수동 변환 금지. 경로:
  hwp/hwpx --[LibreOffice + H2Orestart]--> docx --[pandoc docx리더]--> gfm markdown(표 보존)
  (html 경로는 span lang 노이즈로 ~27배 부풀어 폐기 — docx 리더가 깨끗함.)
  병합셀 표는 gfm 파이프로 표현 불가 → clean HTML <table> 로 보존(LLM 판독 가능).

대상 = **별표만**(실체 규칙 표). 서식·별지는 빈 양식이라 본문 파싱 안 함(메타 레지스트리 별도).
원본 우선(파일링크). 별표는 원본 100% 존재하므로 PDF 빠진 별표(≈16)도 여기서 커버.

전제: LibreOffice(soffice) + H2Orestart 확장 설치(사용자 프로필). pandoc 설치.
사용: python3 scripts/_parse_attachments.py [attachments폴더] [출력폴더]
"""
import sys, os, re, subprocess, tempfile, shutil, zipfile
import xml.etree.ElementTree as ET
from collections import Counter

SRC = sys.argv[1] if len(sys.argv) > 1 else 'data/attachments'
DST = sys.argv[2] if len(sys.argv) > 2 else 'data/attachments-parsed'

ORIG_EXT = ('.hwpx', '.hwp')   # hwpx 우선(개방포맷)

# 도구 가드 — soffice(+H2Orestart)·pandoc 미설치 환경(예: CI ubuntu-latest)에선
# 수집 파이프라인을 깨지 않도록 조용히 skip. 파싱은 도구 갖춘 환경에서 재실행.
_missing = [t for t in ('soffice', 'pandoc') if not shutil.which(t)]
if _missing:
    print(f'[parse skip] {" ".join(_missing)} 미설치 → 별표 파싱 생략 '
          '(수집은 정상, 파싱은 도구 환경에서 재실행)')
    sys.exit(0)


def meta_from_name(stem):
    """'[별표] <제목(…조 관련)>(<부모법령>)' → (title, parent, [제N조...])."""
    body = re.sub(r'^\[별표\]\s*', '', stem)
    m = re.match(r'^(.*)\(([^()]*)\)\s*$', body)   # 마지막 괄호 = 부모법령
    if m:
        title, parent = m.group(1).strip(), m.group(2).strip()
    else:
        title, parent = body.strip(), ''
    arts = list(dict.fromkeys(re.findall(r'제\d+조(?:제\d+항)?(?:제\d+호)?', title)))
    return title, parent, arts


def yaml_list(xs):
    return '[' + ', '.join(xs) + ']'


def clean_md(body):
    """docx→gfm 산출 정리: colgroup 노이즈 + 바깥 페이지-래퍼 표 제거(내용 무손실)."""
    # 1) 표 폭 힌트(colgroup — 법적 정보 아님) 제거
    body = re.sub(r'<colgroup>.*?</colgroup>\s*', '', body, flags=re.S)
    # 2) H2Orestart 페이지 본문 래퍼(100% 단일셀 표) 벗기기.
    #    안전 우선: 문서 전체가 단일 래퍼일 때만 앵커드-그리디로 벗김(내용 절대 안 잃음).
    #    다중페이지(복수 래퍼)는 내용 보존 위해 그대로 둠(경미한 잔여 <table> 허용).
    m = re.match(r'^<table>\s*<tbody>\s*<tr[^>]*>\s*<td>(.*)</td>\s*</tr>\s*'
                 r'</tbody>\s*</table>\s*$', body, re.S)
    if m:
        body = m.group(1)
    return re.sub(r'\n{3,}', '\n\n', body).strip()


def text_len(s):
    """마크업 제외 순수 텍스트 길이(HWP md ↔ PDF 텍스트 비교용)."""
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'[#*|\\_-]', '', s)
    return len(re.sub(r'\s+', '', s))


def pdf_text(path):
    """원본 PDF 의 pdftotext -layout 결과(변환 손실 폴백용)."""
    try:
        return subprocess.run(['pdftotext', '-layout', path, '-'],
                              capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ''


def num_set(s):
    """법적 수치 집합(순서 무관 손실탐지용). 2자리+ 정수·소수만 — 1자리 호·연도 노이즈 축소.
    표 선형화 순서 차이에 둔감(집합이라) → 순서민감 diff 의 오탐을 피하면서 숫자 드롭만 포착."""
    s = re.sub(r'<[^>]+>', ' ', s)                 # 태그 속성 숫자 제외
    return set(re.findall(r'\d+\.\d+|\d{2,}', s.replace(',', '')))


def char_bag(s):
    """콘텐츠 문자 다중집합(Counter). 주 손실감지 — 순서·분절 무관(bag), 텍스트+숫자 전부 포착.
    difflib(순서민감)의 표 선형화 오탐도, 어절집합의 분절 노이즈도 없음. 길이·숫자집합을 포섭."""
    s = re.sub(r'<[^>]+>', ' ', s)                 # 태그
    s = re.sub(r'&[a-z]+;', ' ', s)                 # HTML 엔티티
    s = re.sub(r'[^가-힣0-9A-Za-z]', '', s)          # 콘텐츠 문자만
    return Counter(s)


# ── OWPML(hwpx) 직접 파싱 — LibreOffice 우회(H2Orestart 가 표를 버리는 문제 근본 해결) ──
# hwpx = OWPML(개방형 XML) zip. Contents/section*.xml 을 직접 파싱해 문단+표(HTML) 복원.
def _ln(tag):
    return tag.split('}')[-1]


def _owpml_ptext(p):
    return ''.join(''.join(t.itertext()) for t in p.iter() if _ln(t.tag) == 't')


def _owpml_has_tbl(e):
    return any(_ln(d.tag) == 'tbl' for d in e.iter())


def _owpml_cell(tc):
    sub = next((c for c in tc if _ln(c.tag) == 'subList'), None)
    if sub is None:
        return ''
    out = []
    for p in sub:
        if _ln(p.tag) != 'p':
            continue
        if _owpml_has_tbl(p):
            _owpml_walk(p, out)              # 중첩 표는 HTML 그대로(개행 유지)
        else:
            t = _owpml_ptext(p).strip()
            if t:
                out.append(t)
    return '\n'.join(out)


def _owpml_table(tbl):
    rows = ['<table>']
    for tr in (c for c in tbl if _ln(c.tag) == 'tr'):
        rows.append('<tr>')
        for tc in (c for c in tr if _ln(c.tag) == 'tc'):
            span = next((c for c in tc if _ln(c.tag) == 'cellSpan'), None)
            cs = int(span.get('colSpan', '1')) if span is not None else 1
            rs = int(span.get('rowSpan', '1')) if span is not None else 1
            a = (f' colspan="{cs}"' if cs > 1 else '') + (f' rowspan="{rs}"' if rs > 1 else '')
            rows.append(f'<td{a}>{_owpml_cell(tc)}</td>')
        rows.append('</tr>')
    rows.append('</table>')
    return '\n'.join(rows)


def _owpml_walk(elem, out):
    for ch in elem:
        ln = _ln(ch.tag)
        if ln == 'tbl':
            out.append(_owpml_table(ch))
        elif ln == 'p':
            if _owpml_has_tbl(ch):
                _owpml_walk(ch, out)
            else:
                t = _owpml_ptext(ch).strip()
                if t:
                    out.append(t)
        else:
            _owpml_walk(ch, out)


def parse_hwpx(path):
    """hwpx → 문단+표(HTML) markdown. 실패 시 '' (호출부가 폴백)."""
    try:
        with zipfile.ZipFile(path) as z:
            secs = sorted(n for n in z.namelist()
                          if re.match(r'Contents/section\d+\.xml', n))
            out = []
            for n in secs:
                _owpml_walk(ET.fromstring(z.read(n)), out)
        return re.sub(r'\n{3,}', '\n\n', '\n\n'.join(out)).strip()
    except Exception as e:
        print(f'  ? OWPML 파싱 실패({e}) → docx 폴백: {os.path.basename(path)[:40]}',
              file=sys.stderr)
        return ''


# 별표 원본 수집(hwpx 우선, 같은 stem 은 하나만)
byname = {}
for fn in sorted(os.listdir(SRC)):
    if not fn.startswith('[별표]'):
        continue
    low = fn.lower()
    for ext in ORIG_EXT:
        if low.endswith(ext):
            stem = fn[:-len(ext)]
            # hwpx 우선: 이미 있으면 hwpx 로만 덮음
            if stem not in byname or ext == '.hwpx':
                byname[stem] = fn
            break

srcfiles = [os.path.join(SRC, byname[s]) for s in sorted(byname)]
print(f'별표 원본 {len(srcfiles)}개 파싱 시작 → {DST}')
if not srcfiles:
    sys.exit(0)

os.makedirs(DST, exist_ok=True)
tmp = tempfile.mkdtemp(prefix='hwpparse_')
try:
    # 1) 배치 docx 변환 — **hwp(바이너리)만** soffice 로. hwpx 는 OWPML 직접 파싱(2단계).
    hwp_srcs = [p for p in srcfiles if p.lower().endswith('.hwp')]
    if hwp_srcs:
        subprocess.run(
            ['soffice', '--headless', '--convert-to', 'docx', '--outdir', tmp] + hwp_srcs,
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1200)

    ok, fail, no_table, recovered, flagged, diverged = 0, [], [], [], [], []
    fmt = Counter()
    for stem in sorted(byname):
        srcfn = byname[stem]
        out = os.path.join(DST, stem + '.md')
        ext = os.path.splitext(srcfn)[1].lstrip('.').lower()
        # 2) 본문 추출 — hwpx: OWPML 직접(LibreOffice 우회) / hwp: docx→pandoc
        if ext == 'hwpx':
            body = parse_hwpx(os.path.join(SRC, srcfn))
            if not body:                       # OWPML 실패 시 docx 폴백 시도
                subprocess.run(['soffice', '--headless', '--convert-to', 'docx',
                                '--outdir', tmp, os.path.join(SRC, srcfn)],
                               check=False, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=300)
        else:
            body = ''
        if not body:
            docx = os.path.join(tmp, stem + '.docx')
            if not os.path.exists(docx) or os.path.getsize(docx) < 200:
                fail.append(stem)
                continue
            raw = subprocess.run(
                ['pandoc', '-f', 'docx', '-t', 'gfm', '--wrap=none', docx],
                capture_output=True, text=True).stdout
            body = clean_md(raw)
        # 3) 파싱 손실 감지 — 3층(원본 PDF 대비). 행동 차등:
        #    · 길이 대량손실(pl≫ml)      → PDF 텍스트로 본문 대체(pdf_fallback, 완전성 확실·비가역)
        #    · 문자다중집합 divergence   → 주 감지기. 순서·분절 무관, 텍스트+숫자 전부.
        #      비율 임계(다중페이지 머리말 반복 노이즈 흡수) → 검토 플래그(char_diverge, HWP-md 유지)
        #    · 숫자집합 divergence       → 가중 오버레이. 숫자 1개 오류는 char 차 미미하나 법적 치명 → num_diverge
        #    · PDF 없음 + 본문 빈약      → short_no_pdf(수동 조사)
        #    diverge 는 자동교체 안 함(부분손실 시 구조 좋은 HWP-md 유지, vision/수동이 판정).
        note = ''
        pdfp = os.path.join(SRC, stem + '.pdf')
        if os.path.exists(pdfp):
            ptxt = pdf_text(pdfp)
            ml, pl = text_len(body), text_len(ptxt)
            hbag = char_bag(body)
            htot = sum(hbag.values())
            only_pdf_ch = sum((char_bag(ptxt) - hbag).values())   # PDF 에만 있는 문자 수
            only_pdf_num = len(num_set(ptxt) - num_set(body))       # PDF 에만 있는 숫자
            if pl > ml * 1.8 and pl - ml > 150:
                body = ptxt
                note = 'pdf_fallback'
                recovered.append((stem, ml, pl))
            elif only_pdf_ch > 120 and only_pdf_ch > htot * 0.15:
                note = 'char_diverge'
                diverged.append((stem, 'char', only_pdf_ch, htot))
            elif only_pdf_num >= 5:
                note = 'num_diverge'
                diverged.append((stem, 'num', only_pdf_num, 0))
        elif text_len(body) < 100:
            note = 'short_no_pdf'
            flagged.append(stem)
        title, parent, arts = meta_from_name(stem)
        ext = os.path.splitext(srcfn)[1].lstrip('.').lower()
        fmt[ext] += 1
        note_line = f'parse_note: {note}\n' if note else ''
        fm = (f'---\ntype: 별표\ntitle: "{title}"\nparent_law: "{parent}"\n'
              f'delegating_articles: {yaml_list(arts)}\n'
              f'source: "{srcfn}"\nsource_format: {ext}\n{note_line}---\n\n')
        open(out, 'w', encoding='utf-8').write(fm + body + '\n')
        ok += 1
        if '<table' not in body and '|' not in body:   # 표 자체가 없음 = 순수 텍스트형
            no_table.append(stem)

    total_bytes = sum(os.path.getsize(os.path.join(DST, s + '.md')) for s in byname
                      if os.path.exists(os.path.join(DST, s + '.md')))
    print(f'\n파싱 완료: {ok}개 성공 {dict(fmt)}, 실패 {len(fail)}개')
    print(f'평균 {total_bytes // max(ok, 1)} bytes/파일 (총 {total_bytes} bytes)')
    if fail:
        print('  ✗ 실패:', *[f'\n     - {s}' for s in fail])
    print(f'\nPDF 폴백 복구(길이 대량손실 → PDF 텍스트) {len(recovered)}개:')
    for s, ml, pl in recovered:
        print(f'     - md {ml}자 → PDF {pl}자 | {s[:60]}')
    if diverged:
        print(f'\n⚠ 내용 divergence(길이 OK·PDF 대비 누락 → 검토, HWP-md 유지) {len(diverged)}개:')
        for s, kind, mag, base in diverged:
            if kind == 'char':
                print(f'     - [char] PDF전용 {mag}자 / HWP {base}자 | {s[:52]}')
            else:
                print(f'     - [num]  PDF전용 숫자 {mag}개 | {s[:52]}')
    if flagged:
        print(f'\n⚠ PDF 없음 + 본문 빈약(수동 조사) {len(flagged)}개:')
        for s in flagged:
            print(f'     - {s}')
finally:
    shutil.rmtree(tmp, ignore_errors=True)
