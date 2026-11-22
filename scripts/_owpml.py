#!/usr/bin/env python3
"""OWPML(hwpx) 직접 파싱 — 문단+표(HTML). _parse_attachments.py 에서 분리(2026-11-23, hwp2hwpx 자가검사가 재사용).

LibreOffice 를 거치지 않는다(H2Orestart 가 표를 버리는 문제의 근본 해결). 순수 파이썬 — CI 에서도 돈다.
"""
import os, re, sys, zipfile
import xml.etree.ElementTree as ET


# ── OWPML(hwpx) 직접 파싱 — LibreOffice 우회(H2Orestart 가 표를 버리는 문제 근본 해결) ──
# hwpx = OWPML(개방형 XML) zip. Contents/section*.xml 을 직접 파싱해 문단+표(HTML) 복원.
def _ln(tag):
    return tag.split('}')[-1]


def _eq(script):
    """수식 개체(hp:equation) → 한컴 수식 스크립트를 그대로 남긴다(2026-11-23).
    OWPML 은 수식을 글(t)이 아니라 <hp:script> 로 품어서, 글만 모으면 수식이 통째로 사라진다
    (예: 부담금 산정기준·등급별 최소 방사능의 산식). LaTeX 로 바꾸지 않고 원문 스크립트를
    표지와 함께 둔다 — 'A over B'·'sum' 등은 LLM 이 읽을 수 있고, 변환 오류의 여지가 없다."""
    t = re.sub(r'\s+', ' ', (script.text or '').replace('`', ' ')).strip()
    return f' [수식: {t}] ' if t else ''


def _owpml_ptext(p):
    out = []
    for e in p.iter():
        ln = _ln(e.tag)
        if ln == 't':
            out.append(''.join(e.itertext()))
        elif ln == 'script':
            out.append(_eq(e))
    return ''.join(out)


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
    # 표 캡션(hp:caption) — tr 만 돌면 캡션 글이 빠진다(2026-11-23 vault 보도자료 실측: 표 제목·
    # 참여 단체 목록이 캡션에 있었다). 표 바로 앞 문단으로 남긴다.
    cap = [_owpml_ptext(p).strip() for c in tbl if _ln(c.tag) == 'caption'
           for p in c.iter() if _ln(p.tag) == 'p']
    rows = (['\n'.join(x for x in cap if x), ''] if any(cap) else []) + ['<table>']
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
        elif ln == 'script':
            out.append(_eq(ch).strip())
        elif ln == 't':
            # 표를 품은 문단은 위에서 통째로 재귀하는데, 그 문단의 글(run 안의 t)을 예전엔
            # 아무 분기도 안 받아 버렸다(2026-11-23 vault hwp 830건 대조에서 발견 — 표 옆
            # 문단 글·표 앞 제목이 소리 없이 사라짐). 문서 순서대로 글을 남긴다.
            t = ''.join(ch.itertext()).strip()
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
