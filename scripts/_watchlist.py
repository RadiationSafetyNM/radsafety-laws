#!/usr/bin/env python3
"""워치리스트 단일 권위 로더 — watchlist.toml 을 읽어 fetch·audit 양쪽에 공급.

watchlist.toml(레포 루트)이 "어떤 법을 추적하는가"의 유일한 정의다. 이 모듈이
그걸 파싱해, ① bash fetch 스크립트(update_laws.sh)엔 CLI 로 'name<TAB>type' 행을,
② python 감사 스크립트엔 import 로 멤버 리스트를 준다. tomllib 은 py3.11+ 표준라이브러리.

CLI:
  python3 _watchlist.py --fetch-list   # name<TAB>type  (core 만 — update_laws.sh 소비)
  python3 _watchlist.py --fetch-list --all  # core+peripheral 전부
  python3 _watchlist.py --ids          # law_id  tier  name  type  family  (확인용)
  python3 _watchlist.py --admrule-list # agency<TAB>kind<TAB>name  (_collect_admrul.py 소비)
"""
import sys, os, tomllib

ROOT = os.path.join(os.path.dirname(__file__), '..')
PATH = os.path.join(ROOT, 'watchlist.toml')


def members():
    """[{family, name, type, law_id, tier, watch_articles}] 평탄 리스트.

    tier 생략 시 'core'. peripheral 은 방사선 조항만 감시(whole-law 추적·전문 fetch 제외)."""
    with open(PATH, 'rb') as f:
        cfg = tomllib.load(f)
    out = []
    for fam in cfg.get('family', []):
        tier = fam.get('tier', 'core')
        watch = fam.get('watch_articles', [])
        for m in fam.get('members', []):
            out.append(dict(family=fam.get('act', ''), name=m['name'],
                            type=m['type'], law_id=m.get('law_id', ''),
                            tier=tier, watch_articles=watch))
    return out


def admrules():
    """[{name, kind, agency, parent}] — 명시 고시·예규·훈령 목록.

    고시 선별은 키워드 sweep 이 동음이의(방호=경비, 방재=재난) 노이즈로 불가 → 명시 큐레이션.
    fetch 경로(admrule-kr) = <agency>/<kind>/<name>/본문.md"""
    with open(PATH, 'rb') as f:
        cfg = tomllib.load(f)
    return [dict(name=a['name'], kind=a['kind'], agency=a['agency'],
                 parent=a.get('parent', '')) for a in cfg.get('admrule', [])]


def main(argv):
    ms = members()
    if '--fetch-list' in argv:
        # 기본은 core 만 전문 수집. --all 이면 peripheral 도(보통 불필요).
        want_all = '--all' in argv
        for m in ms:
            if not want_all and m['tier'] != 'core':
                continue
            print(f"{m['name']}\t{m['type']}")
    elif '--ids' in argv:
        for m in ms:
            print(f"{m['law_id']}\t{m['tier']}\t{m['name']}\t{m['type']}\t{m['family']}")
    elif '--admrule-list' in argv:
        for a in admrules():
            print(f"{a['agency']}\t{a['kind']}\t{a['name']}")
    else:
        print(__doc__)
        core = sum(1 for m in ms if m['tier'] == 'core')
        peri = len(ms) - core
        print(f"멤버 {len(ms)}건 (core {core} / peripheral {peri}) / 법률패밀리 "
              f"{len({m['family'] for m in ms})}그루", file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
