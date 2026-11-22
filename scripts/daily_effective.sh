#!/bin/bash
# 법령 시행일 판본 일일 수집 — kimbi 전용(법제처 API 등록 IP 필요). 2026-11-22 신설.
#
# 흐름: git pull → _collect_eflaw.py(새 스냅샷만) → _collect_attachments_effective.py(별표 판본,
#       LibreOffice 파싱) → 고시 판본·고시 별표 판본 → 바뀐 게 있으면 커밋·푸시 → GitHub Actions(update-laws.yml)가 push 로
#       발화해 청크 재생성·챗봇 DB 인입까지 한다. 바뀐 게 없으면 아무것도 안 한다(멱등).
# 왜 kimbi 인가: eflaw API 는 등록 IP 에서만 응답한다(CI 동적 IP 불가). Oracle VM 이 생기면 그리로.
# 설치: ops/systemd/ 의 유닛 2개를 ~/.config/systemd/user/ 에 두고
#       systemctl --user daemon-reload && systemctl --user enable --now radsafety-effective.timer
# 수동: bash scripts/daily_effective.sh
set -uo pipefail
cd "$(dirname "$0")/.."
log() { echo "[$(date '+%F %T')] $*"; }

git fetch -q origin && git merge -q --ff-only origin/main || { log "⛔ pull 실패 — 로컬 변경 확인 필요"; exit 1; }

python3 scripts/_collect_eflaw.py;                 rc1=$?
python3 scripts/_collect_attachments_effective.py; rc2=$?
# 고시(IP 무관)도 매일 — CI 는 주 1회이고 파싱 도구가 없다(2026-11-22)
GITHUB_TOKEN="${GITHUB_TOKEN:-$(gh auth token 2>/dev/null)}" python3 scripts/_collect_admrul_effective.py || log "⚠ 고시 판본 수집 실패"
python3 scripts/_collect_admrul_attachments_effective.py || log "⚠ 고시 별표 판본 수집 실패"
[ $rc1 -ne 0 ] && log "⚠ _collect_eflaw 종료코드 $rc1 (2=API 실패 — IP 등록 확인)"
[ $rc2 -ne 0 ] && log "⚠ _collect_attachments_effective 종료코드 $rc2"

git add data/laws-effective data/attachments-effective data/admin-rules-effective
if git diff --cached --quiet; then
    log "새 판본 없음"
    exit $(( rc1 | rc2 ))
fi
n=$(git diff --cached --name-only | grep -c '@')
git commit -q -m "chore: 법령 시행일 판본 수집 $(date +%F) (kimbi 일일, 변경 ${n}건)" \
    -m "scripts/daily_effective.sh — push 로 update-laws.yml 이 청크 재생성·DB 인입을 이어 한다."
# ssh push 가 끝나 놓고도 안 끝나는 일이 있어 timeout 후 원격 HEAD 로 판정한다
timeout 90 git push -q origin HEAD:main
[ "$(git ls-remote origin refs/heads/main | cut -f1)" = "$(git rev-parse HEAD)" ] \
    && log "✅ 푸시 완료 (${n}건)" || { log "⛔ 푸시 확인 실패"; exit 1; }
exit $(( rc1 | rc2 ))
