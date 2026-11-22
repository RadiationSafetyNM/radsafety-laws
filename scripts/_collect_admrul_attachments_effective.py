#!/usr/bin/env python3
"""고시 별표 시행일 판본 — data/admin-rules-effective 스냅샷의 첨부파일 목록 → 별표 판본 manifest (2026-11-22).

법령 별표판(_collect_attachments_effective.py)의 고시 버전. 차이는 **판본 지문**이다:
  법령은 eflaw API 의 별표 본문 해시를 쓰지만(등록 IP 필요), 고시 스냅샷(admrule-kr 커밋 이력)은
  본문 없이 첨부파일 목록만 준다. 그래서 **파일링크의 flSeq**(법제처 첨부파일 일련번호)를 지문으로 쓴다.
  파일이 바뀌면 새 flSeq 가 붙는다. 내용이 같은데 재업로드로 번호만 바뀌는 경우는 같은 본문의 새 행이
  생길 뿐(옛 행은 그 날 닫힘) 답은 틀리지 않는다 — 미탐보다 안전한 쪽.

본문은 어디서 오나:
  · attachments-parsed/ 의 md 가 그 판본임이 근거 대장(parsed-provenance.json)에 있으면 재사용
    (미러 고시 파일의 flSeq 와 맞춰 보면 안 된다 — 법령판 docstring 의 2026-11-23 사고 참조)
  · 없거나 다르면 파일링크로 원본+PDF 를 받아
    data/attachments-effective/admin/src/<시행일>/ → parsed/<시행일>/ 로 파싱(LibreOffice·pandoc 필요)
flDownload 는 공개라 IP 무관 — CI 에서도 돈다(단 파싱이 필요한 판본이 생기면 도구 있는 로컬에서).

산출: data/attachments-effective/admin-manifest.json (법령판 manifest.json 과 같은 행 형식, source=mirror|direct).
_build_chunks.py 가 두 manifest 를 모두 읽고, manifest 가 맡은 문서의 미러 파싱본은 건너뛴다.
사용: python3 scripts/_collect_admrul_attachments_effective.py
종료 코드: 0 정상 / 2 다운로드 실패 / 3 파싱본 없음.
"""
import glob, json, os, re, subprocess, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _collect_attachments_effective import base_name, key_str, download, provenance   # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
EFF = os.environ.get('ADMRUL_EFFECTIVE', os.path.join(ROOT, 'data', 'admin-rules-effective'))
OUT = os.environ.get('ADMRUL_ATT_OUT', os.path.join(ROOT, 'data', 'attachments-effective'))
PARSED = os.path.join(ROOT, 'data', 'attachments-parsed')
FLSEQ = re.compile(r'flSeq=(\d+)')


def attachments(path):
    """frontmatter 첨부파일 목록 중 별표만 → [{no, branch, title, file_link, pdf_link, sha}]."""
    text = open(path, encoding='utf-8').read()
    fm = text.split('\n---', 1)[0]
    out = []
    for blk in re.split(r'\n- (?=별표번호:)', fm.split('첨부파일:', 1)[1] if '첨부파일:' in fm else ''):
        g = lambda k: (re.search(rf"{k}:\s*'?([^'\n]*)'?", blk) or [None, ''])[1].strip()
        if g('별표구분') != '별표' or not g('별표번호'):
            continue
        fl = FLSEQ.search(g('파일링크'))
        out.append({'no': g('별표번호'), 'branch': g('별표가지번호') or '00', 'title': g('제목'),
                    'file_link': g('파일링크'), 'pdf_link': g('PDF링크'),
                    'sha': fl.group(1) if fl else g('파일링크')})
    return out


def snapshots():
    by = defaultdict(list)
    for p in glob.glob(os.path.join(EFF, '*@*.md')):
        m = re.match(r'(.+)@(\d{4}-\d{2}-\d{2})_(\d+)\.md$', os.path.basename(p))
        by[m.group(1)].append((m.group(2), m.group(3), attachments(p)))
    return {k: sorted(v) for k, v in by.items()}


def chains(stem, snaps):
    """스냅샷 연쇄 → 별표 판본(법령판 chains 와 같은 규칙, 지문 = flSeq)."""
    opened, out = {}, []
    for date, serial, units in snaps:
        seen = set()
        for u in units:
            k = (u['no'], u['branch'])
            seen.add(k)
            o = opened.get(k)
            if o is not None and o['sha'] == u['sha']:
                continue
            if o is not None:
                o['effective_to'] = date
            v = {'stem': stem, 'no': u['no'], 'branch': u['branch'], 'att_no': key_str(u['no'], u['branch']),
                 'title': u['title'], 'effective_from': date, 'effective_to': None, 'sha': u['sha'],
                 'serial': serial, 'file_link': u['file_link'], 'pdf_link': u['pdf_link']}
            out.append(v)
            opened[k] = v
        for k in [k for k in opened if k not in seen]:
            opened.pop(k)['effective_to'] = date
    return out


def main():
    manifest, need_parse, prov = [], set(), provenance()
    try:
        for stem, snaps in sorted(snapshots().items()):
            for v in chains(stem, snaps):
                base = base_name(v['title'], stem)
                reuse = os.path.join(PARSED, base + '.md')
                if os.path.exists(reuse) and prov.get(base) == v['sha']:
                    v['md'], v['source'] = os.path.relpath(reuse, ROOT), 'mirror'
                else:
                    src = os.path.join(OUT, 'admin', 'src', v['effective_from'])
                    os.makedirs(src, exist_ok=True)
                    if v['file_link']:
                        download(v['file_link'], os.path.join(src, base), False)
                    if v['pdf_link']:
                        download(v['pdf_link'], os.path.join(src, base), True)
                    md = os.path.join(OUT, 'admin', 'parsed', v['effective_from'], base + '.md')
                    v['md'], v['source'] = os.path.relpath(md, ROOT), 'direct'
                    if not os.path.exists(md):
                        need_parse.add(v['effective_from'])
                manifest.append(v)
    except Exception as ex:
        print(f'⛔ 수집 실패: {ex}', file=sys.stderr)
        return 2
    for d in sorted(need_parse):
        dst = os.path.join(OUT, 'admin', 'parsed', d)
        os.makedirs(dst, exist_ok=True)
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'scripts', '_parse_attachments.py'),
                            os.path.join(OUT, 'admin', 'src', d), dst], capture_output=True, text=True)
        print(f'[파싱 {d}] ' + ' / '.join(l for l in r.stdout.splitlines() if '파싱 완료' in l or 'skip' in l))
    missing = [v['md'] for v in manifest if not os.path.exists(os.path.join(ROOT, v['md']))]
    if missing:
        print(f'⛔ 파싱본 없음 {len(missing)}건 — 도구(soffice·pandoc) 확인. 예: {missing[:3]}', file=sys.stderr)
        return 3
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'admin-manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    n_dir = sum(v['source'] == 'direct' for v in manifest)
    print(f"고시 별표 판본 {len(manifest)} · 고시 {len({v['stem'] for v in manifest})} · "
          f"재사용 {len(manifest) - n_dir} · 직접 수집·파싱 {n_dir} · 종료일 있음 "
          f"{sum(v['effective_to'] is not None for v in manifest)}")
    for v in manifest:
        if v['source'] == 'direct' or v['effective_to']:
            print(f"  {v['stem'][:24]} {v['att_no']} @{v['effective_from']}"
                  f"{' → ' + v['effective_to'] if v['effective_to'] else ''} [{v['source']}]")
    return 0


if __name__ == '__main__':
    sys.exit(main())
