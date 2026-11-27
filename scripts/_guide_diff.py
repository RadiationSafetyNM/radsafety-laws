#!/usr/bin/env python3
"""수검가이드 전사본 ↔ OCR(mineru) 쪽 단위 글자 대조 (2026-11-27).

전사(비전 모델)와 OCR 은 서로 독립이라 같은 곳을 똑같이 틀릴 가능성이 낮다. 한글·숫자·영문만 남겨
(띄어쓰기·기호 무시) 견주고, 어긋나는 곳만 사람이 원본 쪽을 열어 확정한다.
사용: python3 scripts/_guide_diff.py <mineru.json> [--pages 11 12] [--digits-only]
"""
import difflib, json, os, re, sys

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'guides', 'kins-2022', 'pages')
CIRC = {chr(0x2460 + i): str(i + 1) for i in range(20)}


def norm(s):
    s = re.sub(r'<!--.*?-->|\[그림:[^\]]*\]|\[빈 쪽\]|</?[a-z][^>]*>', '', s)
    s = ''.join(CIRC.get(c, c) for c in s)
    return re.sub(r'[^0-9A-Za-z가-힣]', '', s)


def texts(node, out):
    if isinstance(node, dict):
        for k in ('content', 'html'):
            if isinstance(node.get(k), str):
                out.append(node[k])
        for v in node.values():
            texts(v, out)
    elif isinstance(node, list):
        for v in node:
            texts(v, out)


def main():
    a = sys.argv[1:]
    pages = [int(x) for x in a[a.index('--pages') + 1:] if x.isdigit()] if '--pages' in a else None
    info = json.load(open(a[0]))['json_structure']['pdf_info']
    total = 0
    for i, pg in enumerate(info, 1):
        if pages and i not in pages:
            continue
        f = f'{DIR}/p{i:03d}.md'
        if not os.path.exists(f):
            print(f'p{i:03d} 전사 없음')
            continue
        o = []
        texts({k: v for k, v in pg.items() if k != 'preproc_blocks'} or pg, o)
        A, B = norm(open(f, encoding='utf-8').read()), norm(' '.join(o))
        sm = difflib.SequenceMatcher(None, A, B, autojunk=False)
        rows = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == 'equal':
                continue
            x, y = A[i1:i2], B[j1:j2]
            if '--digits-only' in a and not re.search(r'\d', x + y):
                continue
            rows.append(f"   {A[max(0, i1 - 10):i1]}【{x}|{y}】{A[i2:i2 + 10]}")
        total += len(rows)
        print(f'p{i:03d} 전사 {len(A)}자 · OCR {len(B)}자 · 일치율 {sm.ratio():.3f} · 어긋남 {len(rows)}')
        for r in rows[:40]:
            print(r)
    print('어긋남 합계', total)


if __name__ == '__main__':
    main()
