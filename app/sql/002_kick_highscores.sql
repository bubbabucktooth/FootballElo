-- Run after 001_shared_statistics.sql. Scores are derived from persisted matches.
begin;
create table if not exists public.btm_kick_rounds (
    id uuid primary key,
    version text not null,
    team text not null,
    length integer not null check (length in (10,20,30)),
    score bigint not null,
    completed_at timestamptz not null default now()
);
create table if not exists public.btm_kick_round_matches (
    match_id uuid primary key references public.btm_matches(id),
    round_id uuid not null references public.btm_kick_rounds(id)
);
create index if not exists btm_kick_records on public.btm_kick_rounds(version,length,score desc,completed_at,id);
alter table public.btm_kick_rounds enable row level security;
alter table public.btm_kick_round_matches enable row level security;
revoke all on public.btm_kick_rounds,public.btm_kick_round_matches from public,anon,authenticated;
grant select,insert on public.btm_kick_rounds,public.btm_kick_round_matches to service_role;

create or replace function public.btm_save_round(payload jsonb)
returns jsonb language plpgsql security invoker set search_path=public,pg_temp as $$
declare
    rid uuid := (payload->>'id')::uuid;
    chosen text := payload->>'team';
    model text := payload->>'version';
    n integer := (payload->>'length')::integer;
    total bigint;
    valid integer;
    ids uuid[];
    previous btm_kick_rounds%rowtype;
begin
    if n not in (10,20,30) or n is null or chosen is null or model is null then
        raise exception 'Invalid round';
    end if;
    select array_agg(value::uuid) into ids from jsonb_array_elements_text(payload->'match_ids');
    if cardinality(ids) is distinct from n or (select count(distinct x) from unnest(ids) x) <> n then
        raise exception 'Invalid match identities';
    end if;
    -- Serialize retries with the same round ID, including ambiguous lost acknowledgements.
    perform pg_advisory_xact_lock(hashtextextended(rid::text,0));
    select * into previous from btm_kick_rounds where id=rid;
    if found then
        if previous.team <> chosen or previous.version <> model or previous.length <> n
           or (select array_agg(match_id order by match_id) from btm_kick_round_matches where round_id=rid)
              is distinct from (select array_agg(x order by x) from unnest(ids) x) then
            raise exception 'Round identity mismatch';
        end if;
        return jsonb_build_object('ok',true);
    end if;
    with own_matches as (
        select *,case when home=chosen then p_h else p_a end as pw,
            (home=chosen and outcome='H') or (away=chosen and outcome='A') as won
        from btm_matches where id=any(ids) and mode='kick' and version=model
            and chosen in (home,away)
            and case when home=chosen then p_h else p_a end > 0
    )
    select count(*),sum(case when outcome='D' then 0
                            when won then round(100.0/nullif(pw,0))::bigint
                            else -round(100.0*pw)::bigint end)
    into valid,total from own_matches;
    if valid <> n or total is null then raise exception 'Incomplete round'; end if;
    insert into btm_kick_rounds(id,version,team,length,score) values(rid,model,chosen,n,total);
    -- A persisted match may only belong to one round; conflicts roll back the transaction.
    insert into btm_kick_round_matches(match_id,round_id) select x,rid from unnest(ids) x;
    return jsonb_build_object('ok',true);
end;
$$;

create or replace function public.btm_highscores(p_version text)
returns jsonb language sql stable security invoker set search_path=public,pg_temp as $$
select coalesce(jsonb_agg(r order by length),'[]'::jsonb) from (
    select distinct on (length) length,score,team,completed_at
    from btm_kick_rounds where version=p_version
    order by length,score desc,completed_at,id
) r;
$$;
revoke all on function public.btm_save_round(jsonb) from public,anon,authenticated;
revoke all on function public.btm_highscores(text) from public,anon,authenticated;
grant execute on function public.btm_save_round(jsonb) to service_role;
grant execute on function public.btm_highscores(text) to service_role;
commit;
