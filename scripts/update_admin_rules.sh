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
# 별표 원본(HWP/HWPX) → 구조보존 markdown. soffice(+H2Orestart)·pandoc 없으면 자동 skip.
python3 scripts/_parse_attachments.py data/attachments data/attachments-parsed
