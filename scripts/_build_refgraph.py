#!/usr/bin/env python3
"""조문 참조 그래프 — 현행 청크 본문에서 다른 조문·별표를 가리키는 말을 정규식으로 뽑는다 (2026-11-27).

LLM 을 쓰지 않는다. 마디 = (법령명, 제N조 | 별표N). 간선 = "이 조문이 저 조문을 가리킨다".
  · 「법령명」 제N조 / 「법령명」 별표 N           → 그 법령
  · 법 제N조 · 영 제N조 · 규칙 제N조               → 같은 계열의 법률 · 시행령 · 시행규칙
  · 맨 "제N조" · "별표 N"                          → 같은 법령
가리킨 대상이 현행 코퍼스에 실재할 때만 간선으로 남긴다(없는 조문·남의 법령은 버림).
산출 = data/chunks/refgraph.json {마디: [가리키는 마디…]} · 사용: python3 scripts/_build_refgraph.py [--today=YYYY-MM-DD]
"""
import datetime, json, os, re, sys
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SRC, OUT = f'{ROOT}/data/chunks/law_chunk_versions.jsonl', f'{ROOT}/data/chunks/refgraph.json'
sq = lambda s: re.sub(r'[\s·ㆍ]', '', s or '')
ART = r'제\s?(\d+)\s?조(?:\s?의\s?(\d+))?'
BYEOL = r'별표\s?(\d+)(?:\s?의\s?(\d+))?'
# 고시의 "법·영·규칙"이 가리키는 계열 — 소관으로 정한다(원안위 고시 = 원자력안전법, 복지부·식약처 = 의료법).
FAMILY_BY_JUR = {'NSSC': '원자력안전법', 'MOHW': '의료법', 'MFDS': '의료기기법'}
PARENT_OF_RULE = {'진단용 방사선 발생장치의 안전관리에 관한 규칙': '의료법', '특수의료장비의 설치 및 운영에 관한 규칙': '의료법'}


def node(law, kind, n, sub):
    return f"{law}#{'별표' if kind == 'b' else '제'}{n}{'' if kind == 'b' else '조'}{'의' + sub if sub else ''}"


def node_of(m):
    if m.get('document_type') == 'attachment':
        b = re.match(r'별표(\d+)(?:의(\d+))?', sq(m.get('attachment_no', '')))
        return node(m['law_title'], 'b', b.group(1), b.group(2)) if b else None
    a = re.match(r'제(\d+)조(?:의(\d+))?', m.get('article', ''))
    return node(m['law_title'], 'a', a.group(1), a.group(2)) if a else None


def family(m):
    t = m['law_title']
    for suf in (' 시행령', ' 시행규칙'):
        if t.endswith(suf):
            return t[:-len(suf)]
    if t in PARENT_OF_RULE:
        return PARENT_OF_RULE[t]
    if m.get('legal_hierarchy') == 'Act':
        return t
    return FAMILY_BY_JUR.get(m.get('jurisdiction', ''), '')


def refs(text, m, titles):
    fam, own, out = family(m), m['law_title'], set()
    body = text.split('\n\n', 1)[-1]                       # 첫 줄(자기 제목)은 뺀다
    body = re.sub(r'<[^>]*>', '', body)                    # <개정 …> 꼬리표
    # ① 「법령명」 뒤에 오는 조·별표 (같은 문장 안 40자까지)
    for q in re.finditer(r'「([^」]{2,60})」([^「。\n]{0,40})', body):
        law = titles.get(sq(q.group(1)))
        if not law:
            continue
        for a in re.finditer(ART, q.group(2)):
            out.add(node(law, 'a', a.group(1), a.group(2)))
        for b in re.finditer(BYEOL, q.group(2)):
            out.add(node(law, 'b', b.group(1), b.group(2)))
    rest = re.sub(r'「[^」]{2,60}」[^「。\n]{0,40}', ' ', body)
    # ② 법/영/규칙 제N조 — 계열 안에서 푼다
    for q in re.finditer(r'(?<![가-힣])(법|영|규칙)\s?' + ART, rest):
        law = {'법': fam, '영': f'{fam} 시행령', '규칙': f'{fam} 시행규칙'}[q.group(1)] if fam else ''
        if law:
            out.add(node(law, 'a', q.group(2), q.group(3)))
    rest = re.sub(r'(?<![가-힣])(법|영|규칙)\s?' + ART, ' ', rest)
    # ③ 맨 제N조·별표 N — 같은 법령
    for a in re.finditer(r'(?<![0-9가-힣])' + ART, rest):
        out.add(node(own, 'a', a.group(1), a.group(2)))
    for b in re.finditer(BYEOL, rest):
        out.add(node(own, 'b', b.group(1), b.group(2)))
    return out


def main():
    kst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date().isoformat()
    today = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--today=')), kst)
    rows = []
    for line in open(SRC, encoding='utf-8'):
        d = json.loads(line)
        if d['effective_from'] <= today and (d['effective_to'] is None or d['effective_to'] > today):
            rows.append(d)
    titles = {sq(d['metadata']['law_title']): d['metadata']['law_title'] for d in rows}
    nodes = {n for n in (node_of(d['metadata']) for d in rows) if n}
    g, dropped = defaultdict(set), 0
    for d in rows:
        me = node_of(d['metadata'])
        if not me:
            continue
        for r in refs(d['content'], d['metadata'], titles):
            if r == me:
                continue
            if r in nodes:
                g[me].add(r)
            else:
                dropped += 1
    out = {k: sorted(v) for k, v in sorted(g.items())}
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    e = sum(len(v) for v in out.values())
    cross = sum(1 for k, v in out.items() for t in v if t.split('#')[0] != k.split('#')[0])
    print(f'마디 {len(nodes)} · 가리키는 쪽 {len(out)} · 간선 {e}(다른 법령으로 {cross}) · 대상이 코퍼스에 없어 버린 참조 {dropped}')
    deg = sorted(((len(v), k) for k, v in out.items()), reverse=True)[:5]
    print('가장 많이 가리키는 조문:', [(n, k) for n, k in deg])


if __name__ == '__main__':
    main()
