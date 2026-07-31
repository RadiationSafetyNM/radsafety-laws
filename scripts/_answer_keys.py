#!/usr/bin/env python3
"""answerable recall 용 정답키 — 표기 정규화 + 자동 제안(2026-08-01 신설).

**왜 필요한가**: 기존 recall 은 "기대출처 *문서* 가 top-k 에 있나"만 본다. 그 문서의
*다른 부분* 이 잡혀도 hit 으로 세므로, 답이 실제로 회수됐는지는 모른다. 2026-08-01 triage
에서 Q12·Q36 이 정확히 그 틈으로 빠져 검색 실패가 생성 실패로 오분류됐다. answerable
recall 은 "top-k 청크 **본문에** 정답 값이 실재하나"를 본다.

**표기 정규화가 핵심**: 같은 값이 코퍼스마다 다르게 적힌다 —
  `1E+09` (별표3 연간섭취한도) · `8 × 10 -1` (별표1 A1/A2, 공백·캐럿 없음)
  · `2.58×10-4` (제2조) · `2.58×10<sup>-5</sup>` (파싱된 별표)
전부 `1e9`·`8e-1`·`2.58e-4`·`2.58e-5` 로 접어서 비교한다. 단위(mSv/Bq)는 정답과 원문이
서로 다른 단위로 적힐 수 있어(별표는 헤더에 단위를 빼둔다) **숫자만** 본다.

정답키는 평가셋의 `answer_keys:` 가 권위. 없으면 `propose()` 가 ground_truth 에서
숫자를 뽑아 제안하지만, **제안은 제안일 뿐** — 큐레이션 안 된 키로 잰 수치는 신뢰하지 말 것.
"""
import re

SUP = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻', '0123456789+-')


def canon(s):
    """수치 표기 접기 — 과학적 표기 전부를 `<가수>e<지수>` 한 형태로."""
    if not s:
        return ''
    s = s.replace('−', '-').replace('–', '-')
    s = re.sub(r'<sup>(.*?)</sup>', r'^\1', s, flags=re.S)      # HTML 윗첨자 → ^
    s = s.translate(SUP)                                        # 유니코드 윗첨자 → 평문
    s = re.sub(r'(?<=\d),(?=\d{3})', '', s)                     # 1,000 → 1000
    # a × 10 ^ b / a × 10 b / aE+b / ae-b  →  a e b
    s = re.sub(r'(\d+(?:\.\d+)?)\s*[×xX*]\s*10\s*\^?\s*([+-]?\d+)',
               lambda m: f'{m.group(1)}e{int(m.group(2))}', s)
    s = re.sub(r'(\d+(?:\.\d+)?)\s*[Ee]\s*([+-]\d+)',
               lambda m: f'{m.group(1)}e{int(m.group(2))}', s)
    return s


def numbers(s):
    """정규화 후 숫자 토큰 집합. 과학적 표기는 통째로 한 토큰."""
    c = canon(s)
    sci = set(re.findall(r'\d+(?:\.\d+)?e-?\d+', c))
    rest = re.sub(r'\d+(?:\.\d+)?e-?\d+', ' ', c)
    return sci | set(re.findall(r'\d+(?:\.\d+)?', rest))


def normtext(s):
    """텍스트 키 비교용 — 공백·구두점 제거."""
    return re.sub(r'[^가-힣0-9A-Za-z]', '', canon(s or ''))


def propose(ground_truth):
    """ground_truth → 숫자 정답키 제안. 조·항·호·별표 번호는 값이 아니므로 먼저 걷어낸다."""
    gt = re.sub(r'제\s*\d+\s*조(?:의\d+)?|제\s*\d+\s*항|제\s*\d+\s*호|별표\s*\d+|별지\s*제?\d+', ' ',
                ground_truth or '')
    return sorted(numbers(gt))


def has_all(text, keys):
    """청크 본문이 정답키를 전부 담고 있나 — 숫자키는 숫자집합, 문자키는 정규화 포함."""
    nums, tx = numbers(text), normtext(text)
    for k in keys:
        k = str(k)
        if re.fullmatch(r'\d+(?:\.\d+)?(?:e-?\d+)?', canon(k).strip()):
            if canon(k).strip() not in nums:
                return False
        elif normtext(k) not in tx:
            return False
    return True
