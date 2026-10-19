-- Run once in the SQL Editor of your Supabase Free project.
-- Only the server's secret/service_role key may read or write these objects.
begin;

create table if not exists public.btm_seasons (
    id uuid primary key,
    version text not null,
    selected_team text not null,
    completed boolean not null default false,
    created_at timestamptz not null default now()
);
create table if not exists public.btm_matches (
    id uuid primary key,
    season_id uuid references public.btm_seasons(id),
    mode text not null check (mode in ('kick','season')),
    version text not null,
    home text not null,
    away text not null check (away <> home),
    outcome text not null check (outcome in ('H','D','A')),
    p_h double precision not null check (p_h between 0 and 1),
    p_d double precision not null check (p_d between 0 and 1),
    p_a double precision not null check (p_a between 0 and 1),
    hg integer check (hg >= 0),
    ag integer check (ag >= 0),
    match_date date,
    occurred_at timestamptz not null default now(),
    check (abs(p_h+p_d+p_a-1) < 0.000001),
    check ((mode='kick' and season_id is null and hg is null and ag is null) or
           (mode='season' and season_id is not null and hg is not null and ag is not null and match_date is not null)),
    check (hg is null or outcome=case when hg>ag then 'H' when hg=ag then 'D' else 'A' end),
    unique (season_id,home,away)
);
create table if not exists public.btm_finishes (
    season_id uuid not null references public.btm_seasons(id),
    team text not null,
    position integer not null check (position between 1 and 20),
    points integer not null check (points between 0 and 114),
    primary key (season_id,team),
    unique (season_id,position)
);
create index if not exists btm_matches_version_time on public.btm_matches(version,occurred_at desc,id);
create index if not exists btm_seasons_version on public.btm_seasons(version);
alter table public.btm_seasons enable row level security;
alter table public.btm_matches enable row level security;
alter table public.btm_finishes enable row level security;
revoke all on public.btm_seasons, public.btm_matches, public.btm_finishes from public, anon, authenticated;
grant select,insert,update on public.btm_seasons, public.btm_matches, public.btm_finishes to service_role;

create or replace function public.btm_ingest(payload jsonb)
returns jsonb language plpgsql security invoker set search_path = public, pg_temp as $$
declare
    s jsonb := payload->'season';
    m jsonb;
    sid uuid;
    existing_version text;
    existing_team text;
    valid_teams integer;
begin
    if jsonb_array_length(coalesce(payload->'matches','[]'::jsonb)) > 380 then
        raise exception 'Batch exceeds 380 matches';
    end if;
    if s is not null then
        sid := (s->>'id')::uuid;
        insert into btm_seasons(id,version,selected_team)
        values(sid,s->>'version',s->>'selected_team') on conflict(id) do nothing;
        select version,selected_team into existing_version,existing_team from btm_seasons where id=sid for update;
        if existing_version <> s->>'version' or existing_team <> s->>'selected_team' then
            raise exception 'Season identity mismatch';
        end if;
    end if;
    for m in select value from jsonb_array_elements(coalesce(payload->'matches','[]'::jsonb)) loop
        if (sid is null and m->>'mode' <> 'kick') or
           (sid is not null and (m->>'mode' <> 'season' or m->>'version' <> existing_version)) then
            raise exception 'Invalid event mode or version';
        end if;
        insert into btm_matches(id,season_id,mode,version,home,away,outcome,p_h,p_d,p_a,hg,ag,match_date,occurred_at)
        values((m->>'id')::uuid,sid,m->>'mode',m->>'version',m->>'home',m->>'away',m->>'outcome',
               (m->'probabilities'->>'H')::double precision,(m->'probabilities'->>'D')::double precision,
               (m->'probabilities'->>'A')::double precision,(m->>'hg')::integer,(m->>'ag')::integer,
               (m->>'match_date')::date,coalesce((m->>'occurred_at')::timestamptz,now()))
        on conflict(id) do nothing;
    end loop;
    if sid is not null and coalesce((s->>'complete')::boolean,false) then
        if (select count(*) from btm_matches where season_id=sid) <> 380 then
            raise exception 'A completed season requires 380 matches';
        end if;
        -- Recompute final positions from persisted matches. Never trust submitted standings.
        with sides as (
            select home as team,hg as gf,ag as ga from btm_matches where season_id=sid
            union all select away,ag,hg from btm_matches where season_id=sid
        ), totals as (
            select team,count(*) as played,sum(case when gf>ga then 3 when gf=ga then 1 else 0 end)::integer as points,
                   sum(gf-ga) as gd,sum(gf) as gf from sides group by team
        )
        select count(*) into valid_teams from totals where played=38;
        if valid_teams <> 20 then raise exception 'Invalid team match counts'; end if;
        with sides as (
            select home as team,hg as gf,ag as ga from btm_matches where season_id=sid
            union all select away,ag,hg from btm_matches where season_id=sid
        ), totals as (
            select team,sum(case when gf>ga then 3 when gf=ga then 1 else 0 end)::integer as points,
                   sum(gf-ga) as gd,sum(gf) as gf from sides group by team
        )
        insert into btm_finishes(season_id,team,position,points)
        select sid,team,row_number() over(order by points desc,gd desc,gf desc,team)::integer,points from totals
        on conflict(season_id,team) do nothing;
        update btm_seasons set completed=true where id=sid;
    end if;
    return jsonb_build_object('ok',true);
end;
$$;

create or replace function public.btm_statistics(
    p_version text default null,p_venue text default null,p_opponent text default null,
    p_team text default null,p_page integer default 0
) returns jsonb language sql stable security invoker set search_path = public, pg_temp as $$
with matches as (
    select * from btm_matches where mode='kick' and (p_version is null or version=p_version)
), sides as (
    select id,home as team,away as opponent,'Hemma' as venue,
           case outcome when 'H' then 'V' when 'D' then 'O' else 'F' end as result,p_h as expected_win from matches
    union all
    select id,away,home,'Borta',case outcome when 'A' then 'V' when 'D' then 'O' else 'F' end,p_a from matches
), filtered as (
    select * from sides where (p_venue is null or venue=p_venue) and (p_opponent is null or opponent=p_opponent)
), kick as (
    select team,count(*) as played,count(*) filter(where result='V') as wins,
           count(*) filter(where result='O') as draws,count(*) filter(where result='F') as losses,
           sum(expected_win) as expected_wins from filtered group by team
), finishes as (
    select f.* from btm_finishes f join btm_seasons s on s.id=f.season_id
    where s.completed and (p_version is null or s.version=p_version)
), distribution as (
    select team,position,count(*) as count from finishes group by team,position
), frequencies as (
    select *,max(count) over(partition by team) as maximum from distribution
), modes as (
    select team,array_agg(position order by position) filter(where count=maximum) as common_positions
    from frequencies group by team
), seasons as (
    select f.team,count(*) as seasons,avg(position) as avg_position,min(position) as best_position,
           max(position) as worst_position,count(*) filter(where position=1) as titles,
           count(*) filter(where position<=4) as top_four,count(*) filter(where position>=18) as relegations,
           avg(points) as avg_points,m.common_positions from finishes f join modes m on m.team=f.team
    group by f.team,m.common_positions
), selected_ids as (
    select distinct id from filtered where p_team is null or team=p_team
), history as (
    select m.id,m.occurred_at,m.home,m.away,m.outcome,m.p_h,m.p_d,m.p_a from matches m
    join selected_ids i on i.id=m.id order by m.occurred_at desc,m.id desc
    limit 50 offset greatest(0,least(p_page,1000000))*50
)
select jsonb_build_object(
    'kick',coalesce((select jsonb_agg(k order by team) from kick k),'[]'::jsonb),
    'seasons',coalesce((select jsonb_agg(s order by team) from seasons s),'[]'::jsonb),
    'distribution',coalesce((select jsonb_agg(d order by team,position) from distribution d),'[]'::jsonb),
    'history',coalesce((select jsonb_agg(h order by occurred_at desc,id desc) from history h),'[]'::jsonb),
    'history_total',(select count(*) from selected_ids),
    'kick_total',(select count(distinct id) from filtered),
    'completed_seasons',(select count(*) from btm_seasons where completed and (p_version is null or version=p_version)),
    'versions',coalesce((select jsonb_agg(v order by v) from (select distinct version as v from btm_matches) x),'[]'::jsonb)
);
$$;

revoke all on function public.btm_ingest(jsonb) from public, anon, authenticated;
revoke all on function public.btm_statistics(text,text,text,text,integer) from public, anon, authenticated;
grant execute on function public.btm_ingest(jsonb) to service_role;
grant execute on function public.btm_statistics(text,text,text,text,integer) to service_role;
commit;
