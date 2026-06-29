# radsafety-laws — RadSafety 법령 데이터 레이어

RadSafety.kr 가족의 **방사선·의료 법령 데이터 repo**. 앱·챗봇이 아니라 그것들이 소비하는 **순수 데이터 + 수집 스크립트 + CI** 다.

- 원격: `git@github.com:RadiationSafetyNM/radsafety-laws.git` (RadiationSafetyNM org)
- 기본 브랜치: `main` (RadSafety 가족 공통 규약 — 모든 repo `main`)
- 짝 자산: **[[radsafety-pwa]]**(선량관리 앱) · **RadSafety-lawbot**(이 데이터 위의 RAG 챗봇, 설계 단계)
- 운영 권위 원본(설계·기획·타임라인) = vault 허브 `knowledge/01_projects/2026-01_RadSafety-pwa/RadSafety-lawbot/RadSafety-lawbot.md` (lawbot 은 독립 프로젝트가 아니라 radsafety-pwa 의 모듈 — 2026-06-27 §결정1). **이 repo 는 데이터·코드만, 기획은 vault.**

---

## 핵심 원칙 — 재현성(전 데이터가 파이프라인 산출)

이 repo 의 `data/` 는 **전부 legalize-kr 생태계 파이프라인이 자동 생성**한다. **수동 자료(외부 PDF·해설·ICRP·가이드 등)는 절대 커밋하지 않는다** — 2026-06-14 `37c5ecd` 에서 `data/commentary` 등 수동 자료를 의도적으로 제거했다. 누구나 스크립트만 돌리면 동일 데이터를 재현할 수 있어야 한다는 게 이 repo 의 계약이다.

> 새 데이터를 넣고 싶으면: 먼저 "이게 legalize-kr/admrule-kr/공개 flDownload 로 자동 수집 가능한가?" 를 물어라. 아니면 넣지 말고 vault 쪽(resources)으로 보낸다.

---

## 구조

```
data/
  laws/          # [자동·CI] legalize-kr raw fetch — 법률·시행령·시행규칙·부령 (현재 22 파일)
  admin-rules/   # [자동·CI] admrule-kr raw fetch — 의료 방사선 삼원화 고시·예규 (현재 23 파일)
  attachments/   # [자동·CI] law.go.kr flDownload — 법령·고시 frontmatter 의 방사선 별표·서식 PDF (현재 188)
scripts/
  update_laws.sh             # laws 갱신 (legalize-kr raw fetch) — 법령 목록이 이 파일 안에 하드코딩
  update_admin_rules.sh      # 고시(admrule-kr) + 별표(flDownload) 갱신 — 둘 다 IP 무관
  _collect_admrul.py         # 고시 수집 (admrule-kr GitHub raw)
  _collect_attachments.py    # 별표 PDF 수집 (frontmatter 링크 → 공개 flDownload)
  _collect_admrul_openapi.py # ⚠️ 폴백 전용 — 구 법제처 OpenAPI 판(OC+고정IP 필요). 평시 미사용.
.github/workflows/update-laws.yml  # 주간 cron 자동 갱신 (월 03:00 KST / ubuntu-latest)
```

### 포함 법령 (9개 법령군)

방사선·핵의학(원자력안전법·방사선이용진흥법·생활주변방사선안전관리법) · 진단/장비(진단용방사선발생장치규칙·특수의료장비규칙) · 의료 일반(의료법·의료기기법·의료기사법) · 방사성의약품(약사법). 각 법령 = 법률 + 시행령 + 시행규칙(또는 부령) 세트. **법령 추가/제거는 `scripts/update_laws.sh` 의 `LAWS=( ... )` 배열을 편집**한다.

---

## 갱신 모델

- **자동**: `update-laws.yml` 이 매주 월요일 03:00 KST(`cron: "0 18 * * 0"`)에 laws→고시→별표 순으로 수집 후 변경 있으면 `github-actions[bot]` 이 커밋·푸시. 변경 없으면 커밋 생략.
- **수동**: `workflow_dispatch` 버튼 또는 로컬에서:
  ```bash
  bash scripts/update_laws.sh data/laws        # 법령만
  bash scripts/update_admin_rules.sh           # 고시 + 별표
  ```
- **IP 무관**: 전 수집 경로가 GitHub raw fetch + 공개 flDownload 라 `ubuntu-latest` 에서 그대로 돈다. (구 설계는 법제처 OpenAPI 직접 호출이라 고정 IP·Oracle VM·self-hosted runner 가 필요했으나 **2026-06-13 admrule-kr 피벗(`517bc2b`)으로 전부 폐기**. 관련 메모리: `project_moleg_openapi_requires_fixed_ip`.)
- **1차 출처**: 국가법령정보센터(law.go.kr) OpenAPI → legalize-kr/admrule-kr 가공(markdown+frontmatter) → 본 repo raw fetch. raw fetch 라 legalize-kr 의 force-push·history rewrite 에 면역(항상 main 최신).

---

## 작업 규칙

- 데이터(`data/`) 직접 손편집 금지 — CI 가 덮어쓴다. 데이터를 바꾸려면 *스크립트/법령목록* 을 고친다.
- 커밋 시 변경 파일만 명시적으로 `git add <path>` (vault `/git-routine` 안전규칙과 동일 — `git add .`/`-A` 금지).
- 수집 스크립트 수정 후엔 로컬에서 한 번 돌려 산출 diff 를 확인하고 커밋.
- 법령 텍스트 = 공공저작물(자유 이용). 가공 구조·스크립트 = MIT. ⚠️ 가공본이므로 법적 판단이 걸린 정확한 조문은 [law.go.kr](https://www.law.go.kr) 원본 대조 필요.

---

## 이 repo 를 넘어서는 작업은 vault 로

RAG 챗봇 설계·기술 스택·평가셋·radsafety-pwa 연동 등 **기획/설계는 이 repo 가 아니라 vault 허브**(`2026-06_RadSafety-lawbot`)가 정본이다. 이 repo 는 그 설계가 소비할 데이터 레이어를 *생산·유지*하는 경계까지만 책임진다.
