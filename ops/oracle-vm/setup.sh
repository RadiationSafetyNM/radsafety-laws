#!/bin/bash
# Oracle 무료 VM(radsafety-osaka, Ubuntu 24.04 aarch64) 세팅 — 법령 시행일 판본 일일 수집을 kimbi 에서 옮긴다.
#
# 왜 이 VM 인가: 법제처 eflaw API 는 등록한 IP 에서만 응답한다. 집 PC(kimbi)는 꺼질 수 있고 IP 가 바뀐다.
# 방식: GitHub self-hosted runner 를 두지 않는다. kimbi 와 똑같이 systemd 타이머가 scripts/daily_effective.sh 를
#       돌리고, 바뀐 판본을 커밋·푸시하면 GitHub Actions(update-laws.yml)가 청크 재생성·DB 인입을 잇는다.
#       → VM 에는 DB 키가 없다. 필요한 비밀은 푸시용 deploy key 하나뿐이다.
#
# 순서 (VM 에서 ubuntu 사용자로):
#   bash setup.sh base     # 패키지·swap·시간대 (sudo)
#   bash setup.sh key      # deploy key 생성 → 공개키를 GitHub 저장소 Deploy keys 에 등록(쓰기 허용) 〔Dr. Ben〕
#   bash setup.sh clone    # 저장소 clone + hwp2hwpx 빌드
#   bash setup.sh check    # 나가는 IP·법제처 API 응답·수집 시험(커밋 없음)
#   bash setup.sh timer    # systemd 타이머 켜기 — check 가 통과하고 kimbi 타이머를 끈 뒤에
# 절차 전체와 전환 순서는 ops/oracle-vm/README.md.
set -euo pipefail
REPO=git@github-radsafety-laws:RadiationSafetyNM/radsafety-laws.git
DIR=$HOME/projects/radsafety-laws
KEY=$HOME/.ssh/radsafety_laws_deploy

base() {
    sudo apt-get update -q
    # LibreOffice 는 넣지 않는다 — hwp 는 hwp2hwpx(java)가 주 경로이고, 1GB 서버에 무겁다.
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q git python3 openjdk-21-jdk-headless poppler-utils curl
    sudo timedatectl set-timezone Asia/Seoul          # 타이머 07:10 = 한국 시간
    if ! swapon --show | grep -q /swapfile; then      # 1GB 램 — hwp2hwpx 컴파일 여유
        sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap -q /swapfile && sudo swapon /swapfile
        grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    fi
    sudo loginctl enable-linger "$USER"               # 로그아웃해도 user 타이머 유지
    echo "✅ base 완료 — java $(java -version 2>&1 | head -1)"
}

key() {
    mkdir -p ~/.ssh && chmod 700 ~/.ssh
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -f "$KEY" -C "radsafety-osaka-deploy" -N ''
    grep -q 'Host github-radsafety-laws' ~/.ssh/config 2>/dev/null || cat >> ~/.ssh/config <<CFG
Host github-radsafety-laws
    HostName github.com
    User git
    IdentityFile $KEY
    IdentitiesOnly yes
CFG
    chmod 600 ~/.ssh/config
    ssh-keyscan -t ed25519 github.com 2>/dev/null >> ~/.ssh/known_hosts
    sort -u ~/.ssh/known_hosts -o ~/.ssh/known_hosts
    echo "아래 공개키를 GitHub → RadiationSafetyNM/radsafety-laws → Settings → Deploy keys 에 등록 (Allow write access 체크):"
    cat "$KEY.pub"
}

clone() {
    mkdir -p "$(dirname "$DIR")"
    [ -d "$DIR/.git" ] || git clone -q "$REPO" "$DIR"
    cd "$DIR"
    git config user.name  "radsafety-osaka"
    git config user.email "radsafety-osaka@users.noreply.github.com"
    python3 scripts/_hwp2hwpx.py >/dev/null && echo "✅ hwp2hwpx 빌드"
    python3 scripts/_hwp2hwpx.py --selftest | tail -1
    [ -f .env ] || { printf 'MOLEG_OC=benkorea.ai\n# GITHUB_TOKEN=   (선택 — 고시 수집의 GitHub API 한도 완화. 공개 저장소 읽기 전용 토큰)\n' > .env; chmod 600 .env; }
    echo "✅ clone 완료 — $(git log --oneline -1)"
}

check() {
    cd "$DIR"
    echo "나가는 IP: $(curl -s --max-time 10 https://ifconfig.me) ← 법제처 OpenAPI 에 등록한 IP 와 같아야 한다"
    set -a; . ./.env; set +a
    code=$(curl -s -o /tmp/eflaw_probe.xml -w '%{http_code}' --max-time 20 \
        "https://www.law.go.kr/DRF/lawSearch.do?OC=${MOLEG_OC}&target=eflaw&type=XML&query=%EC%9B%90%EC%9E%90%EB%A0%A5%EC%95%88%EC%A0%84%EB%B2%95&display=1")
    if grep -q '<law ' /tmp/eflaw_probe.xml 2>/dev/null; then echo "✅ 법제처 eflaw 응답 (HTTP $code)"
    else echo "⛔ 법제처 eflaw 응답 없음 (HTTP $code) — IP 등록 확인: $(head -c 200 /tmp/eflaw_probe.xml)"; return 1; fi
    # 수집 시험 — 커밋·푸시 없이 작업 트리만. kimbi 가 오늘 이미 돌았다면 "새 판본 없음"이 정상이다.
    python3 scripts/_collect_eflaw.py && echo "✅ _collect_eflaw 통과"
    git status --short | head -5
    git ls-remote -q origin HEAD >/dev/null && echo "✅ GitHub 읽기"
    git push -q --dry-run origin HEAD:main && echo "✅ GitHub 쓰기(deploy key)"
}

timer() {
    mkdir -p ~/.config/systemd/user
    cp "$DIR"/ops/systemd/radsafety-effective.{service,timer} ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now radsafety-effective.timer
    systemctl --user list-timers radsafety-effective.timer --no-pager | head -3
}

case "${1:-}" in base|key|clone|check|timer) "$1" ;; *) sed -n '2,17p' "$0" ;; esac
