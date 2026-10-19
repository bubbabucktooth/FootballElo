"""Beat the Model — Streamlit UI. Requires model.joblib and exported CSVs."""
from pathlib import Path
from html import escape
from base64 import b64encode
import time
import joblib
import numpy as np
import pandas as pd
import streamlit as st
from statistics_store import (SupabaseStore, StatisticsError, model_version, new_run_id,
                              kick_event, sync_season, flush_kicks)
from statistics_page import render_statistics
from ui_tables import render_table

ROOT = Path(__file__).resolve().parent
FEATURES = ['EloDiff_Enhanced', 'FormDiff', 'GoalDiff5']
K, HOME_ADV = 10, 100
st.set_page_config(page_title='Beat the Model', page_icon='⚽', layout='wide')
st.markdown('<style>' + (ROOT / 'styles.css').read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)


def logo_data_uri():
    logo = b64encode((ROOT / 'assets' / 'beat-the-model-arcade.png').read_bytes()).decode('ascii')
    return f'data:image/png;base64,{logo}'


def arcade_logo(compact=False):
    classes = 'arcade-logo header-logo' if compact else 'arcade-logo'
    return f'<img class="{classes}" src="{logo_data_uri()}" alt="Beat the Model"/>'

@st.cache_resource
def model_load():
    return joblib.load(ROOT/'model.joblib')

@st.cache_data
def csv_load(name):
    return pd.read_csv(ROOT/name)

@st.dialog('Om appen', width='large')
def show_about():
    with st.container(key='about_copy'):
        st.markdown('''# BEAT THE MODEL

### Fotboll är oförutsägbart. Men hur mycket kan vi faktiskt förutse?

Vad händer om vi låter statistik spela fotboll?

**Beat the Model** är ett interaktivt fotbollsspel byggt på en prediktiv modell för Premier League. Här möts historiska matchresultat, statistiska sannolikheter och fotbollens oförutsägbarhet.

Välj ditt favoritlag, utmana en motståndare i **Kick-off** eller ta dig an en hel Premier League-säsong. Varje match får sitt eget utfall, varje resultat påverkar säsongen och varje simulering berättar en ny historia.

**Samma förutsättningar. Nya möjligheter.**

## Idén bakom Beat the Model

Kan vi använda historiska matchdata för att förutsäga vem som vinner en fotbollsmatch?

Det var utgångspunkten för projektet.

Genom att kombinera lagens relativa styrka, tidigare prestationer och målskillnad utvecklade vi en modell som uppskattar sannolikheten för tre möjliga utfall: hemmaseger, oavgjort eller bortaseger.

Men en sannolikhet är inte ett facit.

Ett lag med 80 procents vinstchans förlorar fortfarande ibland. En underdog kan överraska. Och en säsong som simuleras två gånger behöver inte få samma mästare.

Det är just den osäkerheten som Beat the Model utforskar.
''')
        if st.button('Stäng', key='close_about', type='primary'):
            st.rerun()


brand,about=st.columns([3,1],gap='large',vertical_alignment='center')
with brand:
    if st.session_state.get('app_started', False):
        if st.button(f'![Till startsidan]({logo_data_uri()})', key='go_home', width=160):
            st.session_state.app_started = False
            st.rerun()
with about:
    if st.button('Om appen', icon=':material/info:', key='open_about'):
        show_about()
if not st.session_state.get('app_started', False):
    st.markdown(f'<div class="landing-hero">{arcade_logo()}'
                '<p class="landing-tagline">Bollen är rund.</p></div>', unsafe_allow_html=True)
    _, play_column, _ = st.columns([1, 2, 1])
    with play_column:
        if st.button('Spela nu', type='primary', use_container_width=True):
            st.session_state.app_started = True
            st.rerun()
    st.stop()

try:
    model = model_load()
    initial = csv_load('team_states.csv').set_index('Team')
except (FileNotFoundError, KeyError) as e:
    st.error(f'Saknad eller felaktig modell-/lagfil: {e}. Lägg model.joblib och team_states.csv i app-mappen.')
    st.stop()

teams = sorted(initial.index.tolist())
if list(model.classes_) != ['A', 'D', 'H']:
    st.warning('Modellens klassordning skiljer sig från den förväntade; utfall mappas via klassnamn.')

def predict(home, away, states):
    h, a = states[home], states[away]
    X = pd.DataFrame([{'EloDiff_Enhanced':h['elo']-a['elo'],
                       'FormDiff':sum(h['form'])-sum(a['form']),
                       'GoalDiff5':sum(h['goals'])-sum(a['goals'])}], columns=FEATURES)
    return dict(zip(model.classes_, model.predict_proba(X)[0]))

def starting_states():
    return {name:{'elo':float(r.Elo), 'form':[0]*5, 'goals':[0]*5}
            for name,r in initial.iterrows()}

def load_history():
    hist = csv_load('history_last5.csv')
    needed = {'Team','Seq','Points','GoalDiff'}
    if not needed.issubset(hist.columns):
        raise ValueError(f'history_last5.csv måste innehålla {sorted(needed)}')
    states = starting_states()
    for team, group in hist.sort_values('Seq').groupby('Team'):
        if team in states:
            states[team]['form'] = ([0]*5 + group['Points'].astype(int).tolist())[-5:]
            states[team]['goals'] = ([0]*5 + group['GoalDiff'].astype(int).tolist())[-5:]
    return states

# One row per historical scoreline, with outcome H/D/A.
def score_pools():
    df = csv_load('historical_scores.csv')
    pools = {key:df.loc[df.Outcome.eq(key), ['FTHG','FTAG']].to_numpy(dtype=int)
             for key in ('H','D','A')}
    if any(len(p)==0 for p in pools.values()):
        raise ValueError('Historiska målresultat måste innehålla H, D och A.')
    return pools

def sample_score(probs, rng, pools):
    keys = list(model.classes_)
    outcome = str(rng.choice(keys, p=[probs[k] for k in keys]))
    score = pools[outcome][rng.integers(len(pools[outcome]))]
    return outcome, int(score[0]), int(score[1])

def update_states(states, batch):
    """Apply a full calendar day after all predictions, matching notebook simulation."""
    for home, away, outcome, hg, ag, rh, ra in batch:
        margin = hg-ag
        hp = 3 if outcome=='H' else 1 if outcome=='D' else 0
        ap = 3 if outcome=='A' else 1 if outcome=='D' else 0
        expected = 1/(1+10**((ra-rh-HOME_ADV)/400))
        actual = 1 if outcome=='H' else .5 if outcome=='D' else 0
        factor = 1+np.log1p(abs(margin)) if margin else 1.
        delta = K*factor*(actual-expected)
        states[home]['elo'] += delta
        states[away]['elo'] -= delta
        for name,pts,gd in [(home,hp,margin),(away,ap,-margin)]:
            states[name]['form'] = states[name]['form'][1:]+[pts]
            states[name]['goals'] = states[name]['goals'][1:]+[gd]

def empty_table():
    return {t:dict(P=0,W=0,D=0,L=0,GF=0,GA=0,Pts=0) for t in teams}

def simulate_day(season, fixtures, dates, pools):
    """Predict the whole day before updating any team's Elo or form."""
    idx = season['date_idx']
    date = dates[idx]
    rng = np.random.default_rng(season['rng_seed'] + idx)
    pending = []
    for row in fixtures.loc[fixtures.Date.eq(date)].itertuples():
        h, a = row.HomeTeam, row.AwayTeam
        probs = predict(h, a, season['states'])
        outcome, hg, ag = sample_score(probs, rng, pools)
        pending.append((h, a, outcome, hg, ag,
                        season['states'][h]['elo'], season['states'][a]['elo']))
        apply_table(season['table'], h, a, hg, ag)
        season['history'].append({'Date': date.strftime('%Y-%m-%d'),
                                  'HomeTeam': h, 'AwayTeam': a, 'Score': f'{hg}–{ag}',
                                  'probabilities': {key: float(probs[key]) for key in ('H','D','A')}})
        if season['team'] in (h, a):
            own_goals, other_goals = (hg, ag) if season['team'] == h else (ag, hg)
            season['last_match'] = dict(home=h, away=a, hg=hg, ag=ag,
                                        win_probability=float(probs['H'] if season['team'] == h else probs['A']),
                                        date=date.strftime('%d/%m %Y'),
                                        result='Vinst' if own_goals > other_goals else
                                        'Oavgjort' if own_goals == other_goals else 'Förlust')
    update_states(season['states'], pending)
    season['date_idx'] += 1

def advance_to_own_match(season, fixtures, dates, pools):
    while season['date_idx'] < len(dates):
        day = fixtures.loc[fixtures.Date.eq(dates[season['date_idx']])]
        if (day.HomeTeam.eq(season['team']) | day.AwayTeam.eq(season['team'])).any():
            break
        simulate_day(season, fixtures, dates, pools)

def match_preview(own, opponent, home, probs, states):
    win = probs['H'] if home else probs['A']
    lose = probs['A'] if home else probs['H']
    outlook = ('Modellen ger dig ett övertag.' if win - lose > .15 else
               'Motståndaren har övertaget enligt modellen.' if lose - win > .15 else
               'Modellen pekar på en jämn match.')
    form = sum(states[opponent]['form'])
    caution = ('Se upp för en motståndare med stark poängform.' if form >= 10 else
               'Motståndaren har haft svårt att samla poäng.' if form <= 4 else
               'Motståndarens senaste resultat har varit blandade.')
    return (f"{'Hemmamatch' if home else 'Bortamatch'} mot {opponent}. {outlook} "
            f'{caution} De har tagit {form} poäng på sina fem senaste matcher, '
            f"du har tagit {sum(states[own]['form'])}.")

def match_card(home, away, middle, caption, winner=None, champion=None, details=None, tone=''):
    home_pose = 'trophy' if home == champion else 'win' if home == winner else 'sad' if winner else 'neutral'
    away_pose = 'trophy' if away == champion else 'win' if away == winner else 'sad' if winner else 'neutral'
    details = details or {}
    st.markdown(f'''<div class="match-card {tone}">
        <div class="match-caption">{escape(caption)}</div>
        <div class="match-teams">
        <div class="match-team">{team_badge(home, home_pose)}<span class="team-name">{escape(home)}</span><small class="match-venue">HEMMA</small>{details.get(home, '')}</div>
        <div class="match-score">{escape(middle)}</div>
        <div class="match-team">{team_badge(away, away_pose)}<span class="team-name">{escape(away)}</span><small class="match-venue">BORTA</small>{details.get(away, '')}</div></div></div>''',
        unsafe_allow_html=True)


# Stylised home kits for the season represented by the app: shirt, trim, shorts, pattern.
TEAM_KITS = {
    'Arsenal': ('#e33136', '#ffffff', '#ffffff', 'sleeves'),
    'Bournemouth': ('#d82c37', '#191a22', '#191a22', 'stripes'),
    'Brighton': ('#2673ce', '#ffffff', '#2673ce', 'stripes'),
    'Burnley': ('#7e2943', '#8fc8e9', '#ffffff', 'sleeves'),
    'Chelsea': ('#2054c5', '#ffffff', '#2054c5', 'plain'),
    'Crystal Palace': ('#2255bb', '#d82f42', '#2255bb', 'stripes'),
    'Everton': ('#2453bf', '#ffffff', '#ffffff', 'plain'),
    'Huddersfield': ('#2681cd', '#ffffff', '#ffffff', 'stripes'),
    'Leicester': ('#245cc5', '#e7c35d', '#245cc5', 'plain'),
    'Liverpool': ('#bd2438', '#ffffff', '#bd2438', 'plain'),
    'Man City': ('#8ac7ec', '#ffffff', '#ffffff', 'plain'),
    'Man United': ('#d52b35', '#ffffff', '#191a22', 'plain'),
    'Newcastle': ('#eeeeef', '#20212a', '#20212a', 'stripes'),
    'Southampton': ('#e43843', '#ffffff', '#20212a', 'stripes'),
    'Stoke': ('#e33140', '#ffffff', '#ffffff', 'stripes'),
    'Swansea': ('#f5f5f4', '#20212a', '#f5f5f4', 'plain'),
    'Tottenham': ('#f5f5f4', '#202b4d', '#202b4d', 'plain'),
    'Watford': ('#f5d637', '#20212a', '#20212a', 'plain'),
    'West Brom': ('#f4f4f3', '#202e50', '#ffffff', 'stripes'),
    'West Ham': ('#80324d', '#89c7e8', '#ffffff', 'sleeves'),
}


def team_badge(team, pose='neutral'):
    """Classic pixel footballer, shared by every view and result pose."""
    shirt, trim, shorts, pattern = TEAM_KITS.get(team, ('#9572d8', '#ffffff', '#30244c', 'plain'))
    sleeves = trim if pattern == 'sleeves' else shirt
    stripes = ''.join(f'<rect x="{x}" y="11" width="2" height="8" fill="{trim}"/>'
                      for x in (9, 13)) if pattern == 'stripes' else ''
    happy = pose in ('win', 'trophy')
    if happy:
        arms = (f'<path d="M5 11h3v4H5zm11 0h3v4h-3z" fill="{sleeves}"/>'
                '<path d="M4 2h2v10H4zm14 0h2v10h-2z" fill="#d6a179"/>'
                '<path d="M3 0h4v3H3zm14 0h4v3h-4z" fill="#e0ac86"/>')
    else:
        arms = (f'<path d="M5 11h3v7H4v-5h1zm11 0h3v2h1v5h-4z" fill="{sleeves}"/>'
                '<path d="M4 18h3v4H4zm13 0h3v4h-3z" fill="#d6a179"/>')
    mouth = ('<path d="M10 8h4v1h-4z" fill="#743d3c"/>' if happy else
             '<path d="M11 8h2v1h-2z" fill="#743d3c"/>'
             '<path d="M15 7h1v2h-1z" fill="#80d9ff"/>' if pose == 'sad' else '')
    trophy = ('<path d="M4-5h3v6H4zm13 0h3v6h-3z" fill="none" stroke="#d79423" stroke-width="1"/>'
              '<path d="M7-6h10v5h-2v1h-2v1h2v1H9V1h2V0H9V-1H7z" fill="#f2bb39"/>'
              '<path d="M8-5h2v3H8z" fill="#fff1a1"/>' if pose == 'trophy' else '')
    label = {'neutral': 'hemmast?ll', 'win': 'jublar med b?da h?nderna ?ver huvudet',
             'sad': 'ledsen efter f?rlust', 'trophy': 'lyfter pokalen'}[pose]
    # Reserve the same headroom in every pose, preserving identical body scale.
    return (f'<svg class="pixel-player pose-{pose}" viewBox="0 -8 24 38" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="{escape(team, quote=True)} ? {label}" shape-rendering="crispEdges">'
            '<ellipse cx="12" cy="29" rx="9" ry="1" fill="#080515" opacity=".35"/>'
            f'<g transform="translate(0 {1 if pose == "sad" else 0})">'
            '<path d="M9 2h6v1h2v5h-2v2H9V8H7V3h2z" fill="#d6a179"/>'
            '<path d="M9 1h6v1h2v3h-2V3H9v2H7V2h2z" fill="#352a30"/>'
            '<path d="M9 6h1v1H9zm5 0h1v1h-1z" fill="#32232c"/>'
            f'{mouth}</g>{arms}'
            f'<path d="M8 10h8v10H8z" fill="{shirt}"/>{stripes}'
            f'<path d="M10 10h4v2h-4z" fill="{trim}"/>'
            f'<path d="M8 20h8v4h-3v-2h-2v2H8z" fill="{shorts}"/>'
            f'<path d="M8 24h3v3H8zm5 0h3v3h-3z" fill="{shirt}"/>'
            '<path d="M7 27h4v2H6v-1h1zm6 0h4v1h1v1h-5z" fill="#eee9f8"/>'
            f'{trophy}</svg>')


def show_match_result(result):
    tone = {'Vinst': 'result-win', 'Oavgjort': 'result-draw', 'Förlust': 'result-loss'}[result['result']]
    st.markdown(f'<div class="result-reveal {tone}"><div class="result-label">'
                f'{"Seriesegrare!" if result.get("new_champion") else escape(result["result"])}</div></div>', unsafe_allow_html=True)
    if result.get('mode') == 'kick':
        winner = result['home'] if result['outcome'] == 'H' else result['away'] if result['outcome'] == 'A' else None
        match_card(result['home'], result['away'], {'H': '1', 'D': 'X', 'A': '2'}[result['outcome']],
                   'KICK-OFF · MATCHUTFALL', winner=winner, tone=tone)
        st.write({'Vinst': 'Segern är din! Poängen är inne.', 'Oavgjort': 'Delad pott. Redo för ett nytt försök?',
                  'Förlust': 'Motståndaren vann den här gången. Ladda om!'}[result['result']])
        st.markdown('<div class="result-grid kick-grid result-reveal">'
                    f'<div class="result-stat"><strong>{result["delta"]:+d}</strong><small>Poäng den här matchen</small></div>'
                    f'<div class="result-stat"><strong>{result["points"]:+d}</strong><small>Totalpoäng</small></div></div>',
                    unsafe_allow_html=True)
        st.caption('1 = hemmavinst · X = oavgjort · 2 = bortavinst. Kick-off simulerar endast matchutfall.')
        if result.get('match_number'):
            st.caption(f'Match {result["match_number"]} av {result["limit"]}')
        if st.button('Visa sammanfattning' if result.get('finished') else 'Till nästa match', type='primary', use_container_width=True, key='close_result',on_click=dismiss_result):
            st.session_state.pop('pending_result',None)
            st.rerun()
        return
    winner = result['home'] if result['hg'] > result['ag'] else result['away'] if result['ag'] > result['hg'] else None
    match_card(result['home'], result['away'], f"{result['hg']} – {result['ag']}",
               f"SLUTRESULTAT · {result['date']}", winner=winner,
               champion=result['team'] if result.get('champion') else None, tone=tone)
    for notice in result.get('notices', []):
        st.info(notice)
    st.write(result.get('reaction', 'Matchen är färdigspelad.'))
    before, after = result.get('place_before'), result.get('place_after')
    movement = f'{before} → {after}' if before is not None else str(after or '–')
    gained = {'Vinst': 3, 'Oavgjort': 1, 'Förlust': 0}[result['result']]
    st.markdown('<div class="result-grid result-reveal">'
                f'<div class="result-stat"><strong>+{gained}</strong><small>Matchpoäng</small></div>'
                f'<div class="result-stat"><strong>{movement}</strong><small>Tabellplats</small></div>'
                f'<div class="result-stat"><strong>{result.get("streak", 0)}</strong><small>Raka vinster</small></div>'
                '</div>', unsafe_allow_html=True)
    st.caption(f"{result['team']} · {result['points']} poäng totalt. "
               'Tabellplats: före din match → efter alla simulerade matcher fram till nästa egna match eller säsongsslutet. '
               'Före första matchen visas bara den aktuella platsen.')
    if result.get('quick_title'):
        if st.button('Fortsätt snabbsimuleringen' if result['quick_pause'] else 'Visa snabbsammanfattning',
                     type='primary', use_container_width=True, key='continue_quick'):
            st.session_state['quick_request'] = 'continue' if result['quick_pause'] else 'summary'
            st.session_state.pop('pending_result',None)
            st.rerun()
        return
    if st.button('Till säsongsöversikten' if result['finished'] else 'Till nästa match',
                 type='primary', use_container_width=True, key='close_result',on_click=dismiss_result):
        st.session_state.pop('pending_result',None)
        st.rerun()


def show_quick_setup(remaining):
    st.subheader('Hur långt vill du spela?')
    choice = st.radio('Antal matcher', ['Nästa 3 matcher', 'Nästa 5 matcher', 'Resten av säsongen'], key='quick_choice')
    count = min(3 if choice == 'Nästa 3 matcher' else 5 if choice == 'Nästa 5 matcher' else remaining, remaining)
    st.write(f'Ditt lag spelar {count} matcher. Övriga ligan följer med automatiskt.')
    st.caption('Om du säkrar seriesegern pausar simuleringen för pokalfirandet.')
    if st.button(f'Simulera {count} matcher', type='primary', use_container_width=True, key='confirm_quick'):
        st.session_state['quick_request'] = count
        st.session_state.pop('show_quick_setup',None)
        st.rerun()


def show_quick_summary(batch):
    results = batch['results']
    last = results[-1]
    st.subheader(f'{len(results)} matcher spelade')
    wins = sum(r['result'] == 'Vinst' for r in results)
    draws = sum(r['result'] == 'Oavgjort' for r in results)
    st.write(f"**{wins} vinster · {draws} oavgjorda · {len(results)-wins-draws} förluster**")
    start = batch['place_before']
    movement = f'{start} → {last["place_after"]}' if start is not None else str(last['place_after'])
    st.write(f"+{last['points']-batch['points_before']} poäng · Placering {movement}")
    if last.get('champion'):
        st.markdown(f'<div class="batch-champion">{team_badge(last["team"], "trophy")}<strong>Seriesegrare!</strong></div>', unsafe_allow_html=True)
    for notice in last.get('notices', []):
        st.info(notice)
    render_table(pd.DataFrame([{'Datum': r['date'], 'Hemma': r['home'], 'Resultat': f"{r['hg']}–{r['ag']}",
                                'Borta': r['away']} for r in results]), 'quick_results')
    if st.button('Visa säsongssammanfattning' if last['finished'] else 'Till nästa match',
                 type='primary', use_container_width=True, key='close_quick',on_click=dismiss_quick_summary):
        st.session_state.pop('pending_quick_summary',None)
        st.rerun()


def dismiss_result():
    st.session_state.pop('pending_result',None)


def dismiss_quick_setup():
    st.session_state.pop('show_quick_setup',None)


def dismiss_quick_summary():
    st.session_state.pop('pending_quick_summary',None)


if hasattr(st, 'dialog'):
    result_dialog = st.dialog('Matchresultat',on_dismiss=dismiss_result)(show_match_result)
    quick_setup_dialog = st.dialog('Snabbsimulera',on_dismiss=dismiss_quick_setup)(show_quick_setup)
    quick_summary_dialog = st.dialog('Snabbsimulering · resultat',on_dismiss=dismiss_quick_summary)(show_quick_summary)
else:
    result_dialog = None
    quick_setup_dialog = quick_summary_dialog = None

def apply_table(table, h, a, hg, ag):
    for t,gf,ga in ((h,hg,ag),(a,ag,hg)):
        r=table[t]; r['P']+=1; r['GF']+=gf; r['GA']+=ga
        if gf>ga: r['W']+=1; r['Pts']+=3
        elif gf==ga: r['D']+=1; r['Pts']+=1
        else: r['L']+=1

def standings(table, selected=None):
    rows=[dict(Lag=t, **r, MS=r['GF']-r['GA']) for t,r in table.items()]
    out=pd.DataFrame(rows).sort_values(['Pts','MS','GF','Lag'],ascending=[False,False,False,True]).reset_index(drop=True)
    out.insert(0,'#',np.arange(1,len(out)+1))
    out['Lag'] = out['Lag'].map(lambda t: ('⭐ '+t) if t==selected else t)
    return out[['#','Lag','P','W','D','L','MS','Pts']]


def season_results(history):
    """Keep season-only results in chronological order, including older sessions."""
    results = {team: [] for team in teams}
    for match in history:
        hg, ag = map(int, match['Score'].split('–'))
        results[match['HomeTeam']].append('V' if hg > ag else 'O' if hg == ag else 'F')
        results[match['AwayTeam']].append('V' if ag > hg else 'O' if hg == ag else 'F')
    return results


def winning_streak(results):
    count = 0
    for result in reversed(results):
        if result != 'V':
            break
        count += 1
    return count


def league_position(table, team):
    return int(standings(table).loc[lambda df: df.Lag.eq(team), '#'].iloc[0])


def result_reaction(result, previous_results, first_away_win=False):
    variant = len(previous_results) % 3
    margin = abs(result.get('hg', 0) - result.get('ag', 0))
    if result['result'] == 'Vinst':
        if result['streak'] >= 3:
            return f"{result['streak']} raka vinster! Segertåget fortsätter."
        if result.get('win_probability', 1) < .35:
            return 'Du trotsade oddsen! En seger när modellen höll motståndaren högre.'
        if first_away_win:
            return 'Första bortasegern för säsongen! Tre poäng följer med hem.'
        if 'V' not in previous_results:
            return 'Där satt den! Säsongens första seger är din.'
        if margin >= 3:
            return 'Vilken urladdning! En storseger med minst tre måls marginal.'
        return ('Tre poäng till! En seger att bygga vidare på.',
                'Segern är din! Laget tar ett nytt steg framåt.',
                'Full pott! Det här resultatet stärker säsongen.')[variant]
    if result['result'] == 'Oavgjort':
        if previous_results and previous_results[-1] == 'V':
            return 'Segersviten stannar här, men du tar med dig en poäng.'
        return ('Delad pott. En poäng när lagen inte gick att skilja åt.',
                'Ingen vinnare den här gången. En poäng till samlingen.',
                'Lika på tavlan. Nu riktas blicken mot nästa match.')[variant]
    if previous_results and previous_results[-1] == 'V':
        return 'Segersviten är bruten. Dags att ladda om.'
    if margin >= 3:
        return 'En tung smäll. Efter en storförlust gäller det att samla laget igen.'
    return ('Tungt resultat. Nästa match ger en ny chans.',
            'Inga poäng den här gången. Säsongen fortsätter.',
            'Motståndaren tog hem det. Dags att hitta tillbaka.')[variant]


def title_status(table, own, fixtures, opponent=None):
    """Use strict points superiority; a possible tie never clinches an unfinished season."""
    totals = fixtures.HomeTeam.value_counts().add(fixtures.AwayTeam.value_counts(), fill_value=0)
    remaining = {team: max(0, int(totals.get(team, 0)) - row['P']) for team, row in table.items()}
    points = table[own]['Pts']
    rivals = [team for team in table if team != own]
    finished = not any(remaining.values())
    champion = (league_position(table, own) == 1 if finished else
                all(points > table[t]['Pts'] + 3*remaining[t] for t in rivals))
    # Against this opponent, an own win also removes one of their remaining wins.
    can_clinch = (not champion and remaining[own] > 0 and opponent is not None and
                  all(points + 3 > table[t]['Pts'] + 3*remaining[t] - (3 if t == opponent else 0)
                      for t in rivals))
    return champion, can_clinch


def relegation_notice(place, before=None, finished=False):
    if place < 18:
        return None
    if finished:
        return f'Säsongen är slut. Plats {place} innebär nedflyttning.'
    if before is not None and before < 18:
        return f'Du har hamnat på plats {place}, under nedflyttningsstrecket. Säsongen är inte avgjord.'
    return f'Plats {place}: laget ligger på nedflyttningsplats. Poäng behövs för att ta sig över strecket.'


def play_next_match(season, fixtures, dates, pools):
    team = season['team']
    previous = season_results(season['history'])[team]
    before = league_position(season['table'], team) if previous else None
    was_champion, _ = title_status(season['table'], team, fixtures)
    previous_away_wins = any(m['AwayTeam'] == team and
        int(m['Score'].split('–')[1]) > int(m['Score'].split('–')[0]) for m in season['history'])
    simulate_day(season, fixtures, dates, pools)
    advance_to_own_match(season, fixtures, dates, pools)
    result = dict(season['last_match'], team=team, points=season['table'][team]['Pts'],
                  finished=season['date_idx'] == len(dates), place_before=before,
                  place_after=league_position(season['table'], team),
                  streak=winning_streak(season_results(season['history'])[team]))
    result['reaction'] = result_reaction(result, previous,
        first_away_win=result['away'] == team and not previous_away_wins)
    result['champion'], _ = title_status(season['table'], team, fixtures)
    result['new_champion'] = result['champion'] and not was_champion
    result['notices'] = []
    if result['new_champion']:
        result['notices'].append('Seriesegern är säkrad! Pokalen är din.')
    notice = relegation_notice(result['place_after'], before, result['finished'])
    if notice:
        result['notices'].append(notice)
    return result


def simulate_batch(season, fixtures, dates, pools, batch):
    """Pause exactly once at a newly secured title; continuation retains the original request."""
    while batch['remaining'] > 0 and season['date_idx'] < len(dates):
        result = play_next_match(season, fixtures, dates, pools)
        batch['results'].append(result)
        batch['remaining'] -= 1
        if result['new_champion']:
            return dict(result, quick_title=True, quick_pause=batch['remaining'] > 0 and not result['finished'])
    return None


def match_context(table, own, opponent, fixtures=None):
    if fixtures is not None:
        champion, can_clinch = title_status(table, own, fixtures, opponent)
        if champion:
            return 'Seriesegrare · pokalen är redan säkrad'
        if can_clinch:
            return 'Titelmatch · en seger säkrar seriesegern!'
    if not table[own]['P'] or not table[opponent]['P']:
        return 'Säsongen tar form · varje poäng räknas'
    own_place, opponent_place = league_position(table, own), league_position(table, opponent)
    if own_place >= 18:
        return f'Under strecket · plats {own_place}. Poäng behövs i kampen om nytt kontrakt.'
    if own_place == 17:
        return 'Nära nedflyttningsstrecket · viktiga poäng för att hålla lagen bakom dig'
    if opponent_place == 1:
        return 'Utmaningen · du möter serieledaren'
    if abs(own_place - opponent_place) <= 2:
        return 'Match mot ett lag nära dig i tabellen'
    return f'Motståndarkollen · plats {opponent_place}, {table[opponent]["Pts"]} poäng'


def reset_season():
    season = st.session_state.get('season')
    if shared_store is not None and season and 'run_id' in season:
        if season.get('statistics_cursor', 0) < len(season['history']) or (len(season['history']) == 380 and not season.get('statistics_complete')):
            archived = {k: season[k] for k in ('run_id','model_version','team','history')}
            archived['history'] = list(season['history'])
            archived['statistics_cursor'] = season.get('statistics_cursor', 0)
            archived['statistics_complete'] = season.get('statistics_complete', False)
            st.session_state.setdefault('pending_statistics_seasons', []).append(archived)
    st.session_state.season = None
    for key in ('quick_batch', 'quick_request', 'show_quick_setup', 'pending_quick_summary', 'pending_result'):
        st.session_state.pop(key, None)


# Final league positions, 2016/17: https://www.rsssf.org/tablese/eng2017.html
PREVIOUS_SEASON = {
    'Chelsea': (1, 'Premier League'), 'Tottenham': (2, 'Premier League'),
    'Man City': (3, 'Premier League'), 'Liverpool': (4, 'Premier League'),
    'Arsenal': (5, 'Premier League'), 'Man United': (6, 'Premier League'),
    'Everton': (7, 'Premier League'), 'Southampton': (8, 'Premier League'),
    'Bournemouth': (9, 'Premier League'), 'West Brom': (10, 'Premier League'),
    'West Ham': (11, 'Premier League'), 'Leicester': (12, 'Premier League'),
    'Stoke': (13, 'Premier League'), 'Crystal Palace': (14, 'Premier League'),
    'Swansea': (15, 'Premier League'), 'Burnley': (16, 'Premier League'),
    'Watford': (17, 'Premier League'), 'Newcastle': (1, 'Championship'),
    'Brighton': (2, 'Championship'), 'Huddersfield': (5, 'Championship'),
}


def probability_cards(home, away, probabilities, decimals=0):
    maximum = max(probabilities.values())
    cards = ''.join(f'<div class="probability-card {"probability-leading" if probabilities[key] == maximum else ""}"><span>{escape(label)}</span>'
                    f'<strong>{probabilities[key]:.{decimals}%}</strong>'
                    f'<small>{"Högst sannolikhet" if probabilities[key] == maximum else "Modellens prognos"}</small></div>'
                    for label, key in ((home, 'H'), ('Oavgjort', 'D'), (away, 'A')))
    st.markdown(f'<div class="probability-grid" aria-label="Modellens sannolikheter">{cards}</div>',
                unsafe_allow_html=True)


def team_preview(team, elo):
    previous = PREVIOUS_SEASON.get(team)
    previous_text = ''
    if previous:
        position, league = previous
        promoted = ' · Uppflyttade' if league == 'Championship' else ''
        previous_text = f'<small>2016/17 · Plats {position} i {league}{promoted}</small>'
    st.markdown(f'<div class="team-preview">{team_badge(team)}<div class="team-details"><strong>{escape(team)}</strong>'
                f'<small>Lagstyrka · Elo {elo:,.0f}</small>{previous_text}</div></div>', unsafe_allow_html=True)


def kickoff_details(team):
    position, league = PREVIOUS_SEASON[team]
    return (f'<small class="match-strength">Elo {float(initial.loc[team, "Elo"]):,.0f}</small>'
            f'<small class="match-history">2016/17 · {position} i {escape(league)}</small>')


def render_match_analysis(own, opponent, home, probs, states, table):
    # Present the existing preview in two sections without changing its conditions or values.
    preview = match_preview(own, opponent, home, probs, states)
    fixture_text, outlook, form = preview.split('. ', 2)
    played = {team: table[team]['P'] for team in (own, opponent)}
    form_heading = 'Form i din säsong'
    descriptions = []
    for team, count in played.items():
        if count == 0:
            descriptions.append(f'{team}: ingen spelad match ännu.')
        else:
            recent_count = min(count, 5)
            points = table[team]['Pts'] if count < 5 else sum(states[team]['form'])
            period = 'sin enda match' if recent_count == 1 else f'sina {recent_count} senaste matcher'
            descriptions.append(f'{team}: {points} poäng på {period}.')
    form = ' '.join(descriptions)
    if any(count < 5 for count in played.values()):
        form += ' Modellens prognos använder även historisk form tills laget har spelat fem matcher.'
    st.markdown('<div class="match-analysis"><section><h3>Matchanalys</h3><p>'
                + escape(f'{fixture_text}. {outlook}.') + '</p></section>'
                '<section><h3>' + form_heading + '</h3><p>' + escape(form) + '</p></section></div>',
                unsafe_allow_html=True)


def actual_snapshot(actual_results, cutoff=None):
    table = empty_table()
    history = []
    if cutoff is not None:
        for row in actual_results.loc[actual_results.Date.le(pd.Timestamp(cutoff))].itertuples():
            apply_table(table, row.HomeTeam, row.AwayTeam, int(row.FTHG), int(row.FTAG))
            history.append(dict(HomeTeam=row.HomeTeam, AwayTeam=row.AwayTeam,
                                Score=f'{int(row.FTHG)}–{int(row.FTAG)}'))
    return table, season_results(history)


def points_difference(simulated, actual):
    return {team: simulated[team]['Pts'] - actual[team]['Pts'] for team in simulated}


def season_comparison(simulated, actual):
    differences = points_difference(simulated, actual)
    rows = [dict(Lag=team, Simulerat=simulated[team]['Pts'], Verkligt=actual[team]['Pts'],
                 Skillnad=delta, Absolutfel=abs(delta)) for team, delta in differences.items()]
    comparison = pd.DataFrame(rows).sort_values(['Absolutfel', 'Lag'], ascending=[False, True])
    return comparison.reset_index(drop=True), float(comparison['Absolutfel'].mean())


def render_season_comparison(simulated, actual, team):
    comparison, mean_error = season_comparison(simulated, actual)
    own = comparison.loc[comparison.Lag.eq(team)].iloc[0]
    delta = int(own.Skillnad)
    description = (f'{abs(delta)} poäng fler' if delta > 0 else
                   f'{abs(delta)} poäng färre' if delta < 0 else 'Exakt lika många poäng')
    st.subheader('Hur nära verkligheten?')
    st.write(f"**{team}: {int(own.Simulerat)} poäng mot verklighetens {int(own.Verkligt)}.** {description}.")
    st.metric('Genomsnittligt absolut poängfel per lag', f'{mean_error:.1f}'.replace('.', ',') + ' poäng')
    st.caption('Medelvärdet av lagens absoluta poängskillnader. Plus och minus tar inte ut varandra. '
               'Detta jämför en simulerad säsong med verkligheten och är inte ensamt ett mått på modellens kvalitet.')
    with st.expander('Jämför alla 20 lag'):
        render_table(comparison, 'season_comparison', team, formats={'Skillnad': '{:+d}'})


def render_league_table(table, results, selected=None, differences=None):
    rows = []
    for row in standings(table).to_dict('records'):
        team = row['Lag']
        recent = results[team][-5:]
        markers = ''.join('<span class="form-dot form-empty" title="Ingen spelad match">–</span>'
                          for _ in range(5 - len(recent)))
        labels = {'V': 'Vinst', 'O': 'Oavgjort', 'F': 'Förlust'}
        markers += ''.join(f'<span class="form-dot form-{r}" title="{labels[r]}">{r}</span>' for r in recent)
        zone = 'zone-cl' if row['#'] <= 4 else 'zone-el' if row['#'] == 5 else 'zone-down' if row['#'] >= 18 else ''
        classes = f'{zone} {"own-team" if team == selected else ""}'.strip()
        zone_label = 'Champions League' if row['#'] <= 4 else 'Europa League' if row['#'] == 5 else 'Nedflyttning' if row['#'] >= 18 else 'Ligaplats'
        delta_cell = ''
        if differences is not None:
            delta = differences[team]
            tone = 'delta-positive' if delta > 0 else 'delta-negative' if delta < 0 else ''
            delta_cell = f'<td class="{tone}">{delta:+d}</td>'
        rows.append(f'<tr class="{classes}"><td title="{zone_label}">{row["#"]}</td><td>{escape(team)}</td><td>{row["P"]}</td>'
                    f'<td>{row["MS"]}</td><td><strong>{row["Pts"]}</strong></td>{delta_cell}'
                    f'<td><span class="form-strip">{markers}</span></td></tr>')
    widths = [7,29,7,8,9,10,30] if differences is not None else [7,31,8,9,10,35]
    columns = '<colgroup>' + ''.join(f'<col style="width:{width}%">' for width in widths) + '</colgroup>'
    delta_header = '<th title="Simulerade poäng minus verkliga poäng">Δ</th>' if differences is not None else ''
    st.markdown('<div class="league-wrap"><table class="league-table" aria-label="Ligatabell">'
                + columns + '<thead><tr><th>#</th><th>Lag</th><th>P</th><th>MS</th><th>Pts</th>'
                + delta_header + '<th>Form</th></tr></thead>'
                '<tbody>' + ''.join(rows) + '</tbody></table></div>', unsafe_allow_html=True)
    with st.expander('Så läser du tabellen'):
        st.caption('P = spelade matcher · MS = målskillnad · Pts = ligapoäng. Sortering: poäng, målskillnad, gjorda mål.')
        st.caption('Senaste 5: grön V = vinst · grå O = oavgjort · röd F = förlust. Nyaste till höger.')
        if differences is not None:
            st.caption('Δ = simulerade poäng minus verkliga poäng vid samma speldatum, i båda flikarna.')
        st.caption('Förenklade Europaplatser. Cupresultat kan påverka verklig kvalificering.')
    st.markdown('<div class="zone-legend"><span class="legend-cl">1–4 CL</span>'
                '<span class="legend-el">5 EL</span><span class="legend-down">18–20 Nedflyttning</span></div>',
                unsafe_allow_html=True)

def shared_statistics_connection():
    try:
        config = st.secrets.get('statistics', {})
    except FileNotFoundError:
        return None
    if not config.get('enabled', False):
        return None
    try:
        return SupabaseStore(str(config.get('url', '')), str(config.get('secret_key', '')))
    except StatisticsError as error:
        st.warning(str(error))
        return None


def sync_shared_statistics(store):
    if store is None:
        return
    # Bound retries during an outage; the manual retry button can bypass this delay.
    if time.monotonic() - st.session_state.get('statistics_last_attempt', -100) < 30 and st.session_state.get('statistics_error'):
        return
    st.session_state.statistics_last_attempt = time.monotonic()
    try:
        flush_kicks(store, st.session_state.setdefault('statistics_outbox', []))
        rounds = st.session_state.setdefault('pending_kick_rounds', [])
        while rounds:
            store.save_round(rounds[0])
            rounds.pop(0)
            load_highscores.clear()
        archived = st.session_state.setdefault('pending_statistics_seasons', [])
        while archived:
            sync_season(store, archived[0], complete=len(archived[0]['history']) == 380)
            archived.pop(0)
        season = st.session_state.get('season')
        if season:
            sync_season(store, season, complete=len(season['history']) == 380)
        st.session_state.pop('statistics_error', None)
    except (StatisticsError, ValueError, KeyError):
        st.session_state.statistics_error = True


@st.cache_data(ttl=30, show_spinner=False)
def load_highscores(endpoint, version, _store):
    return _store.highscores(version)


def round_illustration(count):
    """Small original pixel pitch, made with existing kit sprites."""
    return (f'<div class="round-pitch round-pitch-{count}" aria-hidden="true">'
            '<div class="pitch-center"></div>' + team_badge('Arsenal') + team_badge('Chelsea') +
            '<span class="pixel-ball"></span></div>')


current_model_version = model_version(ROOT)
shared_store = shared_statistics_connection()
sync_shared_statistics(shared_store)
if st.session_state.get('statistics_error'):
    st.warning('Gemensam statistik kunde inte sparas. Spelet fortsätter och resultatet väntar på '
               'ett nytt försök i den här sessionen. Stäng inte sidan om du vill försöka spara igen.')
    if st.button('Försök spara statistiken igen', key='retry_statistics'):
        st.session_state.statistics_last_attempt = -100
        st.rerun()

mode_icons = {'Kick-off': 'sports_soccer', 'Säsong': 'emoji_events', 'Statistik': 'bar_chart'}
mode=st.segmented_control('Spelläge', ['Kick-off','Säsong','Statistik'], default='Kick-off', required=True,
                          format_func=lambda value: f':material/{mode_icons[value]}: {value}',
                          key='game_mode', label_visibility='collapsed')
st.divider()
result_fallback = st.container()
if mode == 'Statistik':
    actual = csv_load('actual_results_2017.csv').copy()
    actual['Date'] = pd.to_datetime(actual['Date'])
    actual_table, _ = actual_snapshot(actual, actual.Date.max())
    render_statistics(shared_store, teams, current_model_version, {t: r['Pts'] for t,r in actual_table.items()})
    st.stop()
if mode=='Kick-off':
    left,right=st.columns([1.5,1],gap='large')
    x = st.session_state.get('kick')
    # Previous unlimited sessions remain separate from the new fixed-length rounds.
    if x and 'limit' not in x:
        st.session_state.pop('kick', None)
        st.session_state.pop('pending_result', None)
        x = None
    team_label = lambda team: team
    with left:
        st.header('Kick-off')
        if x is None:
            st.subheader('Välj spelsätt')
            st.caption('Hur många matcher vill du spela?')
            choice_columns = st.columns(3)
            length = st.session_state.get('kick_length')
            for column, count in zip(choice_columns, (10,20,30)):
                with column, st.container(key=f'round_choice_{count}'):
                    st.markdown(round_illustration(count), unsafe_allow_html=True)
                    if st.button(f'{count} matcher', key=f'choose_round_{count}',
                                 type='primary' if length == count else 'secondary', use_container_width=True):
                        st.session_state.kick_length = count
                        st.rerun()
            if length is not None:
                selected = st.selectbox('Ditt lag', teams, format_func=team_label, key='kick_team_choice')
                team_preview(selected, float(initial.loc[selected, 'Elo']))
                st.caption('Du spelar hela omgången med samma lag. Motståndare och spelplats väljer du inför varje match.')
                if st.button('Starta omgång', type='primary', use_container_width=True):
                    st.session_state.kick = dict(team=selected, limit=length, matches=0, wins=0,
                        draws=0, points=0, last=None, history=[], run_id=new_run_id(),
                        model_version=current_model_version, event_ids=[])
                    st.rerun()
            else:
                st.caption('Välj 10, 20 eller 30 matcher för att fortsätta till lagvalet.')
        elif x['matches'] >= x['limit']:
            st.subheader('Omgången är klar!')
            st.write(f"**{x['team']} · {x['limit']} matcher**")
            st.metric('Slutpoäng', f"{x['points']:+,}")
            st.write(f"{x['wins']} vinster · {x['draws']} oavgjorda · {x['matches']-x['wins']-x['draws']} förluster")
            with st.expander('Alla matcher i omgången'):
                render_table(pd.DataFrame(x['history']), 'kick_round_history')
            if st.button('Ny omgång', type='primary', use_container_width=True):
                st.session_state.pop('kick', None)
                st.session_state.pop('pending_result', None)
                st.rerun()
        else:
            own = x['team']
            st.markdown(f'<div class="season-summary"><span class="season-details"><strong>{escape(own)}</strong>'
                        f'<span>Låst lag · Match {x["matches"]+1} av {x["limit"]}</span></span>'
                        f'<span class="streak">{x["points"]:+d} poäng</span></div>', unsafe_allow_html=True)
            st.progress(x['matches']/x['limit'], text=f"Spelade matcher: {x['matches']} / {x['limit']}")
            opp=st.selectbox('Motståndare',[t for t in teams if t!=own],format_func=team_label, key='kick_opponent')
            venue=st.segmented_control('Spelplats',['Hemma','Borta'], default='Hemma', required=True,
                                       format_func=lambda value: f':material/{"home" if value == "Hemma" else "flight_takeoff"}: {value}',
                                       key='kick_venue')
            h,a=(own,opp) if venue=='Hemma' else (opp,own)
            st.caption(f'Du spelar {"hemma" if venue == "Hemma" else "borta"} med {own}. Hemmalaget visas till vänster.')
            states=load_history() if (ROOT/'history_last5.csv').exists() else starting_states()
            if not (ROOT/'history_last5.csv').exists():
                for t,r in initial.iterrows():
                    states[t]['form']=[0]*4+[int(r.Form5)]
                    states[t]['goals']=[0]*4+[int(r.GoalDiff5)]
            probs=predict(h,a,states)
            pw=probs['H'] if venue=='Hemma' else probs['A']
            reward=round(100/pw); penalty=round(100*pw)
            st.markdown(f'<div class="match-note scoring-note"><span class="score-win"><small>VINST</small><strong>+{reward}</strong></span>'
                        f'<span class="score-draw"><small>OAVGJORT</small><strong>0</strong></span>'
                        f'<span class="score-loss"><small>FÖRLUST</small><strong>−{penalty}</strong></span></div>',
                        unsafe_allow_html=True)
            with st.container(key='kick_match_card'):
                match_card(h,a,'VS','KICK-OFF · DIN MATCH',details={h: kickoff_details(h), a: kickoff_details(a)})
                _, action, _ = st.columns([1,2,1])
                play_match = action.button('Spela match', icon=':material/play_arrow:', type='primary',
                    use_container_width=True, disabled=bool(st.session_state.get('pending_result')))
            probability_cards(h,a,probs,decimals=1)
            if play_match:
                result=str(np.random.choice(list(model.classes_),p=[probs[k] for k in model.classes_]))
                won=(result=='H' and venue=='Hemma') or (result=='A' and venue=='Borta')
                delta=reward if won else 0 if result=='D' else -penalty
                x['matches']+=1; x['wins']+=int(won); x['draws']+=int(result=='D'); x['points']+=delta
                x['last']=(result,delta,won)
                outcome_label = 'Vinst' if won else 'Oavgjort' if result == 'D' else 'Förlust'
                x['history'].append({'Match': x['matches'], 'Hemma': h, 'Borta': a,
                                     'Utfall': outcome_label, 'Poäng': delta, 'Totalpoäng': x['points']})
                if shared_store is not None:
                    event = kick_event(h,a,result,probs,x.get('model_version', current_model_version))
                    st.session_state.setdefault('statistics_outbox', []).append(event)
                    x.setdefault('event_ids', []).append(event['id'])
                    if x['matches'] == x['limit'] and x.get('run_id') and len(x['event_ids']) == x['limit']:
                        st.session_state.setdefault('pending_kick_rounds', []).append(dict(
                            id=x['run_id'], team=own, length=x['limit'], version=x['model_version'],
                            match_ids=list(x['event_ids'])))
                st.session_state['pending_result'] = dict(mode='kick', home=h, away=a, outcome=result,
                    result=outcome_label, delta=delta, points=x['points'], match_number=x['matches'],
                    limit=x['limit'], finished=x['matches'] == x['limit'])
                st.rerun()
            if x['last']:
                result,delta,won=x['last']
                (st.success if won else st.warning if result=='D' else st.error)(f"{'Vinst' if won else 'Oavgjort' if result=='D' else 'Förlust'} · {delta:+d} poäng")
    with right:
        with st.container(key='kick_highscores'):
            st.subheader(':material/emoji_events: Highscore')
            st.caption('Högsta slutpoäng · alla spelare · aktuell modell')
            if shared_store is None:
                st.info('Highscore kräver databasanslutning. Lokalt behöver Supabase-inställningarna finnas i .streamlit/secrets.toml, precis som i Streamlit Cloud.')
            else:
                try:
                    records = load_highscores(shared_store.url, current_model_version, shared_store)
                    for count in (10,20,30):
                        record = next((r for r in records if r['length'] == count), None)
                        st.metric(f'{count} matcher', f'{record["score"]:+d} poäng' if record else '–')
                        st.caption(record['team'] if record else 'Inget rekord ännu – spela klart en omgång.')
                except StatisticsError:
                    st.warning('Highscore kunde inte hämtas. Kontrollera anslutningen och att SQL-uppdatering 002 är körd.')
            if st.button('Uppdatera highscore', key='refresh_highscores'):
                load_highscores.clear()
                st.rerun()
            st.caption('Endast avslutade omgångar registreras. Din omgång måste ha spelats med databasanslutning från första matchen.')
        st.subheader('Din omgång')
        if x is not None:
            st.metric('Totalpoäng',f"{x['points']:+,}")
            b1,b2=st.columns(2); b1.metric('Matcher', f"{x['matches']} / {x['limit']}"); b2.metric('Vinster',x['wins'])
            if x['matches'] < x['limit']:
                with st.expander('Avsluta omgången i förtid'):
                    st.caption('Omgångens totalpoäng nollställs. Redan sparade matcher finns kvar i den gemensamma statistiken.')
                    if st.button('Avbryt omgång', key='cancel_kick'):
                        st.session_state.pop('kick',None)
                        st.session_state.pop('pending_result',None)
                        st.rerun()
        else:
            st.caption('Välj omgångslängd och lag för att börja. Jämför totalpoäng från lika långa omgångar.')
else:
    missing=[f for f in ('fixtures_2017.csv','historical_scores.csv','history_last5.csv') if not (ROOT/f).exists()]
    if missing:
        st.error('Säsongsläget behöver exportfilerna: '+', '.join(missing)+'. Kör exportcellen i den bifogade instruktionen.')
        st.stop()
    try:
        fixtures=csv_load('fixtures_2017.csv')
        fixtures['Date']=pd.to_datetime(fixtures['Date'])
        fixtures=fixtures.sort_values(['Date','HomeTeam','AwayTeam']).reset_index(drop=True)
        pools=score_pools()
        if len(fixtures)!=380 or set(fixtures.HomeTeam).union(fixtures.AwayTeam)!=set(teams):
            st.error('Spelschemat måste ha 380 matcher och samma 20 lag som lagfilen.');st.stop()
    except Exception as e:
        st.error(f'Fel i säsongsunderlaget: {e}');st.stop()
    dates=list(fixtures['Date'].drop_duplicates())
    if 'season' not in st.session_state: st.session_state.season=None
    summary_slot=None
    left,right=st.columns([1.6,1],gap='large')
    with left:
        st.header('Säsong 2017/18')
        if st.session_state.season is None:
            selected=st.selectbox('Ditt lag',teams,key='season_team')
            team_preview(selected, float(initial.loc[selected, 'Elo']))
            st.caption('Elo: högre värde innebär högre uppskattad lagstyrka.')
            st.write('Spela ditt lags 38 matcher från augusti till maj. Övriga ligamatcher simuleras automatiskt och resultaten finns kvar att läsa.')
            if st.button('Starta säsong',type='primary',use_container_width=True):
                st.session_state.season={'team':selected,'date_idx':0,'states':load_history(),
                                         'table':empty_table(),'history':[], 'last_match':None,
                                         'run_id':new_run_id(),'model_version':current_model_version,
                                         'rng_seed':int(np.random.default_rng().integers(1,2**31))}
                advance_to_own_match(st.session_state.season,fixtures,dates,pools)
                st.rerun()
        else:
            s=st.session_state.season
            advance_to_own_match(s,fixtures,dates,pools)
            played=s['table'][s['team']]['P']
            total=int((fixtures.HomeTeam.eq(s['team']) | fixtures.AwayTeam.eq(s['team'])).sum())
            place=int(standings(s['table']).loc[lambda df: df.Lag.eq(s['team']),'#'].iloc[0])
            streak=winning_streak(season_results(s['history'])[s['team']])
            st.markdown(f'<div class="season-summary"><span class="season-details"><strong>{escape(s["team"])}</strong>'
                        f'<span class="season-standing">Plats {place} · {s["table"][s["team"]]["Pts"]} poäng</span></span>'
                        f'<span class="streak">{streak} raka vinster</span></div>',
                        unsafe_allow_html=True)
            recent = season_results(s['history'])[s['team']][-5:]
            markers = ''.join(f'<span class="form-dot form-{r}" title="{r}">{r}</span>' for r in recent)
            if recent:
                st.markdown(f'<div class="season-form">Senaste fem · äldst till vänster <span class="form-strip">{markers}</span></div>', unsafe_allow_html=True)
            st.caption(f"Dina matcher: {played} / {total}")
            champion, _ = title_status(s['table'], s['team'], fixtures)
            if champion:
                st.success('Seriesegrare! Pokalen är säkrad.')
            elif s['date_idx'] == len(dates) and place >= 18:
                st.warning(relegation_notice(place, finished=True))
            st.markdown(f'<div class="season-track" role="progressbar" aria-label="Spelade matcher" '
                        f'aria-valuemin="0" aria-valuemax="{total}" aria-valuenow="{played}">'
                        f'<div class="season-fill" style="width:{100*played/total:.2f}%"></div></div>',
                        unsafe_allow_html=True)
            paused_batch=st.session_state.get('quick_batch')
            if paused_batch:
                has_remaining=paused_batch['remaining'] > 0 and s['date_idx'] < len(dates)
                st.caption('Snabbsimuleringen pausades för pokalfirandet.')
                if st.button('Fortsätt snabbsimuleringen' if has_remaining else 'Visa snabbsammanfattning', key='resume_quick'):
                    st.session_state['quick_request']='continue' if has_remaining else 'summary'
                    st.rerun()
                if st.button('Avsluta snabbspåret', key='cancel_quick'):
                    st.session_state.pop('quick_batch',None)
                    st.rerun()
            if s['date_idx']<len(dates):
                date=dates[s['date_idx']]
                day=fixtures.loc[fixtures.Date.eq(date)]
                match_heading,match_action,quick_action=st.columns([1.2,1,1],vertical_alignment='center')
                match_heading.subheader('Nästa match')
                with match_action:
                    if st.button('Spela match',type='primary',use_container_width=True,disabled=bool(paused_batch)):
                        st.session_state['pending_result'] = play_next_match(s,fixtures,dates,pools)
                        st.rerun()
                with quick_action:
                    if st.button('Snabbsimulera',use_container_width=True,disabled=bool(paused_batch)):
                        st.session_state['show_quick_setup']=total-played
                        st.rerun()
                for _,row in day.iterrows():
                    h,a=row.HomeTeam,row.AwayTeam
                    if s['team'] in (h,a):
                        p=predict(h,a,s['states'])
                        opponent=a if s['team']==h else h
                        st.markdown(f'<span class="match-context">{escape(match_context(s["table"], s["team"], opponent, fixtures))}</span>',
                                    unsafe_allow_html=True)
                        match_card(h,a,'VS',date.strftime('%d/%m %Y'))
                        probability_cards(h,a,p)
                        render_match_analysis(s['team'],opponent,s['team']==h,p,s['states'],s['table'])
                        st.caption('Förhandstexten bygger på modellens prognos och lagens fem senaste matcher.')
            else:
                final=standings(s['table'],s['team'])
                place=int(final.loc[final.Lag.eq('⭐ '+s['team']),'#'].iloc[0])
                st.success(f"Säsongen är klar! **{s['team']} slutade på plats {place} av 20.**")
                summary_slot=st.container()
                if st.button('Starta ny säsong',type='primary',use_container_width=True):
                    reset_season()
                    st.rerun()
            if s.get('last_match'):
                last=s['last_match']
                st.markdown('<div class="history-heading">Resultat och historik</div>', unsafe_allow_html=True)
                st.markdown(f"**Senast · {last['result']}**  \n"
                            f"{last['home']} {last['hg']} – {last['ag']} {last['away']}")
            if s['history']:
                results=pd.DataFrame(s['history'][::-1])
                # The history contains internal probability data for persistence;
                # present match information rather than raw model dictionaries.
                results = results[['Date', 'HomeTeam', 'AwayTeam', 'Score']].rename(columns={
                    'Date': 'Datum', 'HomeTeam': 'Hemma', 'AwayTeam': 'Borta', 'Score': 'Resultat'})
                with st.expander('Övriga ligan · resultat och datum'):
                    render_table(results.loc[~(results.Hemma.eq(s['team']) | results.Borta.eq(s['team']))],
                                 'other_season_results')
                with st.expander('Dina matcher · hela säsongen'):
                    render_table(results.loc[results.Hemma.eq(s['team']) | results.Borta.eq(s['team'])],
                                 'own_season_results')
            with st.expander('Säsongsinställningar'):
                st.caption('En omstart tar bort den pågående säsongens resultat.')
                if st.button('Starta om säsongen'):
                    reset_season();st.rerun()
    with right:
        st.subheader('Premier League')
        s=st.session_state.season
        simulated=s['table'] if s else empty_table()
        try:
            actual=csv_load('actual_results_2017.csv').copy()
            actual['Date']=pd.to_datetime(actual['Date'])
            actual=actual.sort_values(['Date','HomeTeam','AwayTeam'])
            if len(actual) != 380 or actual[['FTHG','FTAG']].isna().any().any():
                raise ValueError('Verkliga resultat måste innehålla 380 kompletta matcher.')
            cutoff=s['history'][-1]['Date'] if s and s['history'] else None
            real_table,real_form=actual_snapshot(actual,cutoff)
            differences=points_difference(simulated,real_table)
            if summary_slot is not None:
                with summary_slot:
                    render_season_comparison(simulated,real_table,s['team'])
            st.caption('Jämförelse till och med '+pd.Timestamp(cutoff).strftime('%d/%m %Y') if cutoff else 'Före säsongsstart')
            simulated_tab,actual_tab=st.tabs(['Din säsong','Faktisk tabell'])
            with simulated_tab:
                render_league_table(simulated,season_results(s['history'] if s else []),
                                    s['team'] if s else None,differences)
            with actual_tab:
                render_league_table(real_table,real_form,s['team'] if s else None,differences)
        except (FileNotFoundError, ValueError, KeyError) as e:
            st.warning(f'Verklig jämförelse är inte tillgänglig: {e}')
            render_league_table(simulated,season_results(s['history'] if s else []),s['team'] if s else None)
        if s:
            st.caption(f"Spelade matcher: {sum(r['P'] for r in s['table'].values())//2} / 380")

# Only an explicit request runs a batch, never a dialog dismissal or ordinary rerun.
quick_request=st.session_state.pop('quick_request',None)
if quick_request is not None and mode == 'Säsong' and st.session_state.season is not None:
    s=st.session_state.season
    batch=st.session_state.get('quick_batch')
    if isinstance(quick_request,int):
        batch=dict(remaining=quick_request,results=[],points_before=s['table'][s['team']]['Pts'],
                   place_before=league_position(s['table'],s['team']) if s['table'][s['team']]['P'] else None)
        st.session_state['quick_batch']=batch
    if batch:
        with st.spinner('Matcherna simuleras…'):
            title_result=simulate_batch(s,fixtures,dates,pools,batch) if quick_request != 'summary' else None
        if title_result is not None:
            st.session_state['pending_result']=title_result
        else:
            st.session_state['pending_quick_summary']=batch
            st.session_state.pop('quick_batch',None)
        st.rerun()

# Keep interactive dialogs until explicitly closed; only one opens in a run.
pending_result = st.session_state.get('pending_result')
pending_quick=st.session_state.get('pending_quick_summary')
quick_setup=st.session_state.get('show_quick_setup')
if pending_result:
    if result_dialog is not None:
        result_dialog(pending_result)
    else:
        with result_fallback:
            show_match_result(pending_result)
elif pending_quick:
    if quick_summary_dialog is not None:
        quick_summary_dialog(pending_quick)
    else:
        with result_fallback:
            show_quick_summary(pending_quick)
elif quick_setup:
    if quick_setup_dialog is not None:
        quick_setup_dialog(quick_setup)
    else:
        with result_fallback:
            show_quick_setup(quick_setup)
