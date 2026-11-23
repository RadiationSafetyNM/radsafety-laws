# Oracle 무료 VM 으로 일일 수집 옮기기

법령 시행일 판본 일일 수집(`scripts/daily_effective.sh`)을 kimbi 에서 Oracle 무료 VM `radsafety-osaka` 로 옮기는 절차.
2026-11-23 작성 — VM 이 생기기 전에 미리 준비한 것이라 **VM 에서 아직 돌려 보지 않았다.**

## 방식

GitHub self-hosted runner 를 두지 않는다. VM 은 kimbi 와 똑같이 systemd 타이머로 `daily_effective.sh` 를 돌리고,
바뀐 판본을 커밋·푸시한다. 푸시가 `update-laws.yml` 을 깨워 청크 재생성·DB 인입을 잇는다.

- VM 에 두는 비밀 = 저장소 푸시용 **deploy key 하나**. DB·Gemini 키는 두지 않는다(인입은 GitHub Actions).
- LibreOffice 는 설치하지 않는다. hwp 는 hwp2hwpx(java)로 읽는다. 변환에 실패한 별표가 나오면
  `_collect_attachments_effective.py` 가 "파싱본 없음"으로 알리고, 그 건만 kimbi 에서 파싱해 커밋한다.

## 순서

| # | 누가 | 할 일 |
|---|---|---|
| 1 | Claude | 임시 공인 IP 를 **예약 IP** 로 전환 (`oci network public-ip …`). VM 을 지워도 IP 가 남는다 |
| 2 | Dr. Ben | 법제처 OpenAPI(open.law.go.kr) → 신청 정보 수정 → **서버 IP 에 예약 IP 추가**. kimbi IP 는 지우지 않는다(전환 확인 전까지 병행) |
| 3 | Claude | `scp ops/oracle-vm/setup.sh` → VM 에서 `bash setup.sh base` → `bash setup.sh key` |
| 4 | Dr. Ben | 출력된 공개키를 GitHub 저장소 **Settings → Deploy keys** 에 등록, **Allow write access** 체크 |
| 5 | Claude | `bash setup.sh clone` → `bash setup.sh check` (나가는 IP·법제처 응답·GitHub 쓰기 확인) |
| 6 | Claude | kimbi 타이머 끄기: `systemctl --user disable --now radsafety-effective.timer` |
| 7 | Claude | VM 에서 `bash setup.sh timer` |
| 8 | Claude | 다음 날 07:10 이후 `journalctl --user -u radsafety-effective` 확인. 커밋 메시지의 머신 이름이 `radsafety-osaka` 인지 본다 |

kimbi 와 VM 타이머를 **동시에 켜 두지 않는다** — 수집 자체는 멱등이지만 같은 시각에 둘이 푸시하면 한쪽이 실패한다.

## 운영

- 접속: `ssh -i ~/.ssh/oci_radsafety ubuntu@<예약 IP>` (kimbi·ai4lt 공개키가 둘 다 들어 있다).
- 수동 실행: `systemctl --user start radsafety-effective.service`.
- 되돌리기: VM 타이머를 끄고 kimbi 타이머를 다시 켠다. kimbi IP 등록이 남아 있어야 한다.
- 유휴 회수: Oracle 은 7일간 CPU·네트워크·메모리가 모두 낮은 무료 서버를 회수할 수 있다. 1GB 서버는 메모리 사용률이
  기준(20%)을 넘는 것이 보통이지만, 회수 경고 메일이 오면 사양·상주 작업을 다시 본다.
- 보안목록은 SSH 22 인바운드만 연다. VM 에 환자·개인정보·vault 사본을 두지 않는다.
