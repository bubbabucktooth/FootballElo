"""Read-only shared statistics UI. Aggregation is performed by PostgreSQL."""
import pandas as pd
import streamlit as st
from html import escape
from statistics_store import StatisticsError


def statistics_table(frame, key, selected=None, formats=None):
    """Use the league table's presentation with explicit sorting and CSV export."""
    formats = formats or {}
    with st.expander('Sortera och exportera tabellen'):
        column = st.selectbox('Sortera efter', list(frame.columns), key=f'{key}_sort')
        descending = st.checkbox('Fallande ordning', key=f'{key}_descending')
        st.download_button('Hämta CSV', frame.to_csv(index=False).encode('utf-8-sig'),
                           file_name=f'{key}.csv', mime='text/csv', key=f'{key}_csv')
    ordered = frame.sort_values(column, ascending=not descending, na_position='last', kind='stable')
    headers = ''.join(f'<th scope="col">{escape(str(c))}</th>' for c in frame.columns)
    rows = []
    for record in ordered.to_dict('records'):
        cells = []
        for name, value in record.items():
            text = '–' if pd.isna(value) else formats[name].format(value) if name in formats else str(value)
            cells.append(f'<td>{escape(text)}</td>')
        own = 'own-team' if record.get('Lag') == selected else ''
        rows.append(f'<tr class="{own}">' + ''.join(cells) + '</tr>')
    st.markdown('<div class="league-wrap statistics-table-wrap"><table class="league-table statistics-table">'
                '<thead><tr>' + headers + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>',
                unsafe_allow_html=True)


@st.cache_data(ttl=30, show_spinner=False)
def load_statistics(endpoint, version, venue, opponent, team, page, _store):
    return _store.statistics(version, venue, opponent, team, page)


def render_statistics(store, teams, current_version, actual_points):
    st.header('Gemensam statistik')
    st.caption('Alla besökares sparade körningar. Data uppdateras inom 30 sekunder. '
               'Resultaten beskriver simuleringar, inte verkliga spelade matcher.')
    if store is None:
        st.info('Gemensam statistik är inte aktiverad ännu. Spelet fungerar som vanligt. '
                'Databasen behöver anslutas via Streamlits secrets innan historiken kan samlas.')
    known_versions = []
    if store is not None:
        try:
            metadata = load_statistics(store.url, current_version, None, None, None, 0, store)
            known_versions = [v for v in metadata.get('versions', []) if v != current_version]
        except StatisticsError as error:
            st.warning(str(error))
            return
    version = st.selectbox('Modellversion', ['Aktuell modell', 'Alla modellversioner'] + known_versions, key='stats_version')
    selected_version = current_version if version == 'Aktuell modell' else None if version == 'Alla modellversioner' else version
    if selected_version is None:
        st.caption('Olika modellversioner kan ha olika förutsättningar. Jämför dem med försiktighet.')
    venue_col, opponent_col = st.columns(2)
    venue = venue_col.selectbox('Kick-off · spelplats', ['Alla', 'Hemma', 'Borta'], key='stats_venue')
    opponent = opponent_col.selectbox('Kick-off · motståndare', ['Alla'] + teams, key='stats_opponent')
    history_team = st.selectbox('Lag för matchhistorik och placeringsfördelning', teams, key='stats_team')
    filters = (selected_version, venue, opponent, history_team)
    if st.session_state.get('stats_filters') != filters:
        st.session_state.stats_page = 0
        st.session_state.stats_filters = filters
    page = st.session_state.get('stats_page', 0)
    if st.button('Uppdatera statistiken', key='refresh_statistics'):
        load_statistics.clear()
    data = {}
    if store is not None:
        try:
            data = load_statistics(store.url, selected_version, None if venue == 'Alla' else venue,
                                   None if opponent == 'Alla' else opponent, history_team, page, store)
        except StatisticsError as error:
            st.warning(str(error))
            st.caption('Statistiken kan inte visas just nu. Befintliga resultat finns kvar i databasen.')
            return
    kick_tab, season_tab = st.tabs(['Kick-off-statistik', 'Säsongsstatistik'])
    with kick_tab:
        st.metric('Registrerade matcher · valda filter', data.get('kick_total', 0))
        indexed = {r['team']: r for r in data.get('kick', [])}
        rows = []
        for team in teams:
            r = indexed.get(team, {})
            n, wins = int(r.get('played', 0)), int(r.get('wins', 0))
            expected = float(r.get('expected_wins', 0))
            rows.append({'Lag': team, 'Matcher': n, 'V': wins, 'O': int(r.get('draws', 0)),
                         'F': int(r.get('losses', 0)), 'Vinst %': 100*wins/n if n else None,
                         'Förväntade vinster': expected if n else None,
                         'Vinster − förväntat': wins-expected if n else None})
        statistics_table(pd.DataFrame(rows), 'kick_statistics', history_team,
                         {'Vinst %': '{:.1f}', 'Förväntade vinster': '{:.1f}', 'Vinster − förväntat': '{:+.1f}'})
        st.caption('Båda lagen får ett resultat per match. Förväntade vinster är summan av modellens '
                   'vinstsannolikheter. Spelets bonuspoäng ingår inte här.')
        st.subheader(f'Matchhistorik · {history_team}')
        history = data.get('history', [])
        if history:
            labels = {'H': 'Hemmavinst', 'D': 'Oavgjort', 'A': 'Bortavinst'}
            records = [{'Tid (UTC)': r['occurred_at'], 'Hemma': r['home'], 'Borta': r['away'],
                        'Utfall': labels[r['outcome']], '1 %': 100*float(r['p_h']),
                        'X %': 100*float(r['p_d']), '2 %': 100*float(r['p_a'])} for r in history]
            statistics_table(pd.DataFrame(records), 'kick_history',
                             formats={'1 %': '{:.1f}', 'X %': '{:.1f}', '2 %': '{:.1f}'})
        else:
            st.write('Inga registrerade matcher för de valda filtren ännu.')
        total = data.get('history_total', 0)
        st.caption(f'Sida {page+1} av {max(1, (total+49)//50)} · {total} matcher')
        prev, next_page = st.columns(2)
        if prev.button('Föregående sida', disabled=page == 0):
            st.session_state.stats_page = page-1
            st.rerun()
        if next_page.button('Nästa sida', disabled=(page+1)*50 >= total):
            st.session_state.stats_page = page+1
            st.rerun()
    with season_tab:
        st.metric('Avslutade säsonger', data.get('completed_seasons', 0))
        indexed = {r['team']: r for r in data.get('seasons', [])}
        rows = []
        for team in teams:
            r = indexed.get(team, {})
            n = int(r.get('seasons', 0))
            points = float(r['avg_points']) if n else None
            rows.append({'Lag': team, 'Säsonger': n, 'Snittplacering': float(r['avg_position']) if n else None,
                         'Vanligaste placering': ', '.join(map(str, r.get('common_positions', []))) or '–',
                         'Bäst': r.get('best_position'), 'Sämst': r.get('worst_position'),
                         'Seriesegrar': int(r.get('titles', 0)),
                         'Topp 4 %': 100*int(r.get('top_four', 0))/n if n else None,
                         'Nedflyttning %': 100*int(r.get('relegations', 0))/n if n else None,
                         'Poängsnitt': points, 'Verkliga poäng': actual_points[team],
                         'Δ poängsnitt': points-actual_points[team] if n else None})
        statistics_table(pd.DataFrame(rows), 'season_statistics', history_team,
                         {**{k: '{:.1f}' for k in ('Snittplacering', 'Topp 4 %', 'Nedflyttning %', 'Poängsnitt')},
                          'Δ poängsnitt': '{:+.1f}', 'Bäst': '{:.0f}', 'Sämst': '{:.0f}'})
        st.caption('Endast fullständiga säsonger med 380 matcher räknas här. Alla 20 lag ingår i varje '
                   'säsong, oavsett vilket lag besökaren valde. Lika vanliga slutplaceringar visas tillsammans.')
        st.subheader(f'Placeringsfördelning · {history_team}')
        frequencies = {int(r['position']): int(r['count']) for r in data.get('distribution', []) if r['team'] == history_team}
        if frequencies:
            chart = pd.DataFrame({'Placering': list(range(1, 21)),
                                  'Säsonger': [frequencies.get(i, 0) for i in range(1, 21)]})
            st.vega_lite_chart(chart, {
                'background': '#201831',
                'padding': {'top': 20, 'right': 20, 'bottom': 20, 'left': 20},
                'mark': {'type': 'bar', 'color': '#a687eb', 'cornerRadiusTopLeft': 3, 'cornerRadiusTopRight': 3},
                'encoding': {
                    'x': {'field': 'Placering', 'type': 'ordinal', 'sort': list(range(1, 21)),
                          'axis': {'labelAngle': 0}},
                    'y': {'field': 'Säsonger', 'type': 'quantitative', 'axis': {'tickMinStep': 1}},
                    'tooltip': [{'field': 'Placering', 'type': 'ordinal'},
                                {'field': 'Säsonger', 'type': 'quantitative', 'format': 'd'}]},
                'config': {'font': 'Poppins, Segoe UI, sans-serif', 'view': {'stroke': None},
                           'axis': {'labelColor': '#d6c9e8', 'titleColor': '#f4f1fc',
                                    'gridColor': '#ffffff12', 'domainColor': '#665180'}}
            }, use_container_width=True, theme=None)
        else:
            st.write('Ingen avslutad säsong har registrerats ännu.')
        if store is not None:
            st.caption(f'Aktuell modellversion: {current_version}')
