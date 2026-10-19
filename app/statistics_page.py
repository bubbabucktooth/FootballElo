"""Read-only shared statistics UI. Aggregation is performed by PostgreSQL."""
import pandas as pd
import streamlit as st
from ui_tables import render_table as statistics_table
from statistics_store import StatisticsError


@st.cache_data(ttl=30, show_spinner=False)
def load_statistics(endpoint, version, venue, opponent, team, page, _store):
    return _store.statistics(version, venue, opponent, team, page)


def season_overview_charts(rows, completed):
    st.subheader('Säsongen i diagram')
    st.caption(f'Underlag: {completed} avslutade säsonger för vald modellversion. '
               'Med få simuleringar kan skillnader främst bero på slumpen.')
    if not completed:
        st.info('Spela klart en säsong för att se poängavvikelser och titelandelar.')
        return
    frame = pd.DataFrame([r for r in rows if r['Säsonger'] > 0])
    if frame.empty:
        st.info('Inga lagresultat finns för det valda underlaget.')
        return
    frame['Titelandel (%)'] = 100 * frame['Seriesegrar'] / frame['Säsonger']
    presentation = {
        'background': '#201831', 'height': max(160, 23 * len(frame)),
        'padding': {'top': 20, 'right': 20, 'bottom': 20, 'left': 20},
        'config': {'font': 'Poppins, Segoe UI, sans-serif', 'view': {'stroke': None},
                   'axis': {'labelColor': '#d6c9e8', 'titleColor': '#f4f1fc',
                            'gridColor': '#ffffff12', 'domainColor': '#665180'}}}
    st.markdown('**Poängsnitt jämfört med verkligheten**')
    st.caption('Grönt: fler poäng än i Premier League 2017/18. Rött: färre. '
               'Staplarna jämför simuleringarnas poängsnitt med den verkliga säsongen.')
    st.vega_lite_chart(frame, {**presentation, 'layer': [
        {'mark': {'type': 'bar', 'cornerRadiusEnd': 3}, 'encoding': {
            'y': {'field': 'Lag', 'type': 'nominal', 'sort': '-x', 'title': None,
                  'axis': {'labelLimit': 150}},
            'x': {'field': 'Δ poängsnitt', 'type': 'quantitative', 'title': 'Poängskillnad'},
            'color': {'condition': {'test': 'datum["Δ poängsnitt"] >= 0', 'value': '#97e4b9'},
                      'value': '#ee7990'},
            'tooltip': [{'field': 'Lag', 'type': 'nominal'},
                        {'field': 'Poängsnitt', 'type': 'quantitative', 'format': '.1f'},
                        {'field': 'Verkliga poäng', 'type': 'quantitative', 'format': 'd'},
                        {'field': 'Δ poängsnitt', 'type': 'quantitative', 'format': '+.1f'},
                        {'field': 'Säsonger', 'type': 'quantitative', 'format': 'd'}]}},
        {'mark': {'type': 'rule', 'color': '#d6c9e8'},
         'encoding': {'x': {'datum': 0, 'type': 'quantitative'}}}
    ]}, use_container_width=True, theme=None)
    st.markdown('**Vilka lag vinner ligan?**')
    st.caption('Andel avslutade säsonger där varje lag blev seriesegrare. '
               'Detta är observerade simuleringsresultat, inte modellens prognos för nästa säsong.')
    st.vega_lite_chart(frame, {**presentation,
        'mark': {'type': 'bar', 'color': '#a687eb', 'cornerRadiusEnd': 3},
        'encoding': {
            'y': {'field': 'Lag', 'type': 'nominal', 'sort': '-x', 'title': None,
                  'axis': {'labelLimit': 150}},
            'x': {'field': 'Titelandel (%)', 'type': 'quantitative',
                  'scale': {'domain': [0, 100]}, 'title': 'Andel ligatitlar (%)'},
            'tooltip': [{'field': 'Lag', 'type': 'nominal'},
                        {'field': 'Seriesegrar', 'type': 'quantitative', 'format': 'd'},
                        {'field': 'Säsonger', 'type': 'quantitative', 'format': 'd'},
                        {'field': 'Titelandel (%)', 'type': 'quantitative', 'format': '.1f'}]}
    }, use_container_width=True, theme=None)


def version_filter(known_versions, current_version, key):
    version = st.selectbox('Modellversion', ['Aktuell modell', 'Alla modellversioner'] + known_versions, key=key)
    selected = current_version if version == 'Aktuell modell' else None if version == 'Alla modellversioner' else version
    if selected is None:
        st.caption('Olika modellversioner kan ha olika förutsättningar. Jämför dem med försiktighet.')
    return selected


def statistics_data(store, version, venue=None, opponent=None, team=None, page=0):
    if store is None:
        return {}
    try:
        return load_statistics(store.url, version, venue, opponent, team, page, store)
    except StatisticsError as error:
        st.warning(str(error))
        st.caption('Statistiken kan inte visas just nu. Befintliga resultat finns kvar i databasen.')
        return None


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
    kick_tab, season_tab = st.tabs(['Kick-off-statistik', 'Säsongsstatistik'])
    with kick_tab:
        selected_version = version_filter(known_versions, current_version, 'stats_kick_version')
        venue_col, opponent_col = st.columns(2)
        venue = venue_col.selectbox('Spelplats', ['Alla', 'Hemma', 'Borta'], key='stats_venue')
        opponent = opponent_col.selectbox('Motståndare', ['Alla'] + teams, key='stats_opponent')
        if st.button('Uppdatera Kick-off-statistik', key='refresh_statistics'):
            load_statistics.clear()
        venue_filter = None if venue == 'Alla' else venue
        opponent_filter = None if opponent == 'Alla' else opponent
        data = statistics_data(store, selected_version, venue_filter, opponent_filter)
        league_tab, history_tab = st.tabs(['Lagstatistik', 'Matchhistorik'])
        with league_tab:
            if data is not None:
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
                statistics_table(pd.DataFrame(rows), 'kick_statistics', None,
                                 {'Vinst %': '{:.1f}', 'Förväntade vinster': '{:.1f}', 'Vinster − förväntat': '{:+.1f}'})
                st.caption('Båda lagen får ett resultat per match. Förväntade vinster är summan av modellens '
                           'vinstsannolikheter. Spelets bonuspoäng ingår inte här.')
        with history_tab:
            history_team = st.selectbox('Lag för matchhistorik', teams, key='stats_history_team')
            filters = (selected_version, venue, opponent, history_team)
            if st.session_state.get('stats_filters') != filters:
                st.session_state.stats_page = 0
                st.session_state.stats_filters = filters
            page = st.session_state.get('stats_page', 0)
            data = statistics_data(store, selected_version, venue_filter, opponent_filter, history_team, page)
            if data is not None:
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
        selected_version = version_filter(known_versions, current_version, 'stats_season_version')
        if st.button('Uppdatera säsongsstatistik', key='refresh_season_statistics'):
            load_statistics.clear()
        data = statistics_data(store, selected_version)
        if data is not None:
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
                             'Topp 4 (%)': 100*int(r.get('top_four', 0))/n if n else None,
                             'Nedflyttning %': 100*int(r.get('relegations', 0))/n if n else None,
                             'Poängsnitt': points, 'Verkliga poäng': actual_points[team],
                             'Δ poängsnitt': points-actual_points[team] if n else None})
            overview_tab, table_tab = st.tabs(['Översikt', 'Tabell'])
            with overview_tab:
                season_overview_charts(rows, data.get('completed_seasons', 0))
                history_team = st.selectbox('Lag för placeringsfördelning', teams, key='stats_distribution_team')
                st.subheader(f'Placeringsfördelning · {history_team}')
                frequencies = {int(r['position']): int(r['count']) for r in data.get('distribution', []) if r['team'] == history_team}
                if frequencies:
                    st.caption(f'Underlag för {history_team}: {sum(frequencies.values())} avslutade säsonger.')
                    chart = pd.DataFrame({'Placering': list(range(1, 21)),
                                          'Säsonger': [frequencies.get(i, 0) for i in range(1, 21)]})
                    # Inline values make the selected team's data part of the spec itself.
                    # This avoids stale Arrow dataset reuse in the browser on team changes.
                    st.vega_lite_chart({
                        'background': '#201831',
                        'padding': {'top': 20, 'right': 20, 'bottom': 20, 'left': 20},
                        'layer': [{'data': {'values': chart.to_dict('records')},
                        'mark': {'type': 'bar', 'color': '#a687eb', 'cornerRadiusTopLeft': 3, 'cornerRadiusTopRight': 3},
                        'encoding': {
                            'x': {'field': 'Placering', 'type': 'ordinal', 'sort': list(range(1, 21)),
                                  'axis': {'labelAngle': 0}},
                            'y': {'field': 'Säsonger', 'type': 'quantitative', 'axis': {'tickMinStep': 1}},
                            'tooltip': [{'field': 'Placering', 'type': 'ordinal'},
                                        {'field': 'Säsonger', 'type': 'quantitative', 'format': 'd'}]}}],
                        'config': {'font': 'Poppins, Segoe UI, sans-serif', 'view': {'stroke': None},
                                   'axis': {'labelColor': '#d6c9e8', 'titleColor': '#f4f1fc',
                                            'gridColor': '#ffffff12', 'domainColor': '#665180'}}
                    }, use_container_width=True, theme=None,
                        key=f'placement_distribution_{selected_version}_{history_team}')
                    with st.expander('Visa placeringarna som siffror'):
                        statistics_table(chart, 'placement_values')
                else:
                    st.write('Ingen avslutad säsong har registrerats ännu.')
            with table_tab:
                statistics_table(pd.DataFrame(rows), 'season_statistics', None,
                                 {**{k: '{:.1f}' for k in ('Snittplacering', 'Topp 4 (%)', 'Nedflyttning %', 'Poängsnitt')},
                                  'Δ poängsnitt': '{:+.1f}', 'Bäst': '{:.0f}', 'Sämst': '{:.0f}'})
                st.caption('Endast fullständiga säsonger med 380 matcher räknas här. Alla 20 lag ingår i varje '
                           'säsong, oavsett vilket lag besökaren valde. Lika vanliga slutplaceringar visas tillsammans.')
                st.caption('Topp 4 (%) = andelen avslutade simuleringar där laget slutat på plats 1–4. '
                           'Exempel: 8 av 10 säsonger ger 80 %. Nedflyttning (%) gäller på samma sätt plats 18–20.')
