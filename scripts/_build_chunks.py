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
# ⚠️ sorted() 필수 — glob 은 파일시스템 순서를 그대로 준다. kimbi(ext4)와 CI(ubuntu)에서
# 순서가 달라져 `associated_attachments` 목록의 **정렬만** 바뀐 diff 가 매 CI 마다 생겼다
# (2026-08-03 첫 실전 CI 에서 11개 청크가 그렇게 바뀌었다 — 내용은 동일). 산출물이
# 결정적이어야 "이번에 진짜 뭐가 바뀌었나"를 diff 로 판별할 수 있다.
for mdp in sorted(glob.glob(os.path.join(PARSED, '*.md'))):
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
    """고시: 미러 형식이 **두 가지**라 헤딩형을 먼저 시도하고 라인시작형으로 폴백한다.

    ⚠️ 2026-07-05 커밋(`dd06c7f`)에서 admrule-kr 이 고시 본문을 `제N조(제목)` 라인시작에서
    법령과 같은 `##### 제N조 (제목)` 헤딩으로 바꿨다. 라인시작 패턴만 보던 구 코드는 그 뒤로
    **조문 0개를 반환**했고, 청크 파일에 남아 있던 고시 375청크는 07-05 이전 산출물이었다
    (3주 넘게 stale). 파싱 실패가 예외가 아니라 빈 결과로 나타나 조용히 지나간 사례다.
    → 아래 `empty_docs` 가드가 같은 침묵을 막는다.
    """
    out = article_chunks_law(body)                      # ① 신형: ##### 제N조
    if out:
        return out
    pat = re.compile(r'^(제\d+조(?:의\d+)?)\(([^)]*)\)', re.M)   # ② 구형: 제N조(
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


# ── 별표 청킹 (2026-07-31 신설) ────────────────────────────────────────────
# 왜: 별표는 "부칙 같은 형식 조항"이 아니라 **기준·수치가 실제로 사는 곳**이다.
# 지금까지 별표는 조문 청크의 associated_attachments 링크로만 존재해, "선량한도가
# 얼마인가" 같은 질문에 검색이 조문("별표 2와 같다")에서 멈췄다. 별표 자체를 청크로 만든다.
#
# 두 종류가 섞여 있어 단위가 하나로 안 된다(실측 84건):
#   · 서술형 — 본문 중앙값 1,760자. 통째로 1청크가 맞다.
#   · 데이터 테이블형 — 최대 232,030자(핵종별 수천 행). 행 단위로 쪼개야 검색이 닿는다.
# 그래서 MAXCHARS(조문과 동일 기준) 이하면 통짜, 넘으면 표 행 단위로 패킹한다.
# **패킷마다 표 헤더를 반복**해 넣는다 — 안 그러면 `1E+06` 같은 숫자만 남아 무엇의 값인지 잃는다.
#
# HTML 표(pandoc 산출, 84건 중 70건)는 태그가 임베딩 노이즈라 ` | ` 구분 평문으로 편다.
import html as _html

TABLE_RE = re.compile(r'<table.*?</table>', re.S | re.I)
TR_RE = re.compile(r'<tr.*?</tr>', re.S | re.I)
CELL_RE = re.compile(r'<t[dh][^>]*>(.*?)</t[dh]>', re.S | re.I)
TAG_RE = re.compile(r'<[^>]+>')


def _cell(s):
    return re.sub(r'\s+', ' ', _html.unescape(TAG_RE.sub(' ', s))).strip()


def _plain(s):
    return re.sub(r'\n{3,}', '\n\n', _html.unescape(TAG_RE.sub('', s))).strip()


def _rows(tbl):
    out = []
    for tr in TR_RE.findall(tbl):
        cells = [_cell(c) for c in CELL_RE.findall(tr)]
        if any(cells):
            out.append(' | '.join(cells))
    return out


def _seg_index(header, lines):
    """표 조각 맨 앞에 붙일 '수록 항목' 색인 — 첫 열(핵종·항목명) 나열.

    값 조회형 별표(핵종별 A1/A2·연간섭취한도 등)는 한 조각에 수십 행이 들어가, 찾는 핵종이
    조각 안에 *있는데도* 신호가 희석돼 검색에서 밀린다(2026-08-01 Q36 I-131 실측: 답이 든
    조각이 @5 밖). 첫 열을 조각 머리에 모아두면 그 항목의 tf 가 오르고 제목 옆에 붙어
    어휘·벡터 양쪽에서 잡히기 쉬워진다. 내용 추가가 아니라 **이미 있는 값의 재배치**다."""
    keys = []
    for ln in lines:
        c = ln.split('|')[0].strip()
        if c and len(c) <= 20 and c not in keys:
            keys.append(c)
    return f'[수록 항목] {", ".join(keys)}' if len(keys) >= 3 else ''


def attachment_segments(body):
    """[(subunit라벨, 조각)] — 표는 행 패킹 + 헤더 반복, 표 밖 텍스트는 그대로."""
    if len(_plain(body)) <= MAXCHARS and len(body) <= MAXCHARS * 3:
        return [('', _plain(body))]           # 서술형·짧은 표 → 통짜
    segs, pos, tidx = [], 0, 0
    for m in TABLE_RE.finditer(body):
        pre = _plain(body[pos:m.start()])
        if pre:
            segs.append(('', pre))
        tidx += 1
        rows = _rows(m.group(0))
        if rows:
            header, data = rows[0], rows[1:]
            if not data:                       # 헤더뿐인 표
                segs.append((f'표{tidx}', header))
            def _pack(lines, a, b):
                idx = _seg_index(header, lines)
                body_ = header + '\n' + '\n'.join(lines)
                return (f'표{tidx} 행{a}~{b}', (idx + '\n' + body_) if idx else body_)

            # 색인 길이를 패킹 예산에 포함 — 뒤에 붙이면 조각이 MAXCHARS 를 넘고, 임베딩
            # 단계의 CAP 절단에서 꼬리 행이 잘려 나간다(색인 도입 시 통짜초과 15→32개였다).
            cur, start, clen = [], 1, len(header)
            for i, line in enumerate(data, start=1):
                if cur and clen + len(line) + len(_seg_index(header, cur)) > MAXCHARS:
                    segs.append(_pack(cur, start, i - 1))
                    cur, start, clen = [], i, len(header)
                cur.append(line)
                clen += len(line)
            if cur:
                segs.append(_pack(cur, start, len(data)))
        pos = m.end()
    tail = _plain(body[pos:])
    if tail:
        segs.append(('', tail))
    return segs or [('', _plain(body))]


ATT_NO_RE = re.compile(r'\\?\[\s*별표\s*(\d+)(?:\s*의\s*(\d+))?\s*\\?\]')


def build_attachment(path, parent_meta):
    """parsed 별표 md 1건 → 청크들. 모법 메타(law_id·소관·위계)를 상속한다."""
    fm, body = split_front_body(path)
    base = os.path.basename(path)[:-3]
    parent = fval(fm, 'parent_law')
    pm = parent_meta.get(parent)
    if not pm:
        # 폴백 — 파싱기가 `parent_law` 를 못 채운 경우가 있다(파일명 괄호 중첩:
        # "…(교육훈련 포함)의 내용…" 처럼 모법명 안에 괄호가 또 있으면 추출이 어긋난다).
        # 파일명에 모법 stem 이 그대로 들어가므로 **가장 긴 일치**로 되찾는다.
        cand = [k for k in parent_meta if k and k in base]
        if cand:
            pm = parent_meta[max(cand, key=len)]
        else:
            att_orphan.append(base)
            return []
    plain = _plain(TABLE_RE.sub(' ', body))
    if len(plain) < 120 and '삭제' in plain:   # "삭제 <2009.4.29>" 뿐 — 벡터 노이즈
        att_deleted[0] += 1
        return []

    title = fval(fm, 'title') or os.path.basename(path)[:-3]
    m = ATT_NO_RE.search(body[:400])
    att_no = ('별표' + str(int(m.group(1))) + (f'의{int(m.group(2))}' if m.group(2) else '')
              if m else '별표')
    arts = list(dict.fromkeys(re.findall(r'제\d+조(?:의\d+)?', fval(fm, 'delegating_articles'))))

    recs = []
    for sub, seg in attachment_segments(body):
        if not seg.strip():
            continue
        head = f'「{pm["title"]}」 {att_no} {title}'
        if sub:
            head += f' [{sub}]'
        recs.append({
            'chunk_id': f'{pm["law_id"]}#{att_no}' + (f'_{sub.replace(" ", "")}' if sub else ''),
            'content': head + '\n\n' + seg,
            'metadata': {
                'law_id': pm['law_id'], 'law_mst': pm['mst'], 'law_title': pm['title'],
                'jurisdiction': pm['juris'], 'legal_hierarchy': pm['hier'],
                'document_type': 'attachment',
                'article': arts[0] if arts else '',      # 위임 조문(대표)
                'article_title': title, 'subunit': sub,
                'attachment_no': att_no,
                'delegating_articles': arts,             # 조↔별표 양방향의 별표 쪽
                # 파싱 품질 플래그(math_loss·corrected·char_diverge…). 검색·인용 단계에서
                # "이 청크는 파싱 손실 의심" 을 알 수 있어야 하므로 청크까지 끌고 온다.
                'parse_note': fval(fm, 'parse_note'),
                'enforce_date': pm['enforce'], 'promulgate_date': pm['promul'],
                'status': pm['status'],
                'associated_attachments': [], 'referenced_forms': [],
                'source': pm['source'],
            },
        })
    return recs


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
    if not chunks:
        # 파싱 실패는 예외가 아니라 '빈 결과'로 나타난다 — 조용히 지나가면 청크가 stale 해진다.
        empty_docs.append(os.path.basename(path))
    source = fval(fm, '출처')
    juris = JURIS.get(dept, dept)
    hier = HIER.get(gubun, gubun)
    # 별표 청크가 모법 메타를 상속하도록 stem 기준으로 보관
    # (parsed 별표의 frontmatter `parent_law` 값 = 모법 md 파일 stem)
    parent_meta[stem] = {'law_id': law_id, 'mst': mst, 'title': title, 'juris': juris,
                         'hier': hier, 'enforce': enforce, 'promul': promul,
                         'status': status, 'source': source}

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
empty_docs = []
att_deleted = [0]
att_orphan = []
parent_meta = {}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
allrecs = []
for folder, dt in ((LAWS, 'law'), (ADMIN, 'admin_rule')):
    if not os.path.isdir(folder):
        continue
    for fn in sorted(os.listdir(folder)):
        if fn.endswith('.md'):
            allrecs.extend(build(os.path.join(folder, fn), dt))

# 별표 청크는 모법 메타를 상속하므로 반드시 본문 처리 *뒤에* 돈다.
for mdp in sorted(glob.glob(os.path.join(PARSED, '*.md'))):
    allrecs.extend(build_attachment(mdp, parent_meta))

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
print(f'청크 {len(allrecs)}개 → {OUT} (삭제 조 {deleted[0]}개 · 삭제 별표 {att_deleted[0]}개 제외)')
print(f'  document_type: {dict(by_dt)}')
print(f'  jurisdiction:  {dict(by_j)}')
print(f'  legal_hierarchy: {dict(by_h)}')
print(f'  별표 연결 청크: {w_att} · 서식 참조 청크: {w_form} · 항/호 분할 청크: {split}')
print(f'  평균 content 길이: {avg}자 · 최대 {max(lens)}자 · MAXCHARS+400 초과(통짜) {over}개')
att = [r for r in allrecs if r['metadata']['document_type'] == 'attachment']
srcs = len({r['metadata']['attachment_no'] + r['metadata']['law_title'] for r in att})
print(f'  별표 청크: {len(att)}개 (원본 별표 {srcs}건 · 표 분할 '
      f"{sum(1 for r in att if r['metadata']['subunit'])}개)")
noted = Counter(r['metadata']['parse_note'] for r in att if r['metadata']['parse_note'])
if noted:
    print(f'  파싱 플래그 붙은 별표 청크: {dict(noted)}')

# ── 별표 원본 ↔ 파싱본 짝 검사 ──────────────────────────────────────────────
# 별표 파싱은 LibreOffice 가 필요해 CI 에서 못 돈다(로컬 수동). 그래서 개정으로 새 별표가
# 수집됐는데 파싱본이 없으면 그 별표는 *조용히* 청크에서 빠진다 — 없는 줄도 모른다.
# 여기서 짝을 맞춰 ⛔ 로 알린다(워크플로가 chunks.log 의 ⛔ 를 이슈에 얹는다).
ATT_SRC = os.path.join(os.path.dirname(PARSED) or '.', 'attachments')
src_stems = {os.path.splitext(f)[0] for f in os.listdir(ATT_SRC)
             if f.startswith('[별표]') and f.lower().endswith(('.hwp', '.hwpx'))} \
    if os.path.isdir(ATT_SRC) else set()
parsed_stems = {os.path.basename(p)[:-3] for p in glob.glob(os.path.join(PARSED, '*.md'))}
unparsed = sorted(src_stems - parsed_stems)
orphaned = sorted(parsed_stems - src_stems)
if unparsed:
    print(f'  ⛔ 파싱본 없는 별표 원본 {len(unparsed)}건 — 청크에서 누락됩니다. '
          f'도구 갖춘 로컬에서 _parse_attachments.py 실행 후 커밋 필요: {unparsed[:3]}')
if orphaned:
    print(f'  ⚠️ 원본 없는 파싱본 {len(orphaned)}건 — 별표가 폐지됐을 수 있습니다: {orphaned[:3]}')
if empty_docs:
    print(f'  ⛔ 조문 0개로 파싱된 문서 {len(empty_docs)}건 — 형식 변경 의심: {empty_docs[:5]}')
if att_orphan:
    print(f'  ⚠️ 모법 미매칭 별표 {len(att_orphan)}건: {att_orphan[:3]}')
