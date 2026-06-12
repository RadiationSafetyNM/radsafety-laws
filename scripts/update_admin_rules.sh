#!/bin/bash
# 법제처 OpenAPI → 원자력안전법 계통(의료·일반 방사선안전) 행정규칙 수집·갱신
#
# ⚠️ 호출하는 서버의 IP 가 법제처 OpenAPI 에 등록돼 있어야 한다.
#    GitHub Actions 는 동적 IP 라 불가 → 고정 IP 환경(서버/등록된 PC)에서 실행.
#    (그래서 laws[legalize-kr]=CI 자동, admin-rules[법제처]=고정IP 수동 으로 분리)
#
# 사용:  OC=<법제처이메일ID> bash scripts/update_admin_rules.sh [대상폴더]
set -euo pipefail
: "${OC:?OC 환경변수 필요 — 법제처 가입 이메일 ID (예: OC=benkorea.ai)}"
DST="${1:-data/admin-rules}"
python3 "$(dirname "$0")/_collect_admrul.py" "$OC" "$DST"
