-- lawbot 검색 테이블 (Supabase / pgvector)
--
-- 적용 대상: radsafety-pwa 의 Supabase 프로젝트. Supabase 콘솔 SQL Editor 에 붙여 실행한다.
-- 이 파일이 radsafety-laws 에 있는 이유: 이 테이블을 **채우는 쪽**(인입 배치)이 이 저장소이기 때문.
--   경계 = 데이터·임베딩 인입: radsafety-laws / 챗 런타임·UI: radsafety-pwa (2026-06-27 결정 1)
--
-- ⚠️ 차원이 1024 인 이유 — pgvector 인덱스 한계다.
--   qwen3-embedding:8b 의 원본 차원은 **4096** 인데, pgvector 의 HNSW/IVFFlat 는
--   `vector` 타입에서 2,000 차원, `halfvec` 에서도 4,000 차원까지만 인덱싱한다.
--   그래서 Matryoshka(MRL) 절단으로 앞 1024 차원만 쓰고 재정규화한다.
--   실측(2026-08-03, 평가셋 31문항): answerable @5 가 4096·2048·1024·512 **전부 90% 로 동일**.
--   @1 만 문항 하나 수준에서 흔들린다(n=31 에서 노이즈). 즉 절단 비용이 측정되지 않았다.
--   ‼️ **코퍼스와 질의를 같은 차원으로 자르고 같은 방식으로 재정규화해야 한다.** 한쪽만
--   자르면 좌표계가 어긋나 검색이 조용히 망가진다.

create extension if not exists vector;

create table if not exists lawbot_chunks (
  chunk_id   text primary key,              -- 예: 35212#별표1_표1행153~183
  content    text        not null,           -- 생성 모델에 그대로 넘기는 본문
  metadata   jsonb       not null default '{}'::jsonb,
  embedding  vector(1024) not null,          -- MRL 절단 + L2 정규화
  updated_at timestamptz not null default now()
);

-- 코사인 거리 인덱스. 정규화된 벡터라 코사인·내적이 동치지만, 정규화가 깨진 행이
-- 섞여도 안전하도록 코사인을 쓴다.
create index if not exists lawbot_chunks_embedding_idx
  on lawbot_chunks using hnsw (embedding vector_cosine_ops);

-- 계열·문서종류로 좁혀 검색할 여지를 남긴다(관할 분기 라우팅이 필요해질 때).
create index if not exists lawbot_chunks_metadata_idx
  on lawbot_chunks using gin (metadata jsonb_path_ops);

-- ── RLS ────────────────────────────────────────────────────────────────────
-- 법령 본문은 공개 자료이지만, 앱은 로그인 사용자에게만 제공한다. 쓰기는 인입 배치
-- (service role)만 한다 — anon/authenticated 에는 쓰기 정책을 주지 않는다.
alter table lawbot_chunks enable row level security;

drop policy if exists lawbot_chunks_read on lawbot_chunks;
create policy lawbot_chunks_read on lawbot_chunks
  for select to authenticated using (true);

-- ── 검색 RPC ───────────────────────────────────────────────────────────────
-- 앱은 이 함수만 부른다. 임베딩 차원이 시그니처에 박혀 있어, 차원을 바꾸면 여기서
-- 먼저 실패한다 — 조용히 어긋나는 것보다 낫다.
create or replace function lawbot_match(
  query_embedding vector(1024),
  match_count     int default 5
)
returns table (chunk_id text, content text, metadata jsonb, similarity float)
language sql stable
set search_path = public
as $$
  select c.chunk_id, c.content, c.metadata,
         1 - (c.embedding <=> query_embedding) as similarity
    from lawbot_chunks c
   order by c.embedding <=> query_embedding
   limit greatest(1, least(match_count, 20));
$$;

revoke all on function lawbot_match(vector, int) from public;
grant execute on function lawbot_match(vector, int) to authenticated;
