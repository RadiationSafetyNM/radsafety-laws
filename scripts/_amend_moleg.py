#!/usr/bin/env python3
"""법제처 개정 비교 API 클라이언트 — 신구법비교(oldAndNew) + 3단비교(thdCmp).

우리 자체 diff(_amend_selfdiff.py)가 *우리 미러 두 스냅샷*을 비교한다면, 이 모듈은
**법제처가 스스로 판정한 개정 범위**를 가져온다. 둘은 독립 관측이라 대조하면 서로를
검증한다(→ _amend_audit.py).

두 API 의 역할이 다르다:

  ① 신구법비교 target=oldAndNew  — "이 공포본에서 무엇이 바뀌었나"
     MST 하나만 주면 법제처가 직전 공포본을 알아서 짝지어 구/신 조문을 돌려준다.
     ⚠️ 개정된 조문만 담긴다. 부칙은 조문으로 세지 않는다(자체 diff 는 부칙 변경을
        따로 잡으므로, 대조 시 이 비대칭을 알고 있어야 한다).

  ② 3단비교 target=thdCmp — "이 조가 바뀌면 어디가 영향받나"
     법률↔시행령↔시행규칙 조문 대응표. 개정 이력이 아니라 **파급 분석**용이다.
     ⚠️ `knd` 가 필수 — 빠뜨리면 HTTP 200 에 빈 본문(0 bytes)이 와서 "권한 없음"과
        구별되지 않는다(2026-07-31 실측 함정). knd=1 인용조문 / knd=2 위임조문.

전제: 호출 PC 공인 IP 가 open.law.go.kr 에 등록돼야 한다(_freshness_audit.py 와 동일).
      OC = 가입 이메일 @앞(점 포함) — 기본값은 env MOLEG_OC.

사용:
  python3 _amend_moleg.py oldandnew --mst 288077
  python3 _amend_moleg.py thdcmp    --mst 288077 --knd 1
  python3 _amend_moleg.py oldandnew --mst 288077 --json --cache-dir out/moleg
"""
import os, sys, re, json, argparse, urllib.request, urllib.parse
import xml.etree.ElementTree as ET

BASE = 'https://www.law.go.kr/DRF/lawService.do'
DEFAULT_OC = os.environ.get('MOLEG_OC', 'benkorea.ai')


# ── 저수준 호출 ────────────────────────────────────────────────────────────
def fetch(oc, target, cache_dir=None, **params):
    """XML 원문 문자열. cache_dir 지정 시 파일 캐시(재현성·쿼터 절약)."""
    q = dict(OC=oc, target=target, type='XML', **params)
    key = '_'.join(f'{k}{v}' for k, v in sorted(q.items()) if k not in ('OC', 'type'))
    path = os.path.join(cache_dir, f'{key}.xml') if cache_dir else None
    if path and os.path.exists(path):
        return open(path, encoding='utf-8').read()

    raw = urllib.request.urlopen(BASE + '?' + urllib.parse.urlencode(q),
                                 timeout=60).read().decode('utf-8', 'ignore')
    if not raw.strip():
        raise SystemExit(f'✗ 빈 응답 (target={target}) — 필수 파라미터 누락 의심'
                         f' (thdCmp 는 knd 필수) 또는 IP 미등록')
    if raw.lstrip().startswith('<!DOCTYPE') or '<html' in raw[:200].lower():
        raise SystemExit(f'✗ HTML 회신 (target={target}) — 대개 IP 미등록·잘못된 target')
    if path:
        os.makedirs(cache_dir, exist_ok=True)
        open(path, 'w', encoding='utf-8').write(raw)
    return raw


def _txt(el, tag, default=''):
    v = el.findtext(tag)
    return (v or default).strip()


def norm_article(no, branch='00'):
    """('0121','00') → '제121조' · ('0121','02') → '제121조의2'."""
    try:
        base = f'제{int(no)}조'
    except (TypeError, ValueError):
        return ''
    b = int(branch or 0)
    return f'{base}의{b}' if b else base


ART_IN_TEXT = re.compile(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?')
# 회신 본문은 CDATA 안에 `<P>`·`<BR/>` 등 HTML 태그가 **문자열로** 들어온다.
# 이걸 안 벗기면 `<P>제48조의2(...)` 가 조 시작으로 안 잡혀 **가지조문이 앞 조에
# 통째로 흡수된다** — 실측(2026-07-31, 의료기기법 시행규칙): 제48조의2·3·4 가
# 제48조 그룹에 먹혀 자체 diff 와 대조 시 SELF_ONLY 3건 오탐이 났다.
HTML_TAG = re.compile(r'<[^>]{1,40}>')


def strip_tags(text):
    return HTML_TAG.sub('', text).strip()


def articles_in(text):
    """본문 첫머리의 조 표기에서 조번호 추출 (신구법비교 조문 텍스트용)."""
    m = ART_IN_TEXT.match(strip_tags(text))
    if not m:
        return None
    return f'제{int(m.group(1))}조' + (f'의{int(m.group(2))}' if m.group(2) else '')


# ── ① 신구법비교 ──────────────────────────────────────────────────────────
def oldandnew(mst, oc=DEFAULT_OC, cache_dir=None):
    """{'old':{...}, 'new':{...}, 'articles':{조번호:{'old':[..],'new':[..]}}}"""
    root = ET.fromstring(fetch(oc, 'oldAndNew', cache_dir, MST=str(mst)))

    def info(tag):
        e = root.find(tag)
        if e is None:
            return {}
        return {'mst': _txt(e, '법령일련번호'), 'law_id': _txt(e, '법령ID'),
                'name': _txt(e, '법령명'), 'promulgated': _txt(e, '공포일자'),
                'effective': _txt(e, '시행일자'), 'no': _txt(e, '공포번호'),
                'kind': _txt(e, '제개정구분명'), 'current': _txt(e, '현행여부')}

    def side(tag):
        """<조문> 조각들을 조 단위로 묶는다 — 조 표기로 시작하는 조각이 새 조의 시작."""
        out, cur = {}, None
        sec = root.find(tag)
        for j in (sec.findall('조문') if sec is not None else []):
            t = ''.join(j.itertext()).strip()
            if not t:
                continue
            head = articles_in(t)
            if head:
                cur = head
                out.setdefault(cur, [])
            if cur:
                out[cur].append(t)
        return out

    old_a, new_a = side('구조문목록'), side('신조문목록')
    arts = {}
    for no in sorted(set(old_a) | set(new_a), key=_sortkey):
        arts[no] = {'old': old_a.get(no, []), 'new': new_a.get(no, [])}
    return {'old': info('구조문_기본정보'), 'new': info('신조문_기본정보'),
            'articles': arts}


def _sortkey(no):
    m = re.match(r'제(\d+)조(?:의(\d+))?', no)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


# ── ② 3단비교 ─────────────────────────────────────────────────────────────
# knd 별로 root 태그·기준 조문 태그가 다르다(실측 2026-07-31).
THD = {1: ('인용조문삼단비교', '인용조문'), 2: ('위임조문삼단비교', '위임조문')}


def thdcmp(mst, knd=1, oc=DEFAULT_OC, cache_dir=None):
    """{'basis':{...}, 'map':{기준조:{'title':..,'linked':{층:[조번호..]}}}}

    기준 조문(법률조문/시행규칙조문 등)마다 하위 대응 목록(시행령조문목록 등)이 달린다.
    """
    root = ET.fromstring(fetch(oc, 'thdCmp', cache_dir, MST=str(mst), knd=str(knd)))
    sec_name = THD.get(knd, THD[1])[0]
    sec = root.find(sec_name)
    if sec is None:
        raise SystemExit(f'✗ <{sec_name}> 없음 — knd={knd} 응답 구조 변경 의심')

    base = root.find('기본정보')
    basis = {t: _txt(base, t) for t in
             ('법령ID', '시행령ID', '시행규칙ID', '법령명', '시행령명', '시행규칙명')
             } if base is not None else {}

    out = {}
    for item in sec:
        no = norm_article(_txt(item, '조번호'), _txt(item, '조가지번호'))
        if not no:
            continue
        linked = {}
        for lst in item:
            if not lst.tag.endswith('조문목록'):
                continue
            layer = lst.tag.replace('조문목록', '')      # 시행령 / 시행규칙 / 법률
            nos = []
            for sub in lst:
                t = strip_tags(''.join(sub.itertext()))
                # 대응 조문 텍스트는 '013200제132조(건강진단)…' 처럼 코드+본문이 붙어 온다.
                m = ART_IN_TEXT.search(t[:40])
                if m:
                    nos.append(f'제{int(m.group(1))}조'
                               + (f'의{int(m.group(2))}' if m.group(2) else ''))
            if nos:
                linked[layer] = sorted(set(nos), key=_sortkey)
        out[no] = {'title': _txt(item, '조제목'), 'linked': linked}
    return {'basis': basis, 'kind': THD.get(knd, THD[1])[1], 'map': out}


# ── CLI ───────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='법제처 신구법비교·3단비교 클라이언트')
    ap.add_argument('mode', choices=['oldandnew', 'thdcmp'])
    ap.add_argument('--mst', required=True)
    ap.add_argument('--knd', type=int, default=1, choices=[1, 2],
                    help='3단비교 종류 — 1 인용조문 / 2 위임조문 (thdcmp 필수)')
    ap.add_argument('--oc', default=DEFAULT_OC)
    ap.add_argument('--cache-dir', default=None)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    if a.mode == 'oldandnew':
        r = oldandnew(a.mst, a.oc, a.cache_dir)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=2)); return
        o, n = r['old'], r['new']
        print(f"📜 {n.get('name') or o.get('name')} — {n.get('kind','')}")
        print(f"   구 MST {o.get('mst')} (공포 {o.get('promulgated')})"
              f"  →  신 MST {n.get('mst')} (공포 {n.get('promulgated')})")
        print(f"   법제처가 든 개정 조문: {len(r['articles'])}개")
        for no, v in r['articles'].items():
            head = (v['new'] or v['old'] or [''])[0][:56].replace('\n', ' ')
            print(f'   · {no}  {head}')
    else:
        r = thdcmp(a.mst, a.knd, a.oc, a.cache_dir)
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=2)); return
        print(f"🔗 3단비교({r['kind']}) — {r['basis'].get('법령명','')}")
        print(f"   기준 조문 {len(r['map'])}개 · 대응 있는 조문 "
              f"{sum(1 for v in r['map'].values() if v['linked'])}개")
        for no, v in list(r['map'].items())[:10]:
            if v['linked']:
                s = ' · '.join(f"{k} {','.join(x[:3])}" for k, x in v['linked'].items())
                print(f"   · {no}{v['title'] and f' {v['title']}'} → {s}")


if __name__ == '__main__':
    main()
