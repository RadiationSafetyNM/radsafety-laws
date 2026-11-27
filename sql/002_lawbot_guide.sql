-- lawbot 지침 검색 테이블 (2026-11-27) — KINS 「방사선분야 정기검사 수검 가이드」 등 **구속력 없는 실무 안내**
--
-- 법령 테이블(lawbot_chunks)·검색 함수(lawbot_match)는 **건드리지 않는다.**
--   · 법령 답이 지침 편입과 무관하게 그대로임이 구조로 보장된다(같은 표에 딱지로 섞으면 검색 함수를 바꿔야 한다).
--   · 같은 표에 두면 HNSW 인덱스가 "가까운 것 먼저 찾고 나중에 거르는" 탓에, 2,600행 중 90행뿐인 지침은
--     상위 후보에 못 들어 결과가 0건이 될 수 있다. 지침은 행이 적어 인덱스 없이 전부 견준다(정확 탐색).
-- 행 모양은 lawbot_chunks 와 같다(chunk_id·content·metadata·embedding). 판본은 발간 연도로 구분(metadata.edition).
-- 적용: Supabase 콘솔 SQL Editor 에 붙여 1회 실행. 되돌리기 = drop function lawbot_guide_match; drop table lawbot_guide_chunks;

create table if not exists lawbot_guide_chunks (
  chunk_id   text         primary key,          -- 예: kins2022#Part1…/9방사선작업종사자및수시출입
  content    text         not null,
  metadata   jsonb        not null default '{}'::jsonb,   -- document_type=guide · cites(인용 조문) · printed_pages 등
  embedding  vector(1024) not null,             -- lawbot_chunks 와 같은 모델·절단·정규화
  updated_at timestamptz  not null default now()
);

alter table lawbot_guide_chunks enable row level security;
drop policy if exists lawbot_guide_chunks_read on lawbot_guide_chunks;
create policy lawbot_guide_chunks_read on lawbot_guide_chunks
  for select to authenticated using (true);
grant select on lawbot_guide_chunks to authenticated;
grant select, insert, update, delete on lawbot_guide_chunks to service_role;

create or replace function lawbot_guide_match(
  query_embedding vector(1024),
  match_count     int default 3
)
returns table (chunk_id text, content text, metadata jsonb, similarity float)
language sql stable
set search_path = public
as $$
  select c.chunk_id, c.content, c.metadata,
         1 - (c.embedding <=> query_embedding) as similarity
    from lawbot_guide_chunks c
   order by c.embedding <=> query_embedding
   limit greatest(1, least(match_count, 10));
$$;

revoke all on function lawbot_guide_match(vector, int) from public;
grant execute on function lawbot_guide_match(vector, int) to authenticated, service_role;
