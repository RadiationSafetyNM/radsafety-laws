#!/usr/bin/env python3
"""별표 원본(HWP/HWPX) → 구조보존 markdown 파싱.

파이프라인 산출물(재현 가능) — 수동 변환 금지. 경로 (2026-11-23 개정):
  hwpx            --[OWPML 직접 파싱(_owpml.py)]--> markdown(표는 HTML)
  hwp  --[hwp2hwpx(_hwp2hwpx.py, Java)]--> hwpx --> 위와 같음        ← 주 경로
  hwp  --[LibreOffice + H2Orestart]--> docx --[pandoc]--> gfm        ← 변환·파싱 실패 시 폴백만
  (구 주 경로였던 H2Orestart 는 일부 별표의 표를 통째로 버렸다 — _hwp2hwpx.py docstring 실측 참조.)
  (html 경로는 span lang 노이즈로 ~27배 부풀어 폐기 — docx 리더가 깨끗함.)
  병합셀 표는 gfm 파이프로 표현 불가 → clean HTML <table> 로 보존(LLM 판독 가능).

대상 = **별표만**(실체 규칙 표). 서식·별지는 빈 양식이라 본문 파싱 안 함(메타 레지스트리 별도).
원본 우선(파일링크). 별표는 원본 100% 존재하므로 PDF 빠진 별표(≈16)도 여기서 커버.

전제: java(hwp2hwpx). 폴백용으로 LibreOffice(soffice)+H2Orestart·pandoc 가 있으면 더 좋다.
사용: python3 scripts/_parse_attachments.py [attachments폴더] [출력폴더]
"""
import sys, os, re, json, subprocess, tempfile, shutil, zipfile
import xml.etree.ElementTree as ET
from collections import Counter

SRC = sys.argv[1] if len(sys.argv) > 1 else 'data/attachments'
DST = sys.argv[2] if len(sys.argv) > 2 else 'data/attachments-parsed'
CORR = os.path.join(os.path.dirname(SRC) or '.', 'attachments-corrections.json')

ORIG_EXT = ('.hwpx', '.hwp')   # hwpx 우선(개방포맷)

# 도구 가드 — 주 경로는 java(hwp2hwpx)+순수 파이썬. soffice·pandoc 는 폴백 전용.
# 둘 다 없으면(예: 도구 없는 환경) 수집 파이프라인을 깨지 않도록 조용히 skip.
HAS_JAVA = shutil.which('java') is not None
HAS_LO = all(shutil.which(t) for t in ('soffice', 'pandoc'))
if not HAS_JAVA and not HAS_LO:
    print('[parse skip] java·soffice/pandoc 모두 없음 → 별표 파싱 생략 '
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


# 정상 윗첨자 표기(HTML sup·유니코드 위첨자·캐럿) — 지수가 살아있다는 증거
_SUP = '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⌃'


def math_loss(s):
    """수식 개체 소실 탐지 — PDF 대조가 원리적으로 못 잡는 층(2026-07-31 신설).

    HWP 수식(EQEDIT) 개체가 docx 변환에서 통째로 날아가면 `2.58×10⁻⁵ C/kg` 이
    `2.58×C/kg` 이 된다. **원본 PDF 도 같은 자리를 잃으므로**(pdftotext 결과가 공백)
    char/num divergence 는 그대로 통과한다 → 지수만 사라진 채 법적 수치가 10^n 배 틀린
    본문이 무플래그로 RAG 청크가 된다. 선량·농도 기준에선 치명적.

    보수적 탐지(오탐보다 미탐을 감수): 과학적 표기 자리 — 즉 **숫자**가 앞선 `수×…` 만.
    `수×<숫자아님>`(지수부 통째 소실) 과 `수×10<지수없음>`(지수만 소실) 두 형태.
    앞이 문자면 배수 표기라 제외 — 등급표의 `(D×10)`·`(D×1,000)` 이 실제 오탐이었다.
    반환 = 의심 조각 리스트(빈 리스트면 정상)."""
    s = re.sub(r'<sup>.*?</sup>', '⌃', s, flags=re.S)      # 정상 윗첨자 → 보존 표식
    hits = re.findall(rf'\d[\d.,]*\s*×\s*(?![\d\s{_SUP}])[^\s<]{{0,12}}', s)
    hits += re.findall(rf'\d[\d.,]*\s*×\s*10(?![\d\s\^\-−{_SUP}])[^\s<]{{0,12}}', s)
    return hits


def load_corrections():
    """교정 오버레이(data/attachments-corrections.json) 로드. 없으면 빈 dict."""
    try:
        with open(CORR, encoding='utf-8') as f:
            return json.load(f).get('corrections', {})
    except FileNotFoundError:
        return {}


def apply_corrections(body, rules):
    """파서가 구조적으로 복원 못 하는 자리를 근거 있는 수동 교정으로 되돌린다(2026-07-31).

    파싱본을 직접 손대지 않고 재파싱마다 재적용되므로 산출물은 여전히 재현 가능하다.
    all-or-nothing — rule 의 실제 발생 횟수가 expect 와 다르면 **하나도 적용하지 않고**
    correction_stale 을 낸다. 상류 원본이 개정돼 문장이 바뀌었는데 낡은 교정이 조용히
    덧씌워지는 실패(수동 hwpx 가 새 hwp 를 이기는 것과 같은 양식)를 막는 가드다.
    미적용 시 원문 그대로 남으므로 math_loss 등 감지기가 대신 울린다 — 조용히 넘어가지 않는다.
    반환 = (본문, 'corrected' | 'correction_stale' | '')"""
    if not rules:
        return body, ''
    if any(body.count(r['find']) != r['expect'] for r in rules):
        return body, 'correction_stale'
    for r in rules:
        body = body.replace(r['find'], r['replace'])
    return body, 'corrected'


from _owpml import parse_hwpx   # noqa: E402  (OWPML 파서 — 별도 모듈)
import _hwp2hwpx                 # noqa: E402  (hwp → hwpx 자동 변환)


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
    # 1) hwp → hwpx 자동 변환(hwp2hwpx). 실패분·java 없음만 soffice 배치 docx 로 폴백.
    hwp_srcs = [p for p in srcfiles if p.lower().endswith('.hwp')]
    conv = {}
    if hwp_srcs and HAS_JAVA:
        try:
            conv = _hwp2hwpx.convert([(p, os.path.join(tmp, os.path.basename(p)[:-4] + '.hwpx'))
                                      for p in hwp_srcs])
        except Exception as e:
            print(f'  ? hwp2hwpx 사용 불가({e}) → H2Orestart 폴백', file=sys.stderr)
    lo_srcs = [p for p in hwp_srcs if p not in conv]
    if lo_srcs and HAS_LO:
        subprocess.run(
            ['soffice', '--headless', '--convert-to', 'docx', '--outdir', tmp] + lo_srcs,
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1200)

    ok, fail, no_table, recovered, flagged, diverged = 0, [], [], [], [], []
    math_lost, corrected, corr_stale = [], [], []
    corrections = load_corrections()
    fmt = Counter()
    for stem in sorted(byname):
        srcfn = byname[stem]
        out = os.path.join(DST, stem + '.md')
        ext = os.path.splitext(srcfn)[1].lstrip('.').lower()
        # 2) 본문 추출 — hwpx: OWPML 직접 / hwp: hwp2hwpx 변환본을 OWPML 로 / 실패 시 docx→pandoc
        srcpath = os.path.join(SRC, srcfn)
        via = ''
        if ext == 'hwpx':
            body = parse_hwpx(srcpath)
        elif srcpath in conv:
            body = parse_hwpx(conv[srcpath])
            via = 'hwp2hwpx' if body else ''
        else:
            body = ''
        if not body and HAS_LO and not os.path.exists(os.path.join(tmp, stem + '.docx')):
            subprocess.run(['soffice', '--headless', '--convert-to', 'docx',
                            '--outdir', tmp, srcpath],
                           check=False, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=300)
        if not body:
            docx = os.path.join(tmp, stem + '.docx')
            if not os.path.exists(docx) or os.path.getsize(docx) < 200:
                fail.append(stem)
                continue
            raw = subprocess.run(
                ['pandoc', '-f', 'docx', '-t', 'gfm', '--wrap=none', docx],
                capture_output=True, text=True).stdout
            body = clean_md(raw)
            via = 'h2orestart'
        # 2.5) 교정 오버레이 — 감지 *전*에 적용해야 교정된 자리가 재차 플래그되지 않는다.
        body, cnote = apply_corrections(body, corrections.get(stem, {}).get('rules'))
        if cnote == 'corrected':
            corrected.append(stem)
        elif cnote == 'correction_stale':
            corr_stale.append(stem)
        # 3) 파싱 손실 감지 — 4층(PDF 대조 3층 + 수식 1층). 행동 차등:
        #    · 길이 대량손실(pl≫ml)      → PDF 텍스트로 본문 대체(pdf_fallback, 완전성 확실·비가역)
        #    · 문자다중집합 divergence   → 주 감지기. 순서·분절 무관, 텍스트+숫자 전부.
        #      비율 임계(다중페이지 머리말 반복 노이즈 흡수) → 검토 플래그(char_diverge, HWP-md 유지)
        #    · 숫자집합 divergence       → 가중 오버레이. 숫자 1개 오류는 char 차 미미하나 법적 치명 → num_diverge
        #    · PDF 없음 + 본문 빈약      → short_no_pdf(수동 조사)
        #    · 수식 개체 소실            → math_loss. PDF 도 같이 잃는 층이라 위 3층과 독립
        #      (오버레이 — 다른 note 와 공존 가능). 지수 1개가 10^n 배 오차라 법적 치명.
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
        mhits = math_loss(body)                     # PDF 유무와 무관한 독립 층 → 오버레이
        if mhits:
            note = f'{note}+math_loss' if note else 'math_loss'
            math_lost.append((stem, mhits))
        if cnote:                                    # 교정 이력은 항상 앞에 남긴다
            note = f'{cnote}+{note}' if note else cnote
        title, parent, arts = meta_from_name(stem)
        ext = os.path.splitext(srcfn)[1].lstrip('.').lower()
        fmt[ext] += 1
        note_line = f'parse_note: {note}\n' if note else ''
        if via:
            note_line += f'parse_via: {via}\n'
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
    if corrected:
        print(f'\n🔧 교정 오버레이 적용 {len(corrected)}개:')
        for s in corrected:
            print(f'     - {s[:60]}')
    if corr_stale:
        print(f'\n⛔ 교정 stale {len(corr_stale)}개 — 원본이 바뀌어 교정 규칙이 안 맞습니다. '
              'data/attachments-corrections.json 갱신 필요(미적용 상태로 통과):')
        for s in corr_stale:
            print(f'     - {s[:60]}')
    if math_lost:
        n = sum(len(h) for _, h in math_lost)
        print(f'\n⛔ 수식 개체 소실 의심 {len(math_lost)}개 별표 / {n}곳 '
              '(PDF 대조로는 안 잡히는 층 — 원본 확인 필요):')
        for s, hits in math_lost:
            print(f'     - {s[:56]}')
            for h in hits[:4]:
                print(f'         · {h!r}')
finally:
    shutil.rmtree(tmp, ignore_errors=True)
