# radsafety-laws

RadSafety.kr 용 **방사선·의료 관련 대한민국 법령 데이터**.
[legalize-kr](https://github.com/legalize-kr/legalize-kr) 에서 필요한 법령만 raw fetch 하여 vendoring 하고, GitHub Actions 로 주간 자동 갱신한다.

`radsafety-web`(앱) ↔ **`radsafety-laws`(데이터)** 의 데이터 레이어.

## 구조

```
data/
  laws/          # [자동] legalize-kr raw fetch — 법률·시행령·시행규칙·부령 (주간 갱신)
  attachments/   # [수동] 법령 별표·별지·서식, 고시·예규, 실무 가이드 PDF
  commentary/    # [수동] 법령 해설(qmd) — RadiationSafetyNM/website 이관
scripts/update_laws.sh            # laws 갱신 스크립트 (attachments·commentary 는 안 건드림)
.github/workflows/update-laws.yml # 주간 cron 자동 갱신 + 수동 실행
```

- **`laws/`** = 자동 갱신(legalize-kr). CI 가 매주 덮어쓴다.
- **`attachments/`·`commentary/`** = 수동 자료. CI 무관·보존. legalize-kr 에 없는 **별표·고시·예규·가이드·해설**이 여기 있다(출처: `RadiationSafetyNM/website`).

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
