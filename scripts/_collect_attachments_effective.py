#!/usr/bin/env python3
"""법령 별표의 시행일 기준 판본 — 스냅샷의 별표 목록 → 판본 사슬 → 원본 수집·파싱 → manifest.

결정 4(2026-11-20)를 별표까지 넓힌다. 조문과 같은 원리다:
  data/laws-effective/<스냅샷>.별표.json 에는 그 날짜에 시행되는 별표마다 본문 지문(sha)이 있다.
  같은 별표의 지문이 연속한 스냅샷 사이에 바뀌면 그 날짜에 새 판본이 시작된다.
  → 별표 판본 = (법령, 별표번호·가지번호, effective_from, effective_to)

왜 미러 별표(data/attachments)를 그대로 못 쓰나:
  미러 별표는 미러 법령 파일의 frontmatter 링크로 받는다. 미러가 늦으면 옛 판본이,
  미러가 미시행 공포본을 들고 있으면 미래 판본이 현행처럼 들어온다
  (2026-11-20 실측: 원자력안전법 시행령 별표 7·9·10 이 11-17 개정 전 판본이었다).

본문은 어디서 오나 — 같은 판본이면 **기존 파싱본을 재사용**한다:
  data/attachments-parsed/ 의 md 가 그 판본임이 **근거 대장**(parsed-provenance.json,
  {파일 기본이름: 판본 지문})에 적혀 있을 때만 그대로 쓴다. 거기엔 수동 hwpx 변환·교정
  오버레이(CLAUDE.md §예외 1·2)가 이미 들어 있어서다. 대장에 없거나 지문이 다르면
  스냅샷의 원본(HWP/HWPX)+PDF 링크로 직접 받아 data/attachments-effective/src/<시행일>/ 에
  두고 _parse_attachments.py 로 파싱한다.
  ⚠️ "미러 법령 파일의 MST 와 지문이 같으면 재사용"으로 판정하면 안 된다(2026-11-23 사고):
  미러 원본 수집(_collect_attachments.py)은 같은 제목 파일이 있으면 다시 받지 않으므로,
  미러 법령 파일이 새 MST 로 올라가도 attachments-parsed 는 옛 판본에 머문다. 그 상태에서
  MST 지문만 맞춰 보면 옛 파싱본이 새 판본으로 둔갑한다(원자력안전법 시행령 별표 7·9·
  10의2·10의3 이 11-17 개정본에서 2025년판으로 되돌아갔다). 대장은 원본을 새로 받아
  파싱본을 교체했을 때만 손으로 고친다.

범위: 별표만(서식 제외). core 법령은 전량, peripheral 법령은 제목이 방사선 키워드인 것만
(_collect_attachments.py 와 같은 규칙). 고시 별표는 아직 판본을 가르지 않는다.

산출: data/attachments-effective/manifest.json — _build_chunks.py 가 소비.
사용: python3 scripts/_collect_attachments_effective.py
종료 코드: 0 정상 / 2 다운로드 실패 / 3 파싱 실패.
"""
import os, re, sys, json, glob, subprocess, urllib.request
from urllib.parse import unquote
from collections import defaultdict
from _watchlist import members

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EFF = os.path.join(ROOT, 'data', 'laws-effective')
OUT = os.path.join(ROOT, 'data', 'attachments-effective')
PARSED = os.path.join(ROOT, 'data', 'attachments-parsed')
PROVENANCE = os.path.join(OUT, 'parsed-provenance.json')

# _collect_attachments.py 와 같은 규칙(그 파일은 import 시 수집을 돌리므로 복사해 둔다)
RAD = re.compile(r'방사선|방사성|동위원소|선량|피폭|핵종|방사능|진료환자|격리|퇴원|'
                 r'차폐|방어시설|치료용|진단용|핵의학|방호|누설|RI')
TIER = {f"{m['name']}_{m['type']}": m['tier'] for m in members()}


def sanitize(s):
    return re.sub(r'[/\\:*?"<>|]', '_', s)[:120]


def base_name(title, stem):
    return f'[별표] {sanitize(title)}({stem})'


def key_str(no, branch):
    return f'별표{int(no)}' + (f'의{int(branch)}' if branch and int(branch) else '')


def snapshots():
    by = defaultdict(list)
    for p in glob.glob(os.path.join(EFF, '*@*.별표.json')):
        m = re.match(r'(.+)@(\d{4}-\d{2}-\d{2})_(\d+)\.별표\.json$', os.path.basename(p))
        by[m.group(1)].append((m.group(2), m.group(3), json.load(open(p, encoding='utf-8'))))
    return {k: sorted(v) for k, v in by.items()}


def chains(stem, snaps):
    """스냅샷 연쇄 → 별표 판본 목록. 조문 판본(_build_chunks.versioned)과 같은 규칙."""
    core = TIER.get(stem, 'peripheral') == 'core'
    opened, out = {}, []
    for date, mst, units in snaps:
        seen = set()
        for u in units:
            if not core and not RAD.search(u['title']):
                continue
            k = (u['no'], u['branch'])
            seen.add(k)
            o = opened.get(k)
            if o is not None and o['sha'] == u['sha']:
                continue
            if o is not None:
                o['effective_to'] = date
            v = {'stem': stem, 'no': u['no'], 'branch': u['branch'], 'att_no': key_str(u['no'], u['branch']),
                 'title': u['title'], 'effective_from': date, 'effective_to': None, 'sha': u['sha'],
                 'mst': mst, 'file_link': u['file_link'], 'pdf_link': u['pdf_link']}
            out.append(v)
            opened[k] = v
        for k in [k for k in opened if k not in seen]:
            opened.pop(k)['effective_to'] = date
    return out


def provenance():
    """attachments-parsed 의 md 가 어느 판본인지 — {파일 기본이름: 지문}. 없으면 재사용 0."""
    return json.load(open(PROVENANCE, encoding='utf-8')) if os.path.exists(PROVENANCE) else {}


def pick_ext(resp, data):
    cd = unquote(resp.headers.get('Content-Disposition', ''))
    m = re.search(r'\.([A-Za-z0-9]+)"?\s*$', cd)
    if m and m.group(1).lower() in ('pdf', 'hwp', 'hwpx'):
        return m.group(1).lower()
    if data[:4] == b'%PDF':
        return 'pdf'
    if data[:4] == b'PK\x03\x04':
        return 'hwpx'
    if data[:4] == b'\xd0\xcf\x11\xe0':
        return 'hwp'
    return None


def download(url, dst_base, want_pdf):
    for e in (('pdf',) if want_pdf else ('hwpx', 'hwp')):
        if os.path.exists(f'{dst_base}.{e}'):
            return
    resp = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'radsafety-laws'}), timeout=60)
    data = resp.read()
    ext = pick_ext(resp, data)
    if ext is None or (want_pdf and ext != 'pdf') or (not want_pdf and ext == 'pdf'):
        raise RuntimeError(f'형식 확인 실패: {os.path.basename(dst_base)} ({ext})')
    with open(f'{dst_base}.{ext}', 'wb') as f:
        f.write(data)


def main():
    os.makedirs(OUT, exist_ok=True)
    prov = provenance()
    manifest, need_parse = [], set()
    try:
        for stem, snaps in sorted(snapshots().items()):
            vers = chains(stem, snaps)
            if not vers:
                continue
            for v in vers:
                base = base_name(v['title'], stem)
                reuse = os.path.join(PARSED, base + '.md')
                if os.path.exists(reuse) and prov.get(base) == v['sha']:
                    v['md'], v['source'] = os.path.relpath(reuse, ROOT), 'mirror'
                else:
                    src = os.path.join(OUT, 'src', v['effective_from'])
                    os.makedirs(src, exist_ok=True)
                    if v['file_link']:
                        download(v['file_link'], os.path.join(src, base), False)
                    if v['pdf_link']:
                        download(v['pdf_link'], os.path.join(src, base), True)
                    md = os.path.join(OUT, 'parsed', v['effective_from'], base + '.md')
                    v['md'], v['source'] = os.path.relpath(md, ROOT), 'direct'
                    if not os.path.exists(md):
                        need_parse.add(v['effective_from'])
                manifest.append(v)
    except Exception as ex:
        print(f'⛔ 수집 실패: {ex}', file=sys.stderr)
        return 2

    for d in sorted(need_parse):
        dst = os.path.join(OUT, 'parsed', d)
        os.makedirs(dst, exist_ok=True)
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', '_parse_attachments.py'),
                            os.path.join(OUT, 'src', d), dst], capture_output=True, text=True)
        print(f'[파싱 {d}] ' + ' / '.join(l for l in r.stdout.splitlines() if '파싱 완료' in l or 'skip' in l))
    missing = [v['md'] for v in manifest if not os.path.exists(os.path.join(ROOT, v['md']))]
    if missing:
        print(f'⛔ 파싱본 없음 {len(missing)}건 — 도구(soffice·pandoc) 확인. 예: {missing[:3]}', file=sys.stderr)
        return 3

    with open(os.path.join(OUT, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    n_dir = sum(v['source'] == 'direct' for v in manifest)
    n_up = sum(v['effective_to'] is not None for v in manifest)
    print(f'별표 판본 {len(manifest)} · 법령 {len({v["stem"] for v in manifest})} · '
          f'기존 파싱본 재사용 {len(manifest) - n_dir} · 직접 수집·파싱 {n_dir} · 종료일 있음 {n_up}')
    for v in manifest:
        if v['source'] == 'direct' or v['effective_to']:
            print(f"  {v['stem']} {v['att_no']} {v['title'][:34]} @{v['effective_from']}"
                  f"{' → ' + v['effective_to'] if v['effective_to'] else ''} [{v['source']}]")
    return 0


if __name__ == '__main__':
    sys.exit(main())
