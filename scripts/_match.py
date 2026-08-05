#!/usr/bin/env python3
"""기대출처 매처 — "이 청크가 문항의 정답 출처인가" 판정.

평가 하네스와 서빙 대조 검증(`_lawbot_verify.py`)이 **같은 잣대**로 채점해야
"로컬 하네스 recall 이 서빙에서 재현되는가" 라는 질문이 성립한다. 그래서 여기로 뺐다.

기준판은 `_rag_eval_ollama.py`(2026-08-01 개선판 — 부분수열 완화 + 가지조문 `제N조의M`).
구 `_rag_eval.py`(fastembed CPU 하네스)는 매처가 더 엄격한 옛 버전이고, 과거 측정치와의
재현성을 위해 **일부러 그대로 둔다** — 두 하네스 수치를 섞어 비교하지 말 것.
"""
import re


def norm(s):
    return re.sub(r'[\s_·ㆍ()]', '', s or '')


def subseq(a, b):
    """a 가 b 의 부분수열인가 — 공식 법령명에 낀 조사(의·에·관한 등) 삽입에 견디는 완화 매칭.
    예: '의료분야방사선안전관리기술기준' ⊆ '의료분야의방사선안전관리에관한기술기준'."""
    it = iter(b)
    return all(c in it for c in a)


def match(exp_list, u):
    """exp_list = 평가셋의 expected_sources, u = {'law': 정규화된 법령명, 'art': 제N조, 'byeol': 별표번호}"""
    for e in exp_list:
        en = norm(e)
        mb = re.search(r'별표(\d+)', en)
        mj = re.search(r'(제\d+조(?:의\d+)?)', en)   # 제N조의M 형태(가지조문)까지 포착
        if mb:
            law = en[:mb.start()]
            if law and subseq(law, u['law']) and u['byeol'] == mb.group(1):
                return True
        elif mj:
            law = en[:mj.start()]
            if law and subseq(law, u['law']) and u['art'] == mj.group(1):
                return True
        else:                                       # 순수 법령명 → 접두 매칭(계열 허용, 오탐 방지)
            # subseq 는 '의료법'⊆'의료기기법' 오탐 → 접두로 제한. 계열(법→시행령/규칙)은 접두로 잡힘.
            if en and len(en) >= 2 and (u['law'].startswith(en) or en.startswith(u['law'])):
                return True
    return False
