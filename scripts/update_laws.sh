#!/bin/bash
# radsafety-laws — legalize-kr 에서 방사선·의료 법령만 raw fetch (force-push 면역)
# 사용: bash scripts/update_laws.sh [대상폴더(기본 data/laws)]
set -euo pipefail

BASE="https://raw.githubusercontent.com/legalize-kr/legalize-kr/main/kr"
DST="${1:-data/laws}"
mkdir -p "$DST"

# 법령명|구분 목록 (구분은 공백 구분)
LAWS=(
  "원자력안전법|법률 시행령 시행규칙"
  "방사선및방사성동위원소이용진흥법|법률 시행령"
  "생활주변방사선안전관리법|법률 시행령 시행규칙"
  "의료법|법률 시행령 시행규칙"
  "의료기기법|법률 시행령 시행규칙"
  "의료기사등에관한법률|법률 시행령 시행규칙"
  "약사법|법률 시행령 시행규칙"
  "진단용방사선발생장치의안전관리에관한규칙|보건복지부령"
  "특수의료장비의설치및운영에관한규칙|보건복지부령"
)

ok=0; fail=0
for entry in "${LAWS[@]}"; do
  law="${entry%%|*}"; types="${entry#*|}"
  for typ in $types; do
    if curl -sfL "$BASE/$law/$typ.md" -o "$DST/${law}_${typ}.md"; then
      ok=$((ok+1))
    else
      echo "✗ 실패: $law/$typ" >&2; fail=$((fail+1))
    fi
  done
done

echo "갱신 완료: ${ok}개 성공, ${fail}개 실패"
[ "$fail" -eq 0 ]
