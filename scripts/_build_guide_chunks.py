#!/usr/bin/env python3
"""수검 가이드 전사본(data/guides/kins-2022/pages) → 점검 항목 단위 청크 (2026-11-27).

산출 = data/chunks/guide_chunks.jsonl. 법령 청크(law_chunk_versions.jsonl)와 **섞지 않는다** — 지침은
구속력이 없는 실무 안내라 자료 등급(document_type=guide)으로 따로 검색한다.
쪼개는 단위: 편(Part) > 장(Ⅰ~Ⅳ) > 절(숫자 제목) > 소절(가. 나. · 그 밖의 제목). 한 절이 길면 '○' 항목 경계에서 나눈다.
제목 깊이는 '#' 개수가 아니라 글 모양으로 정한다(전사한 묶음마다 '#' 깊이가 달랐다).
"""
import glob, json, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
PAGES = f'{ROOT}/data/guides/kins-2022/pages'
OUT = f'{ROOT}/data/chunks/guide_chunks.jsonl'
GUIDE = '방사선분야 정기검사 수검 가이드(2022년 개정)'
PARTS = [(9, 41, 'Part 1 RI등 허가사용자 정기검사'), (45, 60, 'Part 2 업무대행자 정기검사'),
         (63, 75, 'Part 3 방사성물질등 포장 및 운반 정기검사')]
MAX, MIN = 1600, 200
ROMAN = re.compile(r'^(Ⅰ|Ⅱ|Ⅲ|Ⅳ|IV|III|II|I)\s+(\S.*)$')
NUM = re.compile(r'^(\d{1,2})\s+([가-힣].*)$')
SUB = re.compile(r'^([가-하])\.\s+(\S.*점검사항.*)$')
CITE = re.compile(r'(원자력안전법\s*시행령|원자력안전법\s*시행규칙|원자력안전법|시행령|시행규칙|법|영|규칙)[\]」\s]*제\s?(\d+)조(의\s?\d+)?')


def law_of(n):
    n = re.sub(r'\s', '', n)
    if '시행령' in n or n == '영':
        return '원자력안전법 시행령'
    if '시행규칙' in n or n == '규칙':
        return '원자력안전법 시행규칙'
    return '원자력안전법'


def cites(text, bare=False):
    """지침이 인용한 조문. 「법/영/규칙」 약칭은 원자력안전법 계열로 본다(이 문서의 용례).
    bare: 행정처분 기준 장은 "제53조(…)" 처럼 법 이름 없이 적는다 — 원자력안전법 조문이다."""
    got = []
    if bare:
        for m in re.finditer(r'(?<![가-힣] )(?<![가-힣])제(\d+)조(의\d+)?(?=[(제를 ]|$)', text):
            c = f"원자력안전법 제{m.group(1)}조{m.group(2) or ''}"
            if c not in got:
                got.append(c)
    for m in CITE.finditer(text):
        c = f"{law_of(m.group(1))} 제{m.group(2)}조" + (re.sub(r'\s', '', m.group(3)) if m.group(3) else '')
        if c not in got:
            got.append(c)
    return got


PUBLISHED = '2022-10-31'      # 개정 6판 발행 2022년 10월(판권 쪽) — 이 뒤의 개정은 지침에 반영되지 않았다
LAWS = f'{ROOT}/data/chunks/law_chunk_versions.jsonl'


def law_status(today):
    """(법령명, 조) → {'amended': 발간 뒤 마지막 개정일, 'upcoming': 시행예정 변경일}. 현행 판본 본문의 <개정 …> 꼬리표 기준."""
    cur, up = {}, {}
    for line in open(LAWS, encoding='utf-8'):
        d = json.loads(line)
        m = d['metadata']
        if m.get('document_type') != 'law':
            continue
        k = (m['law_title'], m['article'])
        if d['effective_from'] <= today and (d['effective_to'] is None or d['effective_to'] > today):
            cur.setdefault(k, []).append(d['content'])
            if d['effective_to']:
                up[k] = min(up.get(k, '9999'), d['effective_to'])
        elif d['effective_from'] > today:
            up[k] = min(up.get(k, '9999'), d['effective_from'])
    out = {}
    for k, bodies in cur.items():
        tags = ' '.join(t for b in bodies for t in re.findall(r'<(?:개정|신설|본조신설|전문개정)[^>]*>', b))
        ds = [f'{y}-{int(mo):02d}-{int(dd):02d}' for y, mo, dd in re.findall(r'(20\d\d)\.\s?(\d+)\.\s?(\d+)', tags)]
        last = max(ds, default='')
        if last > PUBLISHED or k in up:
            out[k] = {'amended': last if last > PUBLISHED else None, 'upcoming': up.get(k)}
    return out, set(cur)


def main():
    import datetime
    status, known = law_status(str(datetime.date.today()))
    unknown = set()
    chunks, cur = [], None
    path = {'part': '', 'chap': '', 'sec': '', 'sub': '', 'sub2': ''}

    def flush():
        nonlocal cur
        if cur and ''.join(cur['lines']).strip():
            chunks.append(cur)
        cur = None

    def start(page):
        nonlocal cur
        flush()
        cur = {'path': dict(path), 'lines': [], 'pages': [page]}

    for part_from, part_to, part in PARTS:
        path.update(part=part, chap='', sec='', sub='', sub2='')
        for n in range(part_from, part_to + 1):
            for raw in open(f'{PAGES}/p{n:03d}.md', encoding='utf-8').read().split('\n'):
                if raw.startswith('<!-- page') or raw.strip() == '[빈 쪽]':
                    continue
                head = re.match(r'^#{1,4}\s+(.*)$', raw)
                t = re.sub(r'\*\*', '', head.group(1)).strip() if head else None
                if t and ROMAN.match(t):
                    path.update(chap=t, sec='', sub='', sub2='')
                    start(n)
                elif t and NUM.match(t):
                    path.update(sec=t, sub='', sub2='')
                    start(n)
                elif t and SUB.match(t):
                    path.update(sub=t, sub2='')
                    start(n)
                elif t:                      # 그 밖의 제목(수검근거·원자력안전법·수검 준비 자료 등)
                    path.update(sub2=re.sub(r'\s+', ' ', t) if len(t) > 9 else t.replace(' ', ''))   # 분야(가. 나.) 아래에 둔다
                    start(n)
                else:
                    if cur is None:
                        start(n)
                    if re.match(r'^(○ |[①-⑳] )', raw) and sum(len(x) for x in cur['lines']) > MAX * 0.6:
                        start(n)             # 긴 절은 '○' 항목·①② 표 제목 경계에서 나눈다
                    elif raw.startswith('- ') and sum(len(x) for x in cur['lines']) > MAX * 1.1:
                        start(n)             # '○' 가 없는 긴 절은 맨 앞 '-' 항목에서
                    cur['lines'].append(raw + '\n')
                    if n not in cur['pages']:
                        cur['pages'].append(n)
    flush()

    # 짧은 조각은 같은 절의 앞 조각에 붙인다
    merged = []
    for c in chunks:
        body = ''.join(c['lines']).strip()
        if merged and len(body) < MIN and merged[-1]['path'] == c['path']:
            merged[-1]['lines'] += ['\n'] + c['lines']
            merged[-1]['pages'] += [p for p in c['pages'] if p not in merged[-1]['pages']]
        else:
            merged.append(c)

    seen, rows = {}, []
    for c in merged:
        p = c['path']
        body = re.sub(r'\n{3,}', '\n\n', ''.join(c['lines'])).strip()
        crumbs = [x for x in (p['part'], p['chap'], p['sec'], p['sub'], p['sub2']) if x]
        key = 'kins2022#' + '/'.join(re.sub(r'[^0-9A-Za-z가-힣ⅠⅡⅢⅣ]', '', x)[:14] for x in crumbs[:1] + crumbs[1:])
        seen[key] = seen.get(key, 0) + 1
        cid = key if seen[key] == 1 else f'{key}_p{seen[key]}'
        cs = cites(body, bare='위반에 따른 행정처분' in p['chap'])
        changed = []
        for c_ in cs:
            law, art = c_.rsplit(' ', 1)
            if (law, art) not in known:
                unknown.add(c_)
            elif (law, art) in status:
                changed.append({'cite': c_, **status[(law, art)]})
        printed = f"{c['pages'][0] - 6}" + (f"~{c['pages'][-1] - 6}" if len(c['pages']) > 1 else '')
        rows.append({
            'chunk_id': cid, 'effective_from': '2022-01-01', 'effective_to': None,
            'content': f"「{GUIDE}」 {' > '.join(crumbs)} (p.{printed})\n\n{body}",
            'metadata': {'document_type': 'guide', 'guide_id': 'kins2022', 'law_title': GUIDE,
                         'publisher': '한국원자력안전기술원(KINS)', 'edition': '2022', 'binding': False,
                         'part': p['part'], 'chapter': p['chap'], 'section': p['sec'], 'subsection': ' > '.join(x for x in (p['sub'], p['sub2']) if x),
                         'pdf_pages': c['pages'], 'printed_pages': printed, 'cites': cs, 'cites_changed': changed}})
    ids = [r['chunk_id'] for r in rows]
    if len(ids) != len(set(ids)):
        sys.exit('chunk_id 중복 — 산출하지 않음')
    with open(OUT, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    src = sum(len(re.sub(r'\s', '', open(x, encoding='utf-8').read())) for x in glob.glob(f'{PAGES}/p*.md'))
    out = sum(len(re.sub(r'\s', '', ''.join(c['lines']))) for c in merged)
    print(f'청크 {len(rows)} · 본문 {out:,}자(공백 제외) / 전사본 전체 {src:,}자 · 최장 {max(len(r["content"]) for r in rows)} · 최단 {min(len(r["content"]) for r in rows)}')
    print(f'인용 조문 중 발간(2022-10) 뒤 개정·변경예정이 있는 청크 {sum(bool(r["metadata"]["cites_changed"]) for r in rows)} · 현행 법령에서 못 찾은 인용 {sorted(unknown)}')
    if '-v' in sys.argv:
        for r in rows:
            print(f"{len(r['content']):5d} {r['chunk_id']}  cites={len(r['metadata']['cites'])}")


if __name__ == '__main__':
    main()
