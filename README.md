# radsafety-laws

RadSafety.kr 용 **방사선·의료 관련 대한민국 법령 데이터**.
[legalize-kr](https://github.com/legalize-kr/legalize-kr) 에서 필요한 법령만 raw fetch 하여 vendoring 하고, GitHub Actions 로 주간 자동 갱신한다.

`radsafety-web`(앱) ↔ **`radsafety-laws`(데이터)** 의 데이터 레이어.

## 구조

```
data/
  laws/          # [자동·CI] legalize-kr — 법률·시행령·시행규칙·부령
  admin-rules/   # [자동·CI] admrule-kr — 의료 방사선안전 삼원화 고시·예규(원안위·질병청)
  attachments/   # [자동·CI] 법령·고시 frontmatter 의 방사선 별표·서식 PDF(law.go.kr flDownload)
scripts/update_laws.sh            # laws 갱신 (legalize-kr raw fetch)
scripts/update_admin_rules.sh     # 고시(admrule-kr) + 별표(flDownload) 갱신 — 모두 IP 무관
scripts/_collect_admrul.py        # 고시 수집 (admrule-kr GitHub raw)
scripts/_collect_attachments.py   # 별표 PDF 수집 (frontmatter 링크 → 공개 flDownload)
scripts/_collect_admrul_openapi.py# (폴백) 구 법제처 OpenAPI 판 — OC+고정IP 필요
.github/workflows/update-laws.yml # 법령·고시·별표 주간 cron 자동 갱신 (ubuntu-latest, IP 무관)
```

- **`laws/`** = 자동 갱신(legalize-kr). CI 가 매주 덮어쓴다.
- **`admin-rules/`** = **admrule-kr**(legalize-kr 자매 미러)의 의료 방사선 행정규칙(원안위·질병청 삼원화). 같은 markdown+frontmatter+git 모델이라 raw fetch — **IP 무관, CI 자동**. (구 설계는 법제처 OpenAPI 직접 호출이라 고정 IP·Oracle VM 이 필요했으나 admrule-kr 피벗으로 해소.)
- **`attachments/`** = 법령·고시 frontmatter 의 별표 링크 중 **방사선 관련만** 공개 flDownload 로 자동 수집(IP 무관). **전 데이터가 legalize-kr 파이프라인 산출** — 수동 자료(외부 PDF·해설)는 포함하지 않는다(재현성 보장).

## 포함 법령 (9개 법령군)

| 분류 | 법령 |
|---|---|
| 방사선·핵의학 | 원자력안전법 · 방사선및방사성동위원소이용진흥법 · 생활주변방사선안전관리법 |
| 진단·장비 | 진단용방사선발생장치의안전관리에관한규칙 · 특수의료장비의설치및운영에관한규칙 |
| 의료 일반 | 의료법 · 의료기기법 · 의료기사등에관한법률 |
| 방사성의약품 | 약사법 |

각 법령은 법률 + 시행령 + 시행규칙(또는 부령) 세트.

## 수동 갱신

```bash
bash scripts/update_laws.sh data/laws
```

## 데이터 출처·갱신 모델

- 1차 출처: **국가법령정보센터(law.go.kr) OpenAPI** → legalize-kr 가공(markdown) → 본 repo raw fetch.
- 갱신: 매주 자동(`update-laws.yml`). legalize-kr 이 거의 매일 개정을 추적·커밋한다.
- **raw fetch 라 legalize-kr 의 force-push / history rewrite 에 영향받지 않는다**(항상 main 최신 파일).

## 라이선스·면책

- 법령 텍스트 = **공공저작물**(자유 이용). 본 repo 의 가공 구조·스크립트 = MIT.
- ⚠️ 가공본이므로 **법적 판단이 걸린 정확한 조문·최신 개정은 [law.go.kr](https://www.law.go.kr) 원본 대조**가 필요하다.
