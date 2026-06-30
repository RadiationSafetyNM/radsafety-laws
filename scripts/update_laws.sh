#!/bin/bash
# radsafety-laws — legalize-kr 에서 방사선·의료 법령만 raw fetch (force-push 면역)
# 사용: bash scripts/update_laws.sh [대상폴더(기본 data/laws)]
set -euo pipefail

BASE="https://raw.githubusercontent.com/legalize-kr/legalize-kr/main/kr"
DST="${1:-data/laws}"
mkdir -p "$DST"

# 감시 대상 목록은 watchlist.toml(단일 권위)에서 읽는다 — 하드코딩 금지.
# --fetch-list --all = core+peripheral 전부(의료 중심이라 peripheral 도 의료법 → 코퍼스·
# peripheral REVIEW 기준치 둘 다에 필요). core 만 원하면 --all 제거.
ok=0; fail=0
while IFS=$'\t' read -r law typ; do
  [ -z "$law" ] && continue
  if curl -sfL "$BASE/$law/$typ.md" -o "$DST/${law}_${typ}.md"; then
    ok=$((ok+1))
  else
    echo "✗ 실패: $law/$typ" >&2; fail=$((fail+1))
  fi
done < <(python3 "$(dirname "$0")/_watchlist.py" --fetch-list --all)

echo "갱신 완료: ${ok}개 성공, ${fail}개 실패"
[ "$fail" -eq 0 ]
