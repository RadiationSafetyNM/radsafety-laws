#!/usr/bin/env python3
"""공포 전 개정 수집 — 국민참여입법센터(법제처) 입법예고·행정예고 + 입법현황 (2026-11-27).

법제처 법령 API 는 공포 뒤(공포~시행)만 준다. 고시와 일부 하위법령은 공포에서 시행까지 0~3일이라
**예고 단계가 유일한 사전 정보**다. 여기서 모은 것은 **확정이 아니다** — 답변 근거로 쓰지 않고
「개정 예정」 화면에 "공포 전"으로만 보인다(amendment-tracker.yaml 과 같은 원칙).

원천 (등록 IP 필요 — AWS radsafety-sydney · kimbi. OC = 법제처 계정 아이디):
  /rest/ogLmPp     입법예고(법률·시행령·시행규칙)      /rest/ptcpAdmPp  행정예고(고시·훈령 등)
  /rest/govLmSts   입법현황(입법예고→법제처심사→차관·국무회의→공포) — 입법예고와는 mappingLbicId 로 이어진다
  목록은 최신순. 공식 안내서 없이 실제 응답으로 확인한 주소·항목이다(2026-11-27) — 바뀌면 이 파일을 고친다.
  /rest/<종류>/<번호>/0/<유형>.xml  상세 — 공고문 전문(개정이유 및 주요내용)·담당 부서·첨부(법령안·조문별 이유서)
고르는 기준: ① 제목에 방사선 분야 낱말, 또는 ② 감시 부처(원안위·복지부·식약처·질병청) 예고의 **공고문 본문**에 그 낱말.
  ②가 필요한 이유: 「의료법 시행규칙 일부개정령안」처럼 제목만으로는 방사선 관련인지 알 수 없다.
  본문을 본 뒤 해당 없던 예고는 data/prenotice-seen.json 에 번호만 적어 다시 받지 않는다.
주제(topic)는 제목 낱말 규칙으로 임시 분류 — 사람이 화면에서 확인한다.
산출 = data/prenotice.json (내용이 바뀔 때만 다시 쓴다). 사용: python3 scripts/_collect_prenotice.py [--days 400] [--dry-run]
"""
import datetime, json, os, re, sys, time
import xml.etree.ElementTree as ET
import requests

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
OUT = f'{ROOT}/data/prenotice.json'
BASE = 'https://opinion.lawmaking.go.kr/rest'
OC = os.environ.get('LAWMAKING_OC', 'kimbi.kirams')
KW = re.compile(r'방사선|방사성|방사능|원자력|동위원소|선량|핵의학|진단용|특수의료장비|피폭|엑스선|핵연료|원자로')
# 의료·RI 이용과 거리가 먼 주제(원자로·핵연료·기관 운영 등). upcoming 화면에서 접어 둔다.
OTHER = re.compile(r'원자로|핵연료|발전용|부담금|방재|비상대책|해체|핵물질|직제|운송선박|진흥|소형모듈|사용후|규제지침|회의 운영|정보공유|환경영향평가|통제|손해배상|생활주변|연구개발사업|재난관리|결핵')
SEEN = f'{ROOT}/data/prenotice-seen.json'
AGENCIES = ('원자력안전위원회', '보건복지부', '식품의약품안전처', '질병관리청')
# 본문 판정은 제목보다 좁게 — '원자력'·'선량' 같은 낱말은 남의 분야 공고문에도 스친다
KW_BODY = re.compile(r'방사선|방사성|방사능|동위원소|핵의학|특수의료장비|피폭|엑스선')
SUM_MAX = 1500
# 본문으로 보는 '의료·RI 이용 밖' — 원전·핵연료 얘기이고 RI·의료·종사자 얘기가 없을 때
OTHER_BODY = re.compile(r'발전용\s*원자로|원자로시설|원자력시설|핵연료주기|물리적\s*방호|원전|원자로조종')
RI_BODY = re.compile(r'방사성동위원소|방사선작업종사자|의료|진단용|방사선발생장치|수정체|선량한도|판독')
STAGES = ['입법예고', '법제처심사', '법제처심사완료', '차관회의', '국무회의', '국회제출', '공포대기', '공포']


def iso(s):
    m = re.match(r'(\d{4})\.\s*(\d+)\.\s*(\d+)', s or '')
    return f'{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}' if m else ''


def pages(kind, date_field, cutoff, size=100, limit=60):
    """최신순 목록을 cutoff 날짜까지 받는다."""
    out = []
    for p in range(1, limit + 1):
        r = requests.get(f'{BASE}/{kind}.xml', params={'OC': OC, 'pageIndex': p, 'pageSize': size}, timeout=60,
                         allow_redirects=False)
        if r.status_code != 200:
            raise RuntimeError(f'{kind} {r.status_code} — 등록 IP·OC 확인')
        lst = ET.fromstring(r.content).find('list')
        items = [{c.tag: (c.text or '').strip() for c in it} for it in (lst if lst is not None else [])]
        if not items:
            break
        out += items
        if min((iso(x.get(date_field)) for x in items if iso(x.get(date_field))), default='9') < cutoff:
            break
        time.sleep(0.2)
    return [x for x in out if iso(x.get(date_field)) >= cutoff]


def detail(kind, seq, typ):
    """상세 → {'dept', 'summary'(개정이유 및 주요내용), 'text'(공고문 전문), 'files'}. 실패하면 None."""
    import html
    for t in dict.fromkeys([typ or 'TYPE5', 'TYPE5', 'TYPE6']):
        r = requests.get(f'{BASE}/{kind}/{seq}/0/{t}.xml', params={'OC': OC}, timeout=60, allow_redirects=False)
        if r.status_code == 200 and b'<info>' in r.content:
            break
    else:
        return None
    info = next(iter(ET.fromstring(r.content).find('info')))
    get = lambda k: (info.findtext(k) or '').strip()
    text = html.unescape(re.sub(r'<[^>]+>', ' ', html.unescape(get('lmPpCts') or get('admPpCts'))))
    text = re.sub(r'[ \t\xa0]+', ' ', text)
    # 공고문 = 머리말 → "1. 개정이유 및 주요내용"(또는 "1. 개정이유 / 2. 주요내용") → "N. 의견제출" → 문의처.
    # 요지는 첫 "1. …이유"부터 "N. 의견제출" 앞까지. 형식이 달라 못 찾으면 의견제출 앞까지라도 자른다.
    m = re.search(r'1\.\s*(?:제정|개정|폐지|제ㆍ개정|제·개정)?\s*이유(.*?)(?=\d\.\s*의견\s*제출|\Z)', text, re.S)
    body = m.group(1) if m else re.split(r'\d\.\s*의견\s*제출', text)[0]
    body = re.sub(r'^\s*(?:및|와)?\s*주요\s*내용\s*', '', body)
    summary = re.sub(r'\s+', ' ', body).strip()
    names = [e.text.strip() for e in info.iter('FileName') if e.text]
    urls = [e.text.strip() for e in info.iter('FileDownUrl') if e.text]
    time.sleep(0.2)
    return {'dept': get('asndDptNm'), 'summary': summary[:SUM_MAX] + ('…' if len(summary) > SUM_MAX else ''),
            'text': text, 'files': [{'name': n, 'url': u} for n, u in zip(names, urls)]}


def clean(t):
    t = re.sub(r'^\[(진행|종료)\]\s*', '', t)
    return re.sub(r'\s*(입법예고|행정예고)\s*$', '', t).strip()


def main():
    days = int(next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == '--days'), 400))
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date()
    cutoff = (today - datetime.timedelta(days=days)).isoformat()
    lm = pages('ogLmPp', 'pntcDt', cutoff)
    adm = pages('ptcpAdmPp', 'pntcDt', cutoff)
    sts = {x['lbicId']: x for x in pages('govLmSts', 'lbPrcStsDt', cutoff)}
    old = json.load(open(OUT, encoding='utf-8'))['items'] if os.path.exists(OUT) else []
    kept = {o['id']: o for o in old}
    seen = set(json.load(open(SEEN, encoding='utf-8'))) if os.path.exists(SEEN) else set()
    items, fetched = [], 0
    for kind, rows, name, seq, path, api in (('입법예고', lm, 'lsNm', 'ogLmPpSeq', 'ogLmPp', 'ogLmPp'),
                                             ('행정예고', adm, 'admRulNm', 'ogAdmPpSeq', 'admpp', 'ptcpAdmPp')):
        for x in rows:
            title, iid = clean(x[name]), f"{path}-{x[seq]}"
            by_title = bool(KW.search(title))
            if not by_title and (iid in seen or not any(a in x.get('asndOfiNm', '') for a in AGENCIES)):
                continue
            prev = kept.get(iid)
            det = {k: prev[k] for k in ('dept', 'summary', 'files', 'matched')} if prev and prev.get('summary') else None
            if det is None:
                d = detail(api, x[seq], x.get('announceType'))
                fetched += 1
                if d is None and not by_title:
                    continue                     # 본문을 못 받았으면 다음 날 다시 본다
                hits = sorted(set(KW_BODY.findall(d['text']))) if d else []
                if not by_title and not hits:
                    seen.add(iid)
                    continue
                det = {'dept': d['dept'] if d else '', 'summary': d['summary'] if d else '', 'files': d['files'] if d else [],
                       'matched': 'title' if by_title else 'body:' + ','.join(hits)}
            st = sts.get(x.get('mappingLbicId', ''))            # 입법현황과 이어진 경우만 단계를 적는다
            start, end = iso(x.get('stYd')), iso(x.get('edYd'))
            items.append({
                'id': iid, 'kind': kind, 'title': title, 'agency': x.get('asndOfiNm', ''),
                'class': x.get('lsClsNm', ''), 'notice_no': x.get('pntcNo', ''), 'notice_date': iso(x.get('pntcDt')),
                'start': start, 'end': end, 'open': bool(start and end and start <= today.isoformat() <= end),
                'stage': st['lbPrcStsNm'] if st else '', 'stage_date': iso(st['lbPrcStsDt']) if st else '',
                'topic': 'other' if OTHER.search(title) or OTHER_BODY.search(title) or (
                    OTHER_BODY.search(det['summary']) and not RI_BODY.search(title + det['summary'])) else 'ri-medical',
                'link': f'https://opinion.lawmaking.go.kr/gcom/{path}/{x[seq]}',
                'current_admrul': x.get('mappingAdmRulSeq', ''), **det})
    items.sort(key=lambda i: (i['notice_date'], i['id']), reverse=True)
    strip = lambda xs: [{k: v for k, v in i.items()} for i in xs]
    print(f'입법예고 {len(lm)} · 행정예고 {len(adm)} · 입법현황 {len(sts)} (최근 {days}일) → 방사선 분야 {len(items)}건'
          f" (예고 기간 중 {sum(i['open'] for i in items)} · 의료·RI {sum(i['topic'] == 'ri-medical' for i in items)}"
          f" · 본문으로 고른 것 {sum(i['matched'] != 'title' for i in items)}) · 상세 조회 {fetched}회")
    if '--dry-run' not in sys.argv:
        json.dump(sorted(seen), open(SEEN, 'w', encoding='utf-8'))
    new = [i for i in items if i['id'] not in {o['id'] for o in old}]
    for i in new[:10]:
        print(f"  새 항목 {i['notice_date']} {i['agency']} {i['title'][:50]}")
    if '--dry-run' in sys.argv or strip(items) == strip(old):
        print('변경 없음' if strip(items) == strip(old) else '(dry-run)')
        return 0
    json.dump({'source': '국민참여입법센터(opinion.lawmaking.go.kr) 입법예고·행정예고 — 공포 전, 확정 아님',
               'days': days, 'items': items}, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'→ {os.path.relpath(OUT, ROOT)} ({len(items)}건, 새 항목 {len(new)})')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:          # 수집 실패가 일일 수집 전체를 멈추지 않게 — 호출 쪽에서 경고만 남긴다
        print(f'⛔ {e}')
        sys.exit(2)
