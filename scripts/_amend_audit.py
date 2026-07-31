#!/usr/bin/env python3
"""개정 감지 교차검증 — 자체 diff ↔ 법제처 신구법비교 대조 (+3단비교 파급).

두 개의 **독립 관측**을 맞대어 서로를 검증한다.

  A. 자체 diff (_amend_selfdiff.py)  = 우리 미러 두 스냅샷의 조문 단위 차이
  B. 법제처 신구법비교 (_amend_moleg.py) = 발행처가 스스로 든 개정 조문

왜 대조하는가 — 각각 다른 방식으로 틀리기 때문이다:

  · A 만 잡음(SELF_ONLY)  → 대개 **미러 재가공 노이즈**(포맷·오타). 법은 안 바뀌었다.
                            부칙도 여기 뜬다 — 법제처는 부칙을 조문으로 세지 않는다(구조적 비대칭).
  · B 만 잡음(API_ONLY)   → ⚠️ **우리 데이터가 뒤처졌다**. 미러가 개정을 아직 안 실었거나
                            우리 파싱이 그 조를 놓쳤다. 신선도 사고의 조기 신호다.
  · 둘 다 잡음(AGREE)     → 신뢰. 알림에 그대로 실어도 되는 개정.

MST 는 "새 판이 나왔다"는 플래그일 뿐이라 내용을 말해주지 않는다. 이 스크립트가 그 다음
질문("무엇이·어디가 바뀌었나")에 답하고, 두 출처가 일치하는지까지 본다.

전제: 신구법비교 호출은 IP 등록 필요(_freshness_audit.py 와 동일). 자체 diff 만 볼 거면
      --no-api 로 오프라인 실행 가능.

사용:
  # 특정 리비전 쌍
  python3 _amend_audit.py data/laws/원자력안전법_시행규칙.md --git 2b6580a 4abdd2a
  # MST 가 바뀐 마지막 커밋 쌍을 자동으로 찾아서
  python3 _amend_audit.py data/laws/원자력안전법_시행규칙.md --auto
  # 워치리스트 전체를 훑어 최근 개정분 감사
  python3 _amend_audit.py --all
  # 개정 조의 상·하위 법령 파급까지 (3단비교)
  python3 _amend_audit.py <path> --auto --impact
"""
import os, sys, re, json, argparse, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _amend_selfdiff import diff_articles, _sortkey            # noqa: E402
import _amend_moleg as moleg                                    # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def sh(*args):
    p = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    return p.stdout if p.returncode == 0 else ''


def frontmatter(text):
    m = re.match(r'---\n(.*?)\n---', text, re.S)
    d = {}
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r'([^:\s][^:]*):\s*(.*)', line)
            if mm:
                d[mm.group(1).strip()] = mm.group(2).strip().strip('\'"')
    return d


def mst_of(rev, path):
    return frontmatter(sh('git', 'show', f'{rev}:{path}')).get('법령MST', '')


def find_mst_change(path, limit=40):
    """그 파일의 법령MST 가 바뀐 가장 최근 커밋 쌍 (old_rev, new_rev) 반환.

    파일이 바뀐 커밋을 최신순으로 훑으며 MST 가 실제로 달라진 지점을 찾는다 —
    본문만 바뀐 재가공 커밋을 건너뛰기 위함(이게 이 프로젝트의 핵심 구분).
    """
    revs = sh('git', 'log', f'-{limit}', '--format=%H', '--', path).split()
    for i in range(len(revs) - 1):
        new, old = revs[i], revs[i + 1]
        if mst_of(new, path) != mst_of(old, path):
            return old, new
    return None, None


def audit(path, old_rev, new_rev, use_api=True, oc=None, cache_dir=None, impact=False):
    old_t, new_t = sh('git', 'show', f'{old_rev}:{path}'), sh('git', 'show', f'{new_rev}:{path}')
    if not new_t:
        return {'path': path, 'error': f'{new_rev}:{path} 읽기 실패'}

    self_res = diff_articles(old_t, new_t)
    self_set = set(self_res['changed']) | set(self_res['added']) | set(self_res['removed'])

    fm_old, fm_new = frontmatter(old_t), frontmatter(new_t)
    out = {
        'path': path,
        'mst': {'old': fm_old.get('법령MST', ''), 'new': fm_new.get('법령MST', '')},
        'promulgated': {'old': fm_old.get('공포일자', ''), 'new': fm_new.get('공포일자', '')},
        'self': {'changed': self_res['changed'], 'added': self_res['added'],
                 'removed': self_res['removed'], 'addenda': self_res['addenda_changed']},
        'api': None, 'verdict': {}, 'impact': None,
    }
    out['mst_changed'] = out['mst']['old'] != out['mst']['new']

    if not use_api:
        return out
    if not out['mst']['new']:
        out['api_error'] = 'frontmatter 에 법령MST 없음 — API 조회 불가'
        return out

    try:
        api = moleg.oldandnew(out['mst']['new'], oc or moleg.DEFAULT_OC, cache_dir)
    except SystemExit as e:
        out['api_error'] = str(e)
        return out

    api_set = set(api['articles'])
    out['api'] = {'articles': sorted(api_set, key=_sortkey),
                  'old_mst': api['old'].get('mst'), 'new_mst': api['new'].get('mst'),
                  'kind': api['new'].get('kind')}

    # 법제처가 짝지은 직전본이 우리 직전본과 같은가 — 다르면 중간 개정을 건너뛴 것.
    out['pair_match'] = (api['old'].get('mst') == out['mst']['old'])

    out['verdict'] = {
        'agree': sorted(self_set & api_set, key=_sortkey),
        'self_only': sorted(self_set - api_set, key=_sortkey),
        'api_only': sorted(api_set - self_set, key=_sortkey),
    }

    if impact and api_set:
        try:
            thd = moleg.thdcmp(out['mst']['new'], 1, oc or moleg.DEFAULT_OC, cache_dir)
            out['impact'] = {no: thd['map'].get(no, {}).get('linked', {})
                             for no in sorted(api_set, key=_sortkey)}
        except SystemExit as e:
            out['impact_error'] = str(e)
    return out


def render(r):
    name = os.path.basename(r['path'])
    if r.get('error'):
        print(f'✗ {name} — {r["error"]}'); return
    print(f'\n📄 {name}')
    print(f'   MST {r["mst"]["old"] or "-"} → {r["mst"]["new"] or "-"}'
          f'   공포 {r["promulgated"]["old"] or "-"} → {r["promulgated"]["new"] or "-"}'
          f'   {"[개정]" if r.get("mst_changed") else "[MST 무변경=재가공]"}')

    s = r['self']
    parts = []
    if s['changed']: parts.append('변경 ' + ','.join(s['changed']))
    if s['added']:   parts.append('신설 ' + ','.join(s['added']))
    if s['removed']: parts.append('삭제 ' + ','.join(s['removed']))
    if s['addenda']: parts.append('부칙 변경')
    print(f'   A 자체 diff : {" · ".join(parts) if parts else "변경 없음"}')

    if r.get('api_error'):
        print(f'   B 법제처   : ✗ {r["api_error"]}'); return
    if not r.get('api'):
        print('   B 법제처   : (미조회 — --no-api)'); return

    print(f'   B 법제처   : {" , ".join(r["api"]["articles"]) or "없음"}'
          f'   ({r["api"]["kind"] or ""}, 직전본 {r["api"]["old_mst"]})')
    if r.get('pair_match') is False:
        print(f'   ⚠️ 짝 불일치 — 법제처 직전본({r["api"]["old_mst"]}) ≠ 우리 직전본'
              f'({r["mst"]["old"]}). 중간 개정을 건너뛰었을 수 있다.')

    v = r['verdict']
    if v.get('agree'):
        print(f'   ✅ AGREE     {", ".join(v["agree"])}  — 두 출처 일치, 알림 신뢰 가능')
    if v.get('self_only'):
        print(f'   ◻ SELF_ONLY {", ".join(v["self_only"])}  — 미러 재가공/부칙 추정, 알림 강도 낮춤')
    if s['addenda'] and not v.get('self_only'):
        print('   ◻ SELF_ONLY 부칙  — 법제처는 부칙을 조문으로 세지 않음(구조적 비대칭)')
    if v.get('api_only'):
        print(f'   ⚠️ API_ONLY  {", ".join(v["api_only"])}  — 우리 데이터가 뒤처졌거나 파싱 누락!')

    if r.get('impact'):
        print('   🔗 파급(3단비교 인용조문)')
        for no, linked in r['impact'].items():
            if linked:
                s2 = ' · '.join(f'{k} {",".join(x)}' for k, x in linked.items())
                print(f'      {no} → {s2}')


def main():
    ap = argparse.ArgumentParser(description='자체 diff ↔ 법제처 신구법비교 교차검증')
    ap.add_argument('path', nargs='?', help='data/laws/<법령>.md')
    ap.add_argument('--git', nargs=2, metavar=('OLD', 'NEW'), help='비교할 두 리비전')
    ap.add_argument('--auto', action='store_true', help='MST 가 바뀐 최근 커밋 쌍 자동 탐색')
    ap.add_argument('--all', action='store_true', help='data/laws 전체 감사(--auto 강제)')
    ap.add_argument('--impact', action='store_true', help='3단비교로 상·하위 파급까지')
    ap.add_argument('--no-api', action='store_true', help='자체 diff 만(오프라인·IP 불필요)')
    ap.add_argument('--oc', default=None)
    ap.add_argument('--cache-dir', default='out/moleg')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    if not a.all and not a.path:
        ap.error('path 를 주거나 --all 을 쓰십시오')

    targets = []
    if a.all:
        d = os.path.join(ROOT, 'data/laws')
        targets = [f'data/laws/{f}' for f in sorted(os.listdir(d)) if f.endswith('.md')]
    else:
        targets = [a.path]

    results = []
    for path in targets:
        if a.git and not a.all:
            old, new = a.git
        else:
            old, new = find_mst_change(path)
            if not old:
                if not a.json:
                    print(f'\n📄 {os.path.basename(path)}\n   (이력 내 MST 변경 없음 — 감사 생략)')
                continue
        results.append(audit(path, old, new, not a.no_api, a.oc, a.cache_dir, a.impact))

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2)); return
    for r in results:
        render(r)

    if results and not a.json:
        agree = sum(len(r.get('verdict', {}).get('agree', [])) for r in results)
        so = sum(len(r.get('verdict', {}).get('self_only', [])) for r in results)
        ao = sum(len(r.get('verdict', {}).get('api_only', [])) for r in results)
        print(f'\n{"─"*72}\n합계 {len(results)}건 감사 — AGREE {agree} · SELF_ONLY {so} · API_ONLY {ao}')
        if ao:
            print('⚠️ API_ONLY 가 있으면 우리 미러가 개정을 놓친 것 — 신선도 조사 필요')


if __name__ == '__main__':
    main()
