-- lawbot 검색 테이블 (Supabase / pgvector)
--
-- 적용 대상: radsafety-pwa 의 Supabase 프로젝트. Supabase 콘솔 SQL Editor 에 붙여 실행한다.
-- 이 파일이 radsafety-laws 에 있는 이유: 이 테이블을 **채우는 쪽**(인입 배치)이 이 저장소이기 때문.
--   경계 = 데이터·임베딩 인입: radsafety-laws / 챗 런타임·UI: radsafety-pwa (2026-06-27 결정 1)
--
-- ⚠️ 차원이 1024 인 이유 — pgvector 인덱스 한계다.
--   (2026-09-21 결정 1.8 로 모델은 gemini-embedding-001·원본 3072 로 바뀌었다. 아래 논리는 같다.)
--   qwen3-embedding:8b 의 원본 차원은 **4096** 인데, pgvector 의 HNSW/IVFFlat 는
--   `vector` 타입에서 2,000 차원, `halfvec` 에서도 4,000 차원까지만 인덱싱한다.
--   그래서 Matryoshka(MRL) 절단으로 앞 1024 차원만 쓰고 재정규화한다.
--   실측(2026-08-03, 평가셋 31문항): answerable @5 가 4096·2048·1024·512 **전부 90% 로 동일**.
--   @1 만 문항 하나 수준에서 흔들린다(n=31 에서 노이즈). 즉 절단 비용이 측정되지 않았다.
--   ‼️ **코퍼스와 질의를 같은 차원으로 자르고 같은 방식으로 재정규화해야 한다.** 한쪽만
--   자르면 좌표계가 어긋나 검색이 조용히 망가진다.

create extension if not exists vector;

-- ── 행의 단위 = 조문의 한 판본 (결정 4, 2026-11-20) ─────────────────────────
-- 같은 제10조라도 개정 전과 후는 다른 행이다. 각 행이 **자기 시행일**을 갖는다.
--   effective_from  이 본문이 효력을 갖기 시작하는 날(시행일)
--   effective_to    효력이 끝나는 날(다음 판본의 시행일). null = 아직 끝이 정해지지 않음
-- 법령 단위가 아니라 조문 단위로 날짜를 두는 이유: 한 번의 개정 안에서도 조문마다
-- 시행일이 다르다(의료기기법 2026-10-20 개정 — 시행일 셋). 원천은
-- data/laws-effective/ 의 시행일 기준 스냅샷이고, 연속한 스냅샷을 조문 단위로 견줘 행을 만든다.
-- 바뀌지 않은 조문은 새 행을 만들지 않는다 — 기존 행이 그대로 이어진다.
--
-- 현행/시행예정의 구분은 **열이 아니라 조회 시점의 날짜**로 한다. 그래서 시행일 0시에
-- 돌아야 하는 배치가 없다: 날짜가 바뀌는 순간 검색 결과가 바뀐다.
create table if not exists lawbot_chunks (
  chunk_id       text        not null,       -- 조문 식별자(판본 무관). 예: 011435#제45조, 35212#별표1_표1행153~183
  effective_from date        not null,       -- 시행일
  effective_to   date,                       -- 다음 판본 시행일(배타). null = 열림
  content        text        not null,       -- 생성 모델에 그대로 넘기는 본문
  metadata       jsonb       not null default '{}'::jsonb,   -- law_mst·공포일·제개정구분 등
  embedding      vector(1024) not null,      -- MRL 절단 + L2 정규화
  updated_at     timestamptz not null default now(),
  primary key (chunk_id, effective_from),
  check (effective_to is null or effective_to > effective_from)
);

-- 코사인 거리 인덱스. 정규화된 벡터라 코사인·내적이 동치지만, 정규화가 깨진 행이
-- 섞여도 안전하도록 코사인을 쓴다.
create index if not exists lawbot_chunks_embedding_idx
  on lawbot_chunks using hnsw (embedding vector_cosine_ops);

-- 계열·문서종류로 좁혀 검색할 여지를 남긴다(관할 분기 라우팅이 필요해질 때).
create index if not exists lawbot_chunks_metadata_idx
  on lawbot_chunks using gin (metadata jsonb_path_ops);

-- 시행예정 조회(같은 조문의 미래 판본 찾기)용.
create index if not exists lawbot_chunks_effective_idx
  on lawbot_chunks (chunk_id, effective_from);

-- ── RLS ────────────────────────────────────────────────────────────────────
-- 법령 본문은 공개 자료이지만, 앱은 로그인 사용자에게만 제공한다. 쓰기는 인입 배치
-- (service role)만 한다 — anon/authenticated 에는 쓰기 정책을 주지 않는다.
alter table lawbot_chunks enable row level security;

drop policy if exists lawbot_chunks_read on lawbot_chunks;
create policy lawbot_chunks_read on lawbot_chunks
  for select to authenticated using (true);

-- ── 테이블 권한 ────────────────────────────────────────────────────────────
-- Supabase 는 2026-10-30 부터 public 의 새 테이블에 Data API 권한을 자동으로 주지 않는다.
-- 명시하지 않으면 인입 배치(service_role)도 `permission denied` 로 막힌다.
-- anon 에는 주지 않는다(로그인 사용자 전용). authenticated 는 읽기만.
grant select on lawbot_chunks to authenticated;
grant select, insert, update, delete on lawbot_chunks to service_role;

-- ── 검색 RPC ───────────────────────────────────────────────────────────────
-- 앱은 이 함수만 부른다. 임베딩 차원이 시그니처에 박혀 있어, 차원을 바꾸면 여기서
-- 먼저 실패한다 — 조용히 어긋나는 것보다 낫다.
-- 한국 날짜 기준 '오늘'. 서버 시간대(UTC)를 그대로 쓰면 시행일 0시~9시 사이에 구판이 나온다.
create or replace function lawbot_today() returns date
language sql stable as $$ select (now() at time zone 'Asia/Seoul')::date $$;

-- as_of 시점에 시행 중인 판본만 검색한다. 기본값 = 한국 날짜 오늘.
create or replace function lawbot_match(
  query_embedding vector(1024),
  match_count     int  default 5,
  as_of           date default null
)
returns table (chunk_id text, content text, metadata jsonb,
               effective_from date, effective_to date, similarity float)
language sql stable
set search_path = public
as $$
  select c.chunk_id, c.content, c.metadata, c.effective_from, c.effective_to,
         1 - (c.embedding <=> query_embedding) as similarity
    from lawbot_chunks c
   where c.effective_from <= coalesce(as_of, lawbot_today())
     and (c.effective_to is null or c.effective_to > coalesce(as_of, lawbot_today()))
   order by c.embedding <=> query_embedding
   limit greatest(1, least(match_count, 20));
$$;

-- "○월 ○일부터 이렇게 바뀝니다" 용 — 검색된 조문들의 **미래 판본**을 돌려준다.
-- chunk_id 가 아니라 (법령, 조)로 찾는다: 조문이 길어져 항 단위로 다시 쪼개지면
-- chunk_id 가 바뀌어(제10조 → 제10조_①) 같은 ID 로는 미래 판본이 안 잡힌다.
-- 임베딩은 필요 없다. 미래 판본 없이 effective_to 만 있는 현행 행 = 그날 삭제되는 조문
-- (lawbot_match 가 effective_to 를 함께 돌려주므로 앱이 알 수 있다).
create or replace function lawbot_upcoming(
  chunk_ids text[],
  as_of     date default null
)
returns table (chunk_id text, content text, metadata jsonb, effective_from date)
language sql stable
set search_path = public
as $$
  select distinct c.chunk_id, c.content, c.metadata, c.effective_from
    from lawbot_chunks c
    join lawbot_chunks h
      on h.chunk_id = any(chunk_ids)
     and c.metadata->>'law_id'  = h.metadata->>'law_id'
     and c.metadata->>'article' = h.metadata->>'article'
     and c.metadata->>'document_type' = h.metadata->>'document_type'
   where c.effective_from > coalesce(as_of, lawbot_today())
   order by c.chunk_id, c.effective_from;
$$;

revoke all on function lawbot_match(vector, int, date) from public;
grant execute on function lawbot_match(vector, int, date) to authenticated;
revoke all on function lawbot_upcoming(text[], date) from public;
grant execute on function lawbot_upcoming(text[], date) to authenticated;
grant execute on function lawbot_match(vector, int, date) to service_role;
grant execute on function lawbot_upcoming(text[], date) to service_role;
grant execute on function lawbot_today() to authenticated, service_role;
