#!/usr/bin/env python3
"""생성 답변 감사 — **누락이 아니라 모순**을 본다 (2026-08-03 신설).

왜 별도 도구인가: 정답키(`_answer_keys.py`)는 *검색* 평가용이다. 같은 키를 모델 답변에
적용하면 정확도가 아니라 **장황함**을 재게 된다 — 간결한 답이 감점되고, 뜻이 같아도
표현이 다르면 오답 처리된다(전수 확인 결과 미포함 8건이 전부 오탐이었다).

그래서 이 도구는 반대를 본다: 답변에 **회수된 원문 어디에도 없는 수치**가 있으면
지어냈을 가능성이 있다. 누락은 간결함일 수 있지만, 없는 숫자가 나오는 건 간결함이 아니다.
이게 채점 3축 중 `no_hallucination` 의 기계 근사다.

⚠️ 플래그는 **판정이 아니라 사람이 볼 후보**다. 단위 환산(mSv↔rem)·합산·반올림은
원문에 없는 숫자를 정당하게 만들어낸다. 그래서 결과는 "확인하십시오"이지 "틀렸다"가 아니다.

사용: python3 scripts/_answer_audit.py out/rag_answers_gemini.md [out/rag_answers_claude.md ...]
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _answer_keys import canon                                    # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

# 값처럼 보이는 숫자만 — 단위가 붙었거나 과학적 표기인 것. 조·항·호·서식 번호는 값이 아니다.
UNIT = (r'mSv|밀리시버트|Sv|rem|Bq|TBq|GBq|MBq|mCi|Ci|C/kg|R|mR|mA|kV|'
        r'년|개월|일|시간|분|명|배|퍼센트|%|m|㎡|㎥|cm|mm')
# canon() 을 먼저 적용하므로 지수는 모두 `2.58e-6` 형태다. **지수부를 통째로 삼켜야** 한다 —
# 안 그러면 `2.58e-6C/kg` 에서 끝의 "6" 만, `3e0 TBq` 에서 "0" 만 잘려 나와 헛플래그가 된다.
# (?<![\w.]) 로 토큰 중간 매칭도 막는다.
NUM = r'(?<![\w.])\d+(?:\.\d+)?(?:e-?\d+)?'
VAL = re.compile(rf'({NUM})\s*(?:{UNIT})\b')
SCI = re.compile(NUM + r'(?<=\d)e-?\d+' if False else r'(?<![\w.])\d+(?:\.\d+)?e-?\d+')
# 값이 아닌 숫자는 먼저 지운다 — 조·항·호 번호, 서식 번호, 그리고 **날짜**
# ("2026년 7월 9일 개정" 의 9 가 근거 없는 수치로 잡혔다).
STRIP = re.compile(
    r'제\s*\d+\s*(?:조|항|호|목|란|장|절)(?:의\d+)?|별표\s*\d+|별지\s*제?\d+호?|<개정[^>]*>|'
    r'\d{4}\s*[.년]\s*\d{1,2}\s*[.월]\s*\d{1,2}\s*일?|\d{4}\s*년|제\s*\d+\s*호')


ANYNUM = re.compile(r'(?<![\w.])\d+(?:\.\d+)?(?:e-?\d+)?')


def values(text, strict=True):
    """strict=True(답변측): 단위가 붙었거나 과학적 표기인 '값'만 — 목록번호·연도 노이즈 배제.
    strict=False(원문측): **모든 숫자**를 담는다.

    ⚠️ 비대칭이 의도다. 표 원문은 단위를 헤더에만 두고 셀은 `1.2 | 33 | 0.07` 처럼 맨숫자라,
    양쪽에 같은 추출기를 쓰면 원문에 있는 값도 '근거 없음'으로 잡힌다(2026-08-03 실제로
    Q3·Q27·Q28 에서 두 모델이 동시에 오탐됐다 — 둘이 같은 값을 지어냈을 리 없다).
    원문측을 넉넉히 잡아 **오탐보다 미탐 쪽으로** 기울인다."""
    t = STRIP.sub(' ', canon(text))
    if not strict:
        return {m for m in ANYNUM.findall(t)}
    out = {canon(m).strip() for m in SCI.findall(t)}
    out |= {canon(m.group(1)).strip() for m in VAL.finditer(t)}
    return {v for v in out if v}


def load_chunks():
    d = {}
    for ln in open(os.path.join(ROOT, 'data/chunks/law_chunks.jsonl'), encoding='utf-8'):
        r = json.loads(ln)
        d[r['chunk_id']] = r['content']
    return d


def audit(path, chunks):
    t = open(path, encoding='utf-8').read()
    blocks = re.split(r'\n## (Q\d+) · ', t)[1:]
    model = (re.search(r'생성 `([^`]+)`', t) or [None, '?'])[1]
    rows, fmt = [], []
    for i in range(0, len(blocks), 2):
        qid, blk = blocks[i], blocks[i + 1]
        ans = re.search(r'\*\*모델 답변\*\*\s*(.*?)\n<!--', blk, re.S)
        ans = ans.group(1).strip() if ans else ''
        cids = (re.search(r'<!-- chunks: (.*?) -->', blk) or [None, ''])[1].split(',')
        src = ' '.join(chunks.get(c, '') for c in cids if c)
        if not src:
            continue
        unsupported = sorted(values(ans, strict=True) - values(src, strict=False))
        if unsupported:
            rows.append((qid, unsupported))
        bad = []
        if not re.search(r'^「근거」', ans, re.M):
            bad.append('「근거」줄 없음')
        if re.search(r'\d\s*[Ee][+-]\d|\d\s*×\s*10\s+-?\d', ans):
            bad.append('원문 지수표기 잔존')
        if re.search(r'오기|정정합니다', ans):
            bad.append('본문 자기정정')
        if bad:
            fmt.append((qid, bad))
    n = len(blocks) // 2
    print(f'\n■ {os.path.basename(path)}  (생성 {model} · {n}문항)')
    print(f'  형식 위반            {len(fmt)}건' + (f'  {fmt}' if fmt else ''))
    print(f'  ⚠ 근거 없는 수치     {len(rows)}건' + ('' if rows else '  — 답변의 모든 값이 회수 원문에 있음'))
    for qid, vs in rows:
        print(f'      {qid}: {", ".join(vs[:8])}')
    return len(rows), len(fmt)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    ch = load_chunks()
    for p in sys.argv[1:]:
        audit(p, ch)
    print('\n※ 플래그는 사람이 볼 후보다. 단위 환산·합산·반올림은 정당하게 새 숫자를 만든다.')
