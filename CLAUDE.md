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
watchlist.toml                     # ★ 감시 대상 법령의 단일 권위(source of truth) — 포함기준·법별 근거 동봉
data/
  laws/          # [자동·CI] legalize-kr raw fetch — 법률·시행령·시행규칙·부령
  admin-rules/   # [자동·CI] admrule-kr raw fetch — 의료 방사선 삼원화 고시·예규
  attachments/   # [자동·CI] law.go.kr flDownload — 방사선 별표·서식의 원본(HWP/HWPX)+PDF 병행 수집
                 #   (2026-07-01~ 원본 추가. 원본=파싱 충실도, PDF=비전 대조·폴백. 같은 stem·확장자만 다름)
  attachments-parsed/  # [자동·로컬] 별표 원본 → 구조보존 markdown (soffice+H2Orestart→docx→pandoc)
  attachments-forms-registry.md  # [자동·CI] 서식·별지 메타 카탈로그(빈 양식 — 본문 파싱 ✗, 제목·근거조·링크만)
  chunks/law_chunks.jsonl  # [자동·CI] 조 단위 RAG 청크(content+metadata). 임베딩 전 단계 — pwa 가 소비
scripts/
  _watchlist.py              # watchlist.toml 로더(tomllib·stdlib) — fetch·audit 양쪽에 공급
  update_laws.sh             # laws 갱신 (legalize-kr raw fetch) — 목록은 _watchlist.py 에서 읽음(하드코딩 제거)
  update_admin_rules.sh      # 고시(admrule-kr) + 별표(flDownload) 수집 + 별표 md 파싱 — 수집부는 IP 무관
  _collect_admrul.py         # 고시 수집 (admrule-kr GitHub raw)
  _collect_attachments.py    # 별표 원본(HWP/HWPX)+PDF 수집 (frontmatter 파일링크·PDF링크 → 공개 flDownload)
  _parse_attachments.py      # 별표 원본 → 구조보존 markdown. 도구(soffice/pandoc) 없으면 자동 skip(CI 안전).
                             #   docx 변환 손실(md 본문 ≪ PDF 텍스트) 시 PDF 텍스트로 폴백(parse_note: pdf_fallback)
  _build_forms_registry.py   # 서식·별지 메타 레지스트리 생성(순수 python, 수집기와 동일 게이트 → disk 1:1)
  _build_chunks.py           # 조 단위 RAG 청크 생성(딥리서치 계층청킹 설계 — 조 기본·긴 조는 항 분할, 메타 6필드, 조↔별표·서식 링크)
  _collect_admrul_openapi.py # ⚠️ 폴백 전용 — 구 법제처 OpenAPI 판(OC+고정IP 필요). 평시 미사용.
  _freshness_audit.py        # 신선도 감사 — law.go.kr OpenAPI(권위) ↔ 우리 frontmatter MST 비교. ⚠️OpenAPI 직접호출이라 IP 등록 필요
.github/workflows/update-laws.yml  # 주간 cron 자동 갱신 (월 03:00 KST / ubuntu-latest)
```

### 포함 법령 — 7개 법률 패밀리 (= 22개 법령)

독립 법률 7그루(서로 대등 — 위계는 패밀리 *내부* 법률>시행령>시행규칙 위임사슬만):

| 분류 | 법률 패밀리 |
|---|---|
| 방사선·핵의학 | 원자력안전법 · 방사선및방사성동위원소이용진흥법 · 생활주변방사선안전관리법 |
| 의료 | 의료법 · 의료기기법 · 의료기사등에관한법률 · 약사법 |

각 패밀리 = 법률 + 시행령 + 시행규칙(또는 부령) 세트. **의료법 패밀리는 추가로 진단용방사선발생장치규칙·특수의료장비규칙**(제37·38조 직접 위임 부령)을 멤버로 포함.

**포함 기준(단일 게이트)**: *"이 법(본문)이 방사선·방사성물질 *자체*를 규율하는 조항을 갖는가?"* — 직업이 방사선에 노출되는가(✗)가 아님. (예: 간호법은 방사선 조항 0개 → 제외. 핵의학 간호사 보호는 원자력안전법·진단용규칙이 이미 커버.) 상세·법별 근거는 `watchlist.toml` 상단 주석. 관련 메모리: `project_radsafety_laws_watchlist`.

> **법령 추가/제거는 `watchlist.toml` 한 곳만 편집**한다 (`[[family]]` 추가/삭제). fetch(update_laws.sh)·audit(_freshness_audit.py) 가 모두 거기서 읽는다 — 코드 하드코딩 금지.

---

## 갱신 모델

- **자동**: `update-laws.yml` 이 매주 월요일 03:00 KST(`cron: "0 18 * * 0"`)에 laws→고시→별표 순으로 수집 후 변경 있으면 `github-actions[bot]` 이 커밋·푸시. 변경 없으면 커밋 생략.
- **수동**: `workflow_dispatch` 버튼 또는 로컬에서:
  ```bash
  bash scripts/update_laws.sh data/laws        # 법령만
  bash scripts/update_admin_rules.sh           # 고시 + 별표
  ```
- **IP 무관**: 전 *수집* 경로가 GitHub raw fetch + 공개 flDownload 라 `ubuntu-latest` 에서 그대로 돈다. (구 설계는 법제처 OpenAPI 직접 호출이라 고정 IP·Oracle VM·self-hosted runner 가 필요했으나 **2026-06-13 admrule-kr 피벗(`517bc2b`)으로 전부 폐기**. 관련 메모리: `project_moleg_openapi_requires_fixed_ip`.)
- **별표 파싱은 로컬 전용(현재)**: `_parse_attachments.py` 는 LibreOffice+H2Orestart+pandoc 필요 → CI(ubuntu-latest)엔 미설치라 자동 skip. 따라서 `attachments-parsed/` 는 도구 갖춘 로컬에서 재생성·커밋한다. (CI 파싱 원하면 workflow 에 tool 설치 스텝 추가 — 향후 과제.)
- **1차 출처**: 국가법령정보센터(law.go.kr) OpenAPI → legalize-kr/admrule-kr 가공(markdown+frontmatter) → 본 repo raw fetch. raw fetch 라 legalize-kr 의 force-push·history rewrite 에 면역(항상 main 최신).

---

## 신선도 감사 (data freshness audit)

데이터 수집(위)은 legalize-kr 미러 경유라 **권위측(법제처)과의 시차를 스스로 검증 못 한다** — legalize-mcp·admrule-kr 도 같은 미러라 독립검증 불가. 그래서 `_freshness_audit.py` 가 **law.go.kr OpenAPI 를 직접 호출**해 우리 frontmatter `법령MST` ↔ 권위측 *최신 공포* MST 를 대조한다.

```bash
python3 scripts/_freshness_audit.py <OC> data/laws    # OC = benkorea.ai (가입 이메일 @앞, 점 포함)
```

- **비교축은 `target=eflaw`(시행일법령)** — `현행연혁코드`(시행예정/현행/연혁)로 *공포됐으나 미시행* 개정까지 본다. lawSearch `target=law`(현행)는 시행 중 버전만 줘서 시행예정 개정을 STALE 로 오판하므로 쓰지 않는다.
- **⚠️ IP 등록 필요**: 데이터 수집과 달리 이건 OpenAPI 직접호출이라 **호출 PC 공인 IP 가 open.law.go.kr 에 등록**돼야 한다(동적 IP면 변경 시 재등록). lawService 본문은 `MST=` 파라미터(`ID=`는 법령ID 기대 — 혼동주의). 함정 상세: 메모리 `reference_moleg_openapi_gotchas`.
- **1차 실측(2026-06-29)**: 22/22 SYNC — 상류 지연 0건. 단 1회 스냅샷이라 *공포→legalize-kr 반영 며칠* cadence 측정은 시계열 반복 필요. 배경: 메모리 `project_radsafety_laws_freshness_lag`.
- **OpenAPI 요청 파라미터·회신 필드 레퍼런스** → [`docs/law-go-kr-openapi.md`](docs/law-go-kr-openapi.md). `query`(법령명 전용) vs 전용 파라미터(`ancYd`·`org`·`efYd`…) 구분, 12 회신필드, 현행연혁코드 3값, 일일 개정감지 함의. 라이브 검증(✓)/미검증(○) 표기.

---

## 작업 규칙

- 데이터(`data/`) 직접 손편집 금지 — CI 가 덮어쓴다. 추적 법령을 바꾸려면 `watchlist.toml`, 수집 로직을 바꾸려면 *스크립트* 를 고친다.
- 커밋 시 변경 파일만 명시적으로 `git add <path>` (vault `/git-routine` 안전규칙과 동일 — `git add .`/`-A` 금지).
- 수집 스크립트 수정 후엔 로컬에서 한 번 돌려 산출 diff 를 확인하고 커밋.
- 법령 텍스트 = 공공저작물(자유 이용). 가공 구조·스크립트 = MIT. ⚠️ 가공본이므로 법적 판단이 걸린 정확한 조문은 [law.go.kr](https://www.law.go.kr) 원본 대조 필요.

---

## 이 repo 를 넘어서는 작업은 vault 로

RAG 챗봇 설계·기술 스택·평가셋·radsafety-pwa 연동 등 **기획/설계는 이 repo 가 아니라 vault 허브**(`2026-06_RadSafety-lawbot`)가 정본이다. 이 repo 는 그 설계가 소비할 데이터 레이어를 *생산·유지*하는 경계까지만 책임진다.
