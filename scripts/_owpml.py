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
