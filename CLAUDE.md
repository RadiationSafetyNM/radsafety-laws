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

### 예외 — 수동 HWPX override (2026-07-01, 경계된 6건)

law.go.kr 이 일부 별표를 **손실 유발 바이너리 `.hwp`로만** 제공하는데, 이 6건은 오픈소스 변환기(H2Orestart)가 표를 통째로 버린다(원본엔 있음 — PDF·수동확인으로 검증). 유일 해결이 **한컴 한글(정품 엔진)로 `.hwp`→`.hwpx` 수동 변환** 후 그 `.hwpx`를 커밋하는 것. `.hwpx`는 OWPML XML 이라 파서가 무손실 복원한다. 이 `.hwpx`는 공개 소스에서 스크립트로 재현 불가 → **재현성 계약의 문서화된 최소 예외**다. `.hwp`(재현 가능)는 그대로 두고 `.hwpx`를 나란히 커밋(파서가 hwpx 우선). 6건:

- 방사성핵종별 자체처분 허용농도 · 원자로조종면허 신체검사 합격기준 · 원자로조종면허 신체검사의 방법ㆍ판정기준 · 정도관리항목 · 특수의료장비 설치인정기준 · 품질관리검사기관의 시설 및 검사장비 기준(⚠ PDF 없어 hwpx 가 유일 복구 경로).

> 신규 별표가 같은 증상(파싱 손실 플래그 `pdf_fallback`/`char_diverge`/`short_no_pdf`/`math_loss`)을 보이면 동일하게 처리: 한글로 hwpx 변환 → 커밋 → 이 목록에 추가.

> ⚠️ **일괄 변환은 하지 않는다** (2026-07-31 실측 판단). hwp 라서 깨지는 게 아니다 — 84개 전량 재파싱 결과 손실 플래그 0, 선량한도(시행령 별표1) 같은 핵심 표도 hwp 원본에서 병합셀까지 온전하다. hwp 는 *깨질 때 전멸*하지만 그 자리는 감지기가 지목한다. **플래그가 뜬 건만** 변환하는 게 이 §의 운영 방식이고, 남은 59개 hwp 를 예방적으로 변환하면 노동만 크고 **낡은 수동 hwpx 가 개정된 새 hwp 를 이기는**(파서가 hwpx 우선) 더 나쁜 실패를 부른다.

### 예외 2 — 교정 오버레이 (2026-07-31)

수식 개체 소실(`math_loss`)처럼 **원본이 hwpx 여도 못 고치거나 hwpx 가 없는** 자리를 위한 좁은 통로. `data/attachments-corrections.json` 에 `find`/`replace`/`expect` 규칙을 두면 파서가 본문 추출 직후·손실감지 직전에 적용한다.

- 파싱본을 직접 손대지 않고 **재파싱마다 재적용**되므로 산출물은 여전히 재현 가능(규칙이 repo 에 있다).
- **staleness 가드**: 실제 발생 횟수가 `expect` 와 다르면 **하나도 적용 않고** `correction_stale` 을 띄운다. 상류가 개정돼 문장이 바뀌었는데 낡은 교정이 조용히 덧씌워지는 것을 막는다. 미적용이면 원문이 그대로 남아 `math_loss` 가 대신 울린다 — 침묵하는 경로가 없다.
- 규칙마다 `reason` 에 **검산 근거 필수**. 근거 없는 교정은 자산이 아니라 오염원이다.

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
                 #   frontmatter `parse_note:` = 파싱 품질 플래그(math_loss·corrected·char_diverge…)
  attachments-corrections.json   # [수동·근거필수] 교정 오버레이 규칙(§예외 2). 파서가 재파싱마다 적용
  attachments-forms-registry.md  # [자동·CI] 서식·별지 메타 카탈로그(빈 양식 — 본문 파싱 ✗, 제목·근거조·링크만)
  chunks/law_chunks.jsonl  # [자동·CI] RAG 청크(content+metadata). 임베딩 전 단계 — pwa 가 소비
                 #   조문(law·admin_rule) + **별표(attachment, 2026-07-31~)**. 별표는 서술형=통짜,
                 #   데이터 테이블형은 표 행 패킹(헤더 반복)으로 MAXCHARS 이하 분할
                 #   ⚠️ 부칙은 청크에 넣지 않는다(2026-08-04~) — §청킹 규약 참조
sql/
  001_lawbot_chunks.sql    # [수동 1회] Supabase 검색 테이블 + lawbot_match RPC. pgvector 인덱스
                 #   한계(vector 2,000차원) 때문에 MRL 절단 1024. Supabase SQL Editor 에서 실행
scripts/
  _watchlist.py              # watchlist.toml 로더(tomllib·stdlib) — fetch·audit 양쪽에 공급
  update_laws.sh             # laws 갱신 (legalize-kr raw fetch) — 목록은 _watchlist.py 에서 읽음(하드코딩 제거)
  update_admin_rules.sh      # 고시(admrule-kr) + 별표(flDownload) 수집 + 별표 md 파싱 — 수집부는 IP 무관
  _collect_admrul.py         # 고시 수집 (admrule-kr GitHub raw)
  _collect_attachments.py    # 별표 원본(HWP/HWPX)+PDF 수집 (frontmatter 파일링크·PDF링크 → 공개 flDownload)
  _parse_attachments.py      # 별표 원본 → 구조보존 markdown. hwpx=OWPML XML 직접 파싱(LibreOffice 우회),
                             #   hwp=soffice(+H2Orestart)→docx→pandoc. 도구 없으면 자동 skip(CI 안전).
                             #   손실감지 3층(원본 PDF 대비): 길이 대량손실→PDF폴백(pdf_fallback),
                             #   문자다중집합 divergence(순서·분절 무관, 주 감지)→char_diverge, 숫자집합→num_diverge(가중 오버레이),
                             #   PDF무+빈약→short_no_pdf. diverge 는 검토 플래그(자동교체 안 함). 순서민감 diff·어절집합은 오탐이라 배제.
  _build_forms_registry.py   # 서식·별지 메타 레지스트리 생성(순수 python, 수집기와 동일 게이트 → disk 1:1)
  _build_chunks.py           # RAG 청크 생성(딥리서치 계층청킹 — 조 기본·긴 조는 항→호 분할(문자수 분할 금지), 메타 6필드, 조↔별표·서식 링크)
                             #   + 별표 청크(2026-07-31): 기준·수치가 사는 곳이라 링크만으론 검색이 조문에서 멈춘다.
                             #   ⚠️고시 본문 형식 2종(헤딩/라인시작) 폴백 + 조문 0개 문서 경고(stale 회귀 방지)
                             #   ⚠️부칙 경계에서 마지막 조를 끊음 + chunk_id 중복 시 산출 없이 실패(2026-08-04)
  _match.py                  # 기대출처 매처(평가 하네스 ↔ 서빙 검증 공용 — 같은 잣대여야 비교가 성립)
  _embed.py                  # 임베딩 어댑터 — 백엔드(gateway/ollama)·1024 절단·재정규화·질의 프리픽스를 한 곳에 고정
  _embed_upsert.py           # 인입 배치 — 청크 → 임베딩 → Supabase lawbot_chunks upsert (변경분만·dry-run)
  _lawbot_verify.py          # 대조 검증 — 서빙 RPC 가 로컬 하네스와 같은 recall 을 내는지(인입 직후 필수)
  _collect_admrul_openapi.py # ⚠️ 폴백 전용 — 구 법제처 OpenAPI 판(OC+고정IP 필요). 평시 미사용.
  _freshness_audit.py        # 신선도 감사 — law.go.kr OpenAPI(권위) ↔ 우리 frontmatter MST 비교. ⚠️OpenAPI 직접호출이라 IP 등록 필요
  _amend_selfdiff.py         # 자체 개정 diff — 조문 단위 비교(부칙 분리). API·IP 불필요, git 안에서 완결
  _amend_moleg.py            # 법제처 비교 API — 신구법비교(oldAndNew)·3단비교(thdCmp, ⚠️knd 필수). IP 등록 필요
  _amend_audit.py            # 교차검증 — 자체 diff ↔ 법제처 신구법비교 대조(AGREE/SELF_ONLY/API_ONLY) + 3단비교 파급
  _amend_report.py           # 주간 갱신분 개정 요약(MST 변경분만 → 조문 단위). CI 알림용·API 불필요
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

- **자동**: `update-laws.yml` 이 매주 월요일 03:00 KST(`cron: "0 18 * * 0"`)에 laws→고시→별표 순으로 수집 → **개정 감지** → **파생물 재생성(청크·서식 레지스트리)** → 변경 있으면 `github-actions[bot]` 이 커밋·푸시. 변경 없으면 커밋 생략.
  - ⚠️ **파생물 재생성은 2026-07-31 에야 CI 에 붙었다.** 그전에는 로컬 수동이라 **7월 내내 청크가 6/30 상태로 방치**됐다(개정된 조문이 `data/laws` 에는 반영되고 청크에는 안 들어감). 서식 레지스트리도 같이 stale 이었다. 순수 파이썬이라 CI 에서 그대로 돈다.
  - ⚠️ **별표 파싱본(`attachments-parsed/`)만은 CI 에서 못 만든다**(LibreOffice 필요) → 새 별표의 *청크* 는 도구 갖춘 로컬에서 파싱·커밋해야 들어온다. 조문 청크는 완전 자동.
    - 그래서 `_build_chunks.py` 가 **별표 원본 ↔ 파싱본 짝 검사**를 하고 빠진 게 있으면 `⛔` 를 찍는다(2026-07-31). CI 가 그 `⛔` 를 개정 이슈에 얹으므로, 새 별표가 파싱 안 된 채 청크에서 누락되는 상황이 조용히 지나가지 않는다 — 로컬 파싱이 필요하다는 알림이 온다.
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

## 청킹 규약 — 부칙 제외·chunk_id 고유성 (2026-08-04 신설)

### 부칙은 청크가 아니다

조 분할(`_slice`)은 **마지막 조를 부칙 앞에서 끊는다.** 부칙은 시행일·경과조치·타법개정문이라 조문 검색의 대상이 아니다.

종전에는 마지막 조의 끝을 `len(body)` 로 잡아 **부칙 전체를 마지막 조가 삼켰다.** 그 텍스트를 `split_long` 이 항(②③…) 단위로 쪼개는데 부칙마다 ②③④ 가 다시 나오니 같은 `{law_id}#{art}_{sub}` 가 최대 27번 만들어졌다. 손상은 두 겹이었다:

- **`chunk_id` 충돌 569행** — Supabase `lawbot_chunks` 는 chunk_id 가 primary key 라 upsert 가 조용히 덮어쓴다. 인입 배치를 처음 돌리는 순간 569행이 에러 없이 사라질 뻔했다.
- **라벨 오염** — 「약사법」 제98조(과태료) ② 인데 본문이 *담배사업법 개정문* 인 청크가 코퍼스에 섞였다. 2026-08-01~03 의 recall 측정에도 이 노이즈가 들어 있었다.

수리 후 청크 3,234 → **2,524**(law 2,566→1,856). 줄어든 710개는 전부 부칙 파생이고 본문 손실은 없다 — 사라진 키를 전수 대조해 ① 부칙 개정문 ② 부칙이 빠져 조가 짧아지며 항 분할이 통짜로 합쳐진 것(내용 보존·ID 만 변경) ③ 원문이 "삭제 <…>" 인 조(삭제 조 제외 규칙이 정상 적용) 셋으로 설명된다.

> 부칙 분리 자체는 새 개념이 아니다 — `_amend_selfdiff.py` 는 처음부터 부칙을 갈라내고 조문만 비교했다. **청킹만 그 규약을 안 따르고 있었다.**

**A/B 실측 (2026-08-04, qwen3-embedding:8b · 4096차원 · 같은 평가셋 36문항)** — 같은 하네스를 수리 전/후 코퍼스로 각각 돌려 비교했다:

| 지표 (vector) | 수리 전 (3,234) | 수리 후 (2,524) |
|---|---|---|
| 출처 recall verified 29문항 @3 | 26/29 (90%) | **27/29 (93%)** |
| 〃 @5 | 27/29 (93%) | **28/29 (97%)** |
| 전체 코퍼스내 35문항 @3 | 30/35 (86%) | **31/35 (89%)** |
| 〃 @5 | 32/35 (91%) | **33/35 (94%)** |
| answerable strict @1·@3·@5 | 77% · 90% · 90% | 77% · 90% · 90% |

@3·@5 에서 각 +1문항, answerable 은 불변. **부칙 청크가 실제로 상위 순위를 잠식하고 있었다.** 코퍼스가 22% 작아졌으므로 임베딩 비용·저장도 함께 준다.

### chunk_id 고유성 가드

`_build_chunks.py` 는 중복 `chunk_id` 를 발견하면 **산출 파일을 쓰지 않고 종료한다**(exit 1). 깨진 청크 파일이 남으면 다음 단계가 그대로 먹기 때문이다. 이 가드가 잡은 별표 쪽 원인 2종:

- **전각 닫는 괄호 `］`(U+FF3D)** — HWP 원문 그대로다. 반각만 받던 `ATT_NO_RE` 가 번호 추출에 실패해 `att_no` 가 '별표' 로 뭉쳤고, 한 법령의 서로 다른 별표 3건이 같은 ID 로 충돌했다.
- **라벨 없는 평문 조각** — 표 사이·표 뒤 평문은 `subunit` 이 '' 이라 한 별표에서 여러 개 나오면 전부 같은 ID 가 된다. 두 번째부터 `_p2`·`_p3` 를 붙인다(첫 조각은 접미 없이 두어 기존 ID 보존).

## 임베딩 선택 (2026-08-01 실측)

**기본 = `qwen3-embedding:8b`** (구 `bge-m3`). 같은 코퍼스·같은 평가셋 32문항에서:

| 임베딩 | answerable @1 | @3 | @5 | source recall @5 |
|---|---|---|---|---|
| bge-m3 (구 기본) | 69% | 78% | 84% | 83% |
| qwen3-embedding:4b | 72% | 88% | 91% | 91% |
| **qwen3-embedding:8b** | **78%** | **91%** | **91%** | **91%** |

> 위 표는 **수리 전 코퍼스(3,234청크)** 기준이다. 부칙 제외 후(2,524청크) 같은 조건 재측정치는 answerable @1 77% · @3 90% · @5 90%(불변, 분모 31문항), **source recall @5 는 91%→94%(35문항 기준)** 로 올랐다 — §청킹 규약의 A/B 표 참조. 모델 간 비교의 상대 순위는 이 변화에 영향받지 않는다.

- 청킹·하이브리드 튜닝으로 얻은 것이 +1문항이었던 데 반해, 임베딩 교체는 **@3 에서 +4문항**을 한 번에 가져왔다. 검색 품질의 지배 변수는 검색 설계가 아니라 임베딩이었다.
- **하이브리드(BM25)는 강한 임베딩에서 오히려 해롭다.** bge-m3 에선 어휘 가중 0.15 가 @5 를 84%→88% 로 올렸지만, qwen3 에선 모든 가중치에서 벡터 단독보다 나쁘다. 어휘 융합은 *약한 임베딩의 보정재* 였다. → 운영 기본은 **벡터 단독**, BM25 는 도구로 남겨둔다(`_bm25.py`, 하네스가 매 실행 비교 출력).
- **질의 프리픽스 필수**: qwen3-embedding 계열은 질의에 `Instruct: …\nQuery: ` 프리픽스를 붙인다(문서는 그대로). 하네스와 `_rag_answer.py` 가 **동일 문구**를 쓴다 — 다르면 측정과 생성이 서로 다른 검색을 하게 된다.
- 4b(2.5GB) 는 8b(4.7GB) 에 @5 는 동률, @1·@3 만 뒤진다. VRAM 이 빠듯한 기기에선 4b 로 내려도 실사용 손실이 작다.

> **서빙 경로 확정 (2026-08-03, vault §결정 1.7)** — 같은 qwen3-embedding:8b 를 **Vercel AI Gateway**(업스트림 DeepInfra)로 부른다. 서버리스에서 8B 를 못 돌리는 문제가 이걸로 해소되고, Voyage/OpenAI 로 갈아탈 이유가 없어졌다(전량 임베딩 1회가 $0.01~0.06 수준이라 비용은 논외).
>
> ‼️ **로컬 ollama 는 평가 하네스 전용이다.** Q4_K_M 양자화라 호스팅과 벡터가 미세하게 다르다. 코퍼스(인입)와 질의(앱)를 **같은 백엔드·같은 차원**으로 임베딩해야 하며, 한쪽만 어긋나면 에러 없이 검색만 나빠진다. 그 규약을 `_embed.py` 한 곳에 모아 고정했다(백엔드·1024 절단·재정규화·질의 프리픽스·CAP). 인입 후에는 `_lawbot_verify.py` 로 이 표의 수치가 서빙에서 재현되는지 대조한다.

---

## 개정 내용 추출·교차검증 (2026-07-31 신설)

신선도 감사(위)가 **"개정이 났는가"**(MST 대조)에 답한다면, 여기는 **"무엇이·어디가 바뀌었는가"**에 답한다. MST 는 플래그일 뿐 내용을 말해주지 않기 때문이다.

**독립 관측 2개를 맞대어 서로를 검증한다.**

| | 무엇 | 네트워크 |
|---|---|---|
| **A 자체 diff** | `_amend_selfdiff.py` — 우리 미러 두 스냅샷의 조문 단위 차이 | 불필요 (git 안에서 완결) |
| **B 법제처** | `_amend_moleg.py oldandnew` — 발행처가 스스로 든 개정 조문 | OpenAPI (IP 등록) |

```bash
python3 scripts/_amend_selfdiff.py --git <old> <new> data/laws/<법령>.md   # A 단독(오프라인)
python3 scripts/_amend_audit.py data/laws/<법령>.md --auto                  # A↔B 대조
python3 scripts/_amend_audit.py --all --cache-dir out/moleg                 # 전량 감사
python3 scripts/_amend_audit.py <path> --auto --impact                      # + 3단비교 파급
```

**판정 3분기** — 각 출처가 서로 다른 방식으로 틀리기 때문에 의미가 있다:

- **AGREE** — 두 출처 일치. 알림에 그대로 실어도 되는 개정.
- **SELF_ONLY** — 우리만 잡음. 대개 **미러 재가공**(포맷·오타)이거나 **부칙**(법제처는 부칙을 조문으로 세지 않는 구조적 비대칭). 알림 강도를 낮춘다.
- **API_ONLY** — ⚠️ 법제처만 잡음. **우리 데이터가 뒤처졌거나 파싱이 놓친 것** — 신선도 사고의 조기 신호.

**왜 `git diff` 로는 안 되는가**: 파일 diff 는 "글자가 달라졌다"만 알려줘 미러 재가공과 진짜 개정을 구별하지 못한다. 실측(2026-07-19 원자력안전법 시행규칙) — **830줄 diff 중 실제 개정 조문은 1개**(제121조 건강진단)였고, 그 다음 주 07-26 커밋은 116줄이 바뀌었지만 MST 무변경 = 법은 그대로였다. 조문 단위로 쪼개야 이 구분이 선다.

**부칙 분리**: 부칙은 마지막 조 뒤에 붙어 있어 그냥 파싱하면 직전 조(제147조 등)의 변경으로 **오귀속**된다. `_amend_selfdiff.py` 가 부칙을 조문에서 떼어 별도 플래그로 낸다 — 이래야 부칙을 조문으로 세지 않는 법제처와 대조가 맞는다.

**3단비교(`thdCmp`) = 파급 분석**이지 개정 이력이 아니다. 법률↔시행령↔시행규칙 조문 대응표라, 개정 조를 찾은 뒤 "이게 바뀌면 어디가 영향받나"에 쓴다(예: 진단용방사선규칙 제13조 → 의료법 제37조). ⚠️ `knd` 필수(1 인용조문 / 2 위임조문) — 빠뜨리면 **HTTP 200 에 빈 본문**이 와서 권한 문제와 구별되지 않는다.

**CI 알림 배선 (2026-07-31)**: 주간 워크플로가 커밋 *전에* `_amend_report.py` 를 돌려 **버전키가 실제로 바뀐 파일만** 골라 조문 단위로 푼다. 감지 대상은 **네 층** — ① 법령 본문(`법령MST`) ② 행정규칙(`행정규칙일련번호` — 고시·훈령·예규는 법규명령이 아닌 별도 트랙이라 필드명부터 다르다) ③ **별표·서식 목록**(frontmatter 첨부에서 `flSeq` 를 뺀 `(구분,번호,가지번호,제목)` 비교 → 신설·삭제·**제목변경=개정**) ④ **별첨 원본 파일**(hwp/hwpx 해시 A/M/D — PDF 는 재생성 노이즈라 제외). 개정이 있으면 ① 커밋 제목이 무엇이 바뀌었는지 말하고(`chore: 원자력안전법 시행규칙 제121조(건강진단) 개정`) ② **GitHub 이슈**를 연다(`[법령개정] …`) — GitHub 알림이 메일·앱으로 도달한다. 별도 secret 불필요(`GITHUB_TOKEN`).
> 왜 이슈인가: 지금까지 개정은 커밋으로만 남아 **묻는 사람이 있어야 드러났다.** 이슈는 밀어내는(push) 채널이라 "알 수 있다"가 "알려준다"가 된다. CI 는 IP 등록이 안 돼 법제처 API 를 못 쓰므로 **자체 diff 만** 쓴다 — 권위 대조는 고정 IP PC 에서 `_amend_audit.py` 로 사후 교차검증하는 분담이다.

**개정 데이터 모델 조사(2026-07-31)** → [`docs/amendment-data-model.md`](docs/amendment-data-model.md). 개정 시 실제로 무엇이 달라지는지 실측하고 신호/노이즈를 갈랐다. **함정 2개가 결정적**: ① `flSeq`(별표 다운로드 링크)는 개정 때 **내용 무관하게 전량 재발급**되므로 변경 신호로 쓰면 전량 오탐 ② 별첨 M(수정) 21건이 **전부 PDF**(원본 hwp/hwpx 는 0건) — PDF 는 재생성만으로 바이트가 바뀌므로 **별표 판정은 원본 해시로만**. 그 밖에 제개정구분 6종(타법개정 71건은 알림 강도 하향 대상·전부개정은 조 번호 체계가 갈려 조 단위 비교 무효), 별표 가지번호(별표 1의2 — 안정적 키는 `(모법,구분,번호,가지번호)`), **별표는 법령 본문보다 늦게 도착**(7/9 개정의 신설 서식이 7/26 커밋에 A) 를 확인했다.

**1차 전량 실측(2026-07-31)**: 8건 감사 — **AGREE 17 · SELF_ONLY 0 · API_ONLY 0**. 상류 지연·파싱 누락 0.
> 그 과정에서 파서 버그 1건 수리: 회신 CDATA 안에 `<P>` 등 HTML 태그가 **문자열로** 들어와 `<P>제48조의2(…)` 가 조 시작으로 안 잡혀 **가지조문이 앞 조에 흡수**됐다(의료기기법 시행규칙에서 SELF_ONLY 3건 오탐). `strip_tags()` 로 해소 — 수정 전 AGREE 12/SELF_ONLY 5 → 수정 후 17/0.

---

## 작업 규칙

- 데이터(`data/`) 직접 손편집 금지 — CI 가 덮어쓴다. 추적 법령을 바꾸려면 `watchlist.toml`, 수집 로직을 바꾸려면 *스크립트* 를 고친다.
- 커밋 시 변경 파일만 명시적으로 `git add <path>` (vault `/git-routine` 안전규칙과 동일 — `git add .`/`-A` 금지).
- 수집 스크립트 수정 후엔 로컬에서 한 번 돌려 산출 diff 를 확인하고 커밋.
- 법령 텍스트 = 공공저작물(자유 이용). 가공 구조·스크립트 = MIT. ⚠️ 가공본이므로 법적 판단이 걸린 정확한 조문은 [law.go.kr](https://www.law.go.kr) 원본 대조 필요.

---

## 이 repo 를 넘어서는 작업은 vault 로

RAG 챗봇 설계·기술 스택·평가셋·radsafety-pwa 연동 등 **기획/설계는 이 repo 가 아니라 vault 허브**(`2026-06_RadSafety-lawbot`)가 정본이다. 이 repo 는 그 설계가 소비할 데이터 레이어를 *생산·유지*하는 경계까지만 책임진다.
