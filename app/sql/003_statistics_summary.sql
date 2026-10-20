-- Add summary metrics over ALL distinct Kick-off matches matching the filters.
-- Existing data, filters, pagination, RPC signature and privileges are preserved.
-- Probability ties use H, then D, then A. This describes simulated outcomes.
-- Run after 001_shared_statistics.sql. Safe to reapply.
begin;

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
), kick_summary as (
    select count(*) filter(where outcome='H') as home_wins,
           count(*) filter(where outcome='D') as draws,
           count(*) filter(where outcome='A') as away_wins,
           count(*) filter(where outcome = case
               when p_h >= p_d and p_h >= p_a then 'H'
               when p_d >= p_a then 'D' else 'A' end) as correct_predictions
    from matches m
    where exists (select 1 from filtered f where f.id=m.id)
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
    'kick_summary',(select to_jsonb(k) from kick_summary k),
    'completed_seasons',(select count(*) from btm_seasons where completed and (p_version is null or version=p_version)),
    'versions',coalesce((select jsonb_agg(v order by v) from (select distinct version as v from btm_matches) x),'[]'::jsonb)
);
$$;

commit;
