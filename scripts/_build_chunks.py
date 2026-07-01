#!/usr/bin/env python3
"""법령·고시 → 조(條) 단위 RAG 청크(JSONL). 딥리서치 계층청킹 설계 구현.

설계 권위: vault [[방사선안전법령RAG전수조사계획_딥리서치_2026-06]] §3.
- 청크 단위 = 조(條). 긴 조(>MAXCHARS)는 항(①②…)으로 분할하되 조 헤더 context 유지.
- content = 「법령명」 제N조(제목) prefix + 본문 (임베딩 문맥 보강).
- 메타 스키마: law_id·jurisdiction·legal_hierarchy·domain_tag·document_type·associated_*.
- 링크: 조→별표(parsed 별표 md frontmatter delegating_articles) · 조→서식(본문 '별지 제N호서식').
- 임베딩 없음(제공자 미결정 — Voyage/OpenAI/Gemini). content+metadata 레코드까지가 경계.
  다운스트림(radsafety-pwa)이 이 JSONL 을 임베딩→pgvector upsert.

법령(##### 제N조 헤딩)·고시(제N조( 라인시작) 두 구조 모두 처리.
사용: python3 scripts/_build_chunks.py [laws] [admin] [parsed] [out.jsonl]
"""
import sys, os, re, json, glob
from collections import Counter, defaultdict

LAWS = sys.argv[1] if len(sys.argv) > 1 else 'data/laws'
ADMIN = sys.argv[2] if len(sys.argv) > 2 else 'data/admin-rules'
PARSED = sys.argv[3] if len(sys.argv) > 3 else 'data/attachments-parsed'
OUT = sys.argv[4] if len(sys.argv) > 4 else 'data/chunks/law_chunks.jsonl'

MAXCHARS = 1800   # 이보다 긴 조는 항 단위로 분할

JURIS = {'원자력안전위원회': 'NSSC', '질병관리청': 'KDCA', '보건복지부': 'MOHW',
         '식품의약품안전처': 'MFDS', '고용노동부': 'MoEL', '과학기술정보통신부': 'MSIT',
         '국무총리': 'PMO'}
HIER = {'법률': 'Act', '대통령령': 'Decree', '총리령': 'Rule', '보건복지부령': 'Rule',
        '부령': 'Rule', '고시': 'Notification', '예규': 'Notification', '훈령': 'Notification'}
CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'


def split_front_body(path):
    t = open(path, encoding='utf-8').read()
    m = re.match(r'^---\n(.*?)\n---\n?(.*)$', t, re.S)
    return (m.group(1), m.group(2)) if m else ('', t)


def fval(fm, key):
    m = re.search(rf'^{key}:\s*[\'"]?(.+?)[\'"]?\s*$', fm, re.M)
    return m.group(1).strip() if m else ''


def flist_first(fm, key):
    """YAML 리스트 첫 항목 (예: 소관부처:\\n- 보건복지부)."""
    m = re.search(rf'^{key}:\s*\n\s*-\s*[\'"]?(.+?)[\'"]?\s*$', fm, re.M)
    if m:
        return m.group(1).strip()
    return fval(fm, key)


def norm_article(s):
    """'제4조제6항' → '제4조' (조 번호까지만; 조의N 유지)."""
    m = re.match(r'(제\d+조(?:의\d+)?)', s)
    return m.group(1) if m else s


# ── 별표 링크맵: parsed 별표 md frontmatter(parent_law·delegating_articles) → (parent, 제N조) → [md] ──
att_map = defaultdict(list)
for mdp in glob.glob(os.path.join(PARSED, '*.md')):
    fm, _ = split_front_body(mdp)
    parent = fval(fm, 'parent_law')
    arts = fval(fm, 'delegating_articles')      # "[제2조제4호]" 형태
    stem = os.path.basename(mdp)[:-3]
    for a in re.findall(r'제\d+조(?:의\d+)?', arts):
        att_map[(parent, a)].append(stem)


def article_chunks_law(body):
    """법령: ##### 제N조 (제목) 헤딩 기준 분할."""
    pat = re.compile(r'^#{3,6}\s*(제\d+조(?:의\d+)?)\s*(?:\(([^)]*)\))?\s*$', re.M)
    return _slice(body, pat)


def article_chunks_admin(body):
    """고시: 제N조(제목) 라인시작 기준 분할."""
    pat = re.compile(r'^(제\d+조(?:의\d+)?)\(([^)]*)\)', re.M)
    return _slice(body, pat)


def _slice(body, pat):
    out = []
    ms = list(pat.finditer(body))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(body)
        art = m.group(1)
        title = (m.group(2) or '').strip()
        text = body[m.end():end].strip()
        out.append((art, title, text))
    return out


HO = re.compile(r'(?m)^\s*(\d+)\\?\.\s')   # 호 마커: 줄 시작 "N." 또는 "N\."(법령 이스케이프)


def _pack_ho(text):
    """호(1. 2. …)를 MAXCHARS 이하로 greedy 패킹 → [(라벨, 조각)]. 호<2 면 []."""
    om = list(HO.finditer(text))
    if len(om) < 2:
        return []
    raw = []
    if om[0].start() > 0:
        head = text[:om[0].start()].strip()
        if head:
            raw.append((None, head))
    for i, m in enumerate(om):
        end = om[i + 1].start() if i + 1 < len(om) else len(text)
        raw.append((m.group(1), text[m.start():end].strip()))
    parts, cur, nums, clen = [], [], [], 0

    def flush():
        if not cur:
            return
        ns = [n for n in nums if n]
        lab = (f'제{ns[0]}호~제{ns[-1]}호' if len(ns) > 1
               else (f'제{ns[0]}호' if ns else ''))
        parts.append((lab, '\n'.join(cur).strip()))

    for num, seg in raw:
        if cur and clen + len(seg) > MAXCHARS:
            flush()
            cur, nums, clen = [], [], 0
        cur.append(seg)
        nums.append(num)
        clen += len(seg)
    flush()
    return parts


def split_long(text):
    """긴 조 분할 — (subunit라벨, 조각) 리스트.
    ① 항(①②…) 있으면 각 항이 한 청크. 오버사이즈 항은 내부 호로 재분할(항+호 라벨).
    ② 항 없고 호만 있으면 호를 MAXCHARS 이하로 패킹.
    ③ 둘 다 없으면 통짜."""
    hm = list(re.finditer(rf'(?:\*\*)?([{CIRCLED}])(?:\*\*)?', text))
    if len(hm) >= 2:
        segs = []
        if hm[0].start() > 0:
            head = text[:hm[0].start()].strip()
            if head:
                segs.append(('', head))
        for i, m in enumerate(hm):
            end = hm[i + 1].start() if i + 1 < len(hm) else len(text)
            segs.append((m.group(1), text[m.start():end].strip()))
        out = []
        for lab, seg in segs:                 # 오버사이즈 항은 호로 재분할
            if len(seg) > MAXCHARS:
                ho = _pack_ho(seg)
                if len(ho) >= 2:
                    out.extend((f'{lab} {hl}'.strip(), hs) for hl, hs in ho)
                    continue
            out.append((lab, seg))
        return out
    ho = _pack_ho(text)
    return ho if ho else [('', text)]


def build(path, doctype):
    fm, body = split_front_body(path)
    stem = os.path.basename(path)[:-3]
    if doctype == 'law':
        title = fval(fm, '제목')
        law_id = fval(fm, '법령ID') or fval(fm, '법령MST')
        mst = fval(fm, '법령MST')
        gubun = fval(fm, '법령구분')
        dept = flist_first(fm, '소관부처')
        enforce, promul = fval(fm, '시행일자'), fval(fm, '공포일자')
        status = fval(fm, '상태')
        chunks = article_chunks_law(body)
    else:
        title = fval(fm, '행정규칙명')
        law_id = fval(fm, '행정규칙ID')
        mst = fval(fm, '행정규칙일련번호')
        gubun = fval(fm, '행정규칙종류')
        dept = fval(fm, '소관부처명')
        enforce, promul = fval(fm, '시행일자'), fval(fm, '발령일자')
        status = fval(fm, '제개정구분')
        chunks = article_chunks_admin(body)
    source = fval(fm, '출처')
    juris = JURIS.get(dept, dept)
    hier = HIER.get(gubun, gubun)

    recs = []
    for art, atitle, text in chunks:
        if not text:
            continue
        # 삭제된 조(내용 0 — 벡터 노이즈) 제외
        if re.match(r'^삭제\s*(&lt;.*?&gt;|<[^>]*>)?\s*$', text):
            deleted[0] += 1
            continue
        art_no = norm_article(art)
        atts = att_map.get((stem, art_no), [])
        forms = list(dict.fromkeys(re.findall(r'별지 제\d+호(?:의\d+)?서식', text)))
        # 긴 조는 항→호 순으로 분할(MAXCHARS 이하)
        segs = split_long(text) if len(text) > MAXCHARS else [('', text)]
        for sub, seg in segs:
            head = f'「{title}」 {art}' + (f'({atitle})' if atitle else '')
            if sub:
                head += f' {sub}'
            content = head + '\n\n' + seg
            cid = f'{law_id}#{art}' + (f'_{sub}' if sub else '')
            recs.append({
                'chunk_id': cid,
                'content': content,
                'metadata': {
                    'law_id': law_id, 'law_mst': mst, 'law_title': title,
                    'jurisdiction': juris, 'legal_hierarchy': hier,
                    'document_type': doctype, 'article': art,
                    'article_title': atitle, 'subunit': sub,
                    'enforce_date': enforce, 'promulgate_date': promul,
                    'status': status,
                    'associated_attachments': atts,
                    'referenced_forms': forms,
                    'source': source,
                },
            })
    return recs


# ── 실행 ──
deleted = [0]
os.makedirs(os.path.dirname(OUT), exist_ok=True)
allrecs = []
for folder, dt in ((LAWS, 'law'), (ADMIN, 'admin_rule')):
    if not os.path.isdir(folder):
        continue
    for fn in sorted(os.listdir(folder)):
        if fn.endswith('.md'):
            allrecs.extend(build(os.path.join(folder, fn), dt))

with open(OUT, 'w', encoding='utf-8') as f:
    for r in allrecs:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

# ── 통계 ──
by_dt = Counter(r['metadata']['document_type'] for r in allrecs)
by_j = Counter(r['metadata']['jurisdiction'] for r in allrecs)
by_h = Counter(r['metadata']['legal_hierarchy'] for r in allrecs)
w_att = sum(1 for r in allrecs if r['metadata']['associated_attachments'])
w_form = sum(1 for r in allrecs if r['metadata']['referenced_forms'])
split = sum(1 for r in allrecs if r['metadata']['subunit'])
lens = [len(r['content']) for r in allrecs]
avg = sum(lens) // max(len(allrecs), 1)
over = sum(1 for x in lens if x > MAXCHARS + 400)   # 분할 후에도 큰 청크(항/호 없는 통짜)
print(f'청크 {len(allrecs)}개 → {OUT} (삭제 조 {deleted[0]}개 제외)')
print(f'  document_type: {dict(by_dt)}')
print(f'  jurisdiction:  {dict(by_j)}')
print(f'  legal_hierarchy: {dict(by_h)}')
print(f'  별표 연결 청크: {w_att} · 서식 참조 청크: {w_form} · 항/호 분할 청크: {split}')
print(f'  평균 content 길이: {avg}자 · 최대 {max(lens)}자 · MAXCHARS+400 초과(통짜) {over}개')
