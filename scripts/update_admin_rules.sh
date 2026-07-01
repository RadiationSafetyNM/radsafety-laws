#!/bin/bash
# radsafety-laws — 고시(행정규칙) + 별표 수집. admrule-kr raw-fetch + law.go.kr flDownload.
#
# ⚙️  IP 무관 (구 법제처 OpenAPI 판은 고정 IP 필요했으나 admrule-kr 미러로 대체).
#    GitHub Actions(ubuntu-latest)에서 그대로 자동화 가능 → Oracle VM·self-hosted runner 불필요.
#    구 OpenAPI 판: scripts/_collect_admrul_openapi.py (폴백 보존, OC+고정IP 필요).
#
# 사용:  bash scripts/update_admin_rules.sh
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/_collect_admrul.py data/admin-rules
python3 scripts/_collect_attachments.py data/laws data/admin-rules data/attachments
# 서식·별지 메타 레지스트리(빈 양식 카탈로그) — 순수 python, IP·도구 무관(CI 가능).
python3 scripts/_build_forms_registry.py data/laws data/admin-rules data/attachments data/attachments-forms-registry.md
# 별표 원본(HWP/HWPX) → 구조보존 markdown. soffice(+H2Orestart)·pandoc 없으면 자동 skip.
python3 scripts/_parse_attachments.py data/attachments data/attachments-parsed
# 조 단위 RAG 청크(JSONL) — 법령+고시 조 청킹 + 별표·서식 링크. 순수 python(CI 가능).
# 커밋된 attachments-parsed 를 읽어 별표 연결 유지(파싱 skip 되는 CI 에서도 링크 보존).
python3 scripts/_build_chunks.py data/laws data/admin-rules data/attachments-parsed data/chunks/law_chunks.jsonl
