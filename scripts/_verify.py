"""답변 검증 표시 — 전문가 보조용(2026-11-22 Dr. Ben 결정). 막지 않고 **경고만** 붙인다.

  ① 근거 강도   top-1 유사도로 "범위 밖일 수 있음(<OFF)"·"근거 약함(<WEAK)" 표시
  ② 숫자 대조   답변의 값(단위 붙은 수·과학적 표기)이 회수 원문 어디에도 없으면 표시
  ③ 인용 대조   「근거」 줄의 제N조·별표N 이 회수된 조문 제목에 없으면 표시

기준값(2026-11-22 평가셋 36문항 + 범위 밖 질문 5개 실측, gemini-embedding-001):
  범위 밖 질문 top-1 최고 0.646 · 정답 회수 문항 최저 0.697 · 코퍼스 공백 Q14 0.714 · 결함 Q15 0.719
  → OFF 0.66, WEAK 0.72 (전문가용이라 헛경고를 감수하고 넉넉히).
런타임 src/lib/verify.ts(radsafety-lawbot)와 같은 규칙이어야 한다 — 한쪽을 고치면 다른 쪽도.
② 원문측은 _answer_audit.values(strict=False)(모든 숫자), 답변측은 answer_values()(아래 — 한글 조사 허용).
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _answer_audit import values, STRIP, SCI, UNIT, NUM   # noqa: E402
from _answer_keys import canon                             # noqa: E402

# 답변측 값 추출 — _answer_audit.VAL 은 단위 뒤에 \b 를 요구해 "500 mSv입니다"·"mSv를" 처럼
# 한글이 바로 붙으면 놓친다(파이썬 \b 는 한글도 단어 문자로 본다). 한국어 답변은 거의 다
# 그렇게 끝나므로 여기서는 "단위 뒤에 영문자만 아니면" 으로 넓힌다. 평가 도구는 비교 가능성을
# 위해 종전 규칙 그대로 둔다.
VAL2 = re.compile(rf'({NUM})\s*(?:{UNIT})(?![A-Za-z])')


def answer_values(text):
    t = STRIP.sub(' ', canon(text))
    out = {canon(m).strip() for m in SCI.findall(t)}
    out |= {canon(m.group(1)).strip() for m in VAL2.finditer(t)}
    return {v for v in out if v}

OFF, WEAK = 0.66, 0.72
REF = re.compile(r'제\s*\d+\s*조(?:\s*의\s*\d+)?|별표\s*\d+(?:\s*의\s*\d+)?')
_sq = lambda s: re.sub(r'\s+', '', s)


def strength(top):
    return 'off' if top < OFF else 'weak' if top < WEAK else 'ok'


def unsupported_numbers(answer, texts):
    src = set()
    for t in texts:
        src |= values(t, strict=False)
    return sorted(answer_values(answer) - src)


def uncited_refs(answer, titles):
    line = next((l for l in answer.splitlines() if l.strip().startswith('「근거」')), '')
    # 별표 제목 안의 "(제2조제4호 관련)" 같은 참조는 별표 인용의 일부라 따로 세지 않는다
    line = re.sub(r'\([^)]*관련\)', '', line)
    pool = _sq(' '.join(titles))
    return sorted({_sq(m) for m in REF.findall(line) if _sq(m) not in pool})


def check(answer, hits, extra_texts=()):
    """hits: [{'similarity', 'title', 'content'}]."""
    top = max((h['similarity'] for h in hits), default=0.0)
    return {
        'strength': strength(top), 'top': round(top, 3),
        'unsupportedNumbers': unsupported_numbers(answer, [h['content'] for h in hits] + list(extra_texts)),
        'uncitedRefs': uncited_refs(answer, [h['title'] for h in hits]),
    }
