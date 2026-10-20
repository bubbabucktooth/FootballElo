"""Read-only shared statistics UI. Aggregation is performed by PostgreSQL."""
import pandas as pd
import streamlit as st
from html import escape
from ui_tables import render_table as statistics_table
from statistics_store import StatisticsError


@st.cache_data(ttl=30, show_spinner=False)
def load_statistics(endpoint, version, venue, opponent, team, page, _store):
    return _store.statistics(version, venue, opponent, team, page)


def summary_cards(cards):
    """Compact, wrapping summaries with explicit explanations of each measure."""
    content = ''.join(
        '<div class="statistics-summary-card">'
        f'<span class="statistics-summary-label">{escape(label)}</span>'
        f'<strong>{escape(value)}</strong><small>{escape(detail)}</small></div>'
        for label, value, detail in cards)
    st.markdown(f'<div class="statistics-summary">{content}</div>', unsafe_allow_html=True)


def season_summary_cards(rows, completed):
    cards = [('Avslutade säsonger', str(completed), 'Hela ligan · 380 matcher per säsong')]
    played = [row for row in rows if row['Säsonger'] > 0]
    if not completed or not played:
        return cards + [
            ('Vanligaste mästare', '–', 'Visas efter en avslutad säsong'),
            ('Avvikelse i poängsnitt', '–', 'Jämförelse med Premier League 2017/18'),
            ('Största avvikelse', '–', 'Lag vars poängsnitt avviker mest')]
    maximum = max(row['Seriesegrar'] for row in played)
    winners = [row['Lag'] for row in played if row['Seriesegrar'] == maximum]
    if maximum:
        cards.append(('Vanligaste mästare', winners[0] if len(winners) == 1 else 'Delad förstaplats',
                      f'{100 * maximum / completed:.1f} % av säsongerna'.replace('.', ',')
                      + (f' vardera · {", ".join(winners)}' if len(winners) > 1
                         else f' · {maximum} {"titel" if maximum == 1 else "titlar"}')))
    else:
        cards.append(('Vanligaste mästare', '–', 'Ingen seriesegrare i underlaget'))
    differences = [abs(row['Δ poängsnitt']) for row in played]
    mean_error = sum(differences) / len(differences)
    cards.append(('Avvikelse i poängsnitt', f'{mean_error:.1f} p'.replace('.', ','),
                  f'Absolut skillnad mot verkligheten · medel över {len(played)} lag'))
    largest = max(differences)
    surprises = [row for row in played if abs(abs(row['Δ poängsnitt']) - largest) < 1e-9]
    if largest < 1e-9:
        cards.append(('Största avvikelse', '0,0 p', 'Alla lagens poängsnitt matchar verkligheten'))
    else:
        detail = ' · '.join(f'{row["Lag"]} {row["Δ poängsnitt"]:+.1f} p'.replace('.', ',') for row in surprises)
        cards.append(('Största avvikelse', f'{largest:.1f} p'.replace('.', ','), detail))
    return cards


def kick_summary_cards(data):
    total = int(data.get('kick_total', 0))
    cards = [('Registrerade matcher', str(total), 'Unika matcher · valda filter')]
    summary = data.get('kick_summary')
    if not total:
        return cards + [('Utfall 1 / X / 2', '–', 'Inga matcher för valda filter'),
                        ('Favoritutfallet träffade', '–', 'Visas när matcher har registrerats')]
    if not summary:
        return cards + [('Utfall 1 / X / 2', '–', 'Sammanställning inte tillgänglig ännu'),
                        ('Favoritutfallet träffade', '–', 'Sammanställning inte tillgänglig ännu')]
    shares = [100 * int(summary[key]) / total for key in ('home_wins', 'draws', 'away_wins')]
    cards.append(('Utfall 1 / X / 2', ' / '.join(f'{value:.0f}' for value in shares) + ' %',
                  'Hemmavinst / Oavgjort / Bortavinst'))
    correct = int(summary['correct_predictions'])
    cards.append(('Favoritutfallet träffade', f'{100 * correct / total:.1f} %'.replace('.', ','),
                  f'{correct} av {total} · utfallet med högst modellsannolikhet'))
    return cards


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


def statistics_data(store, version, venue=None, opponent=None, team=None, page=0, *, retry_key='retry_statistics_data'):
    if store is None:
        return {}
    try:
        return load_statistics(store.url, version, venue, opponent, team, page, store)
    except StatisticsError as error:
        st.warning(str(error))
        st.caption('Statistiken kan inte visas just nu. Befintliga resultat finns kvar i databasen.')
        retry_statistics(retry_key)
        return None


def retry_statistics(key):
    if st.button('Försök igen', key=key, icon=':material/refresh:'):
        load_statistics.clear()
        st.rerun()


def render_statistics(store, teams, current_version, actual_points):
    st.header('Gemensam statistik')
    if store is None:
        st.info('Gemensam statistik är inte tillgänglig lokalt. Du kan spela som vanligt, '
                'men resultaten sparas inte i den gemensamma historiken.')
        with st.expander('Teknisk information'):
            st.write('Anslut Supabase via .streamlit/secrets.toml för att aktivera gemensam statistik och highscore.')
        return
    st.caption('Alla besökares sparade körningar. Data uppdateras inom 30 sekunder. '
               'Resultaten beskriver simuleringar, inte verkliga spelade matcher.')
    known_versions = []
    if store is not None:
        try:
            metadata = load_statistics(store.url, current_version, None, None, None, 0, store)
            known_versions = [v for v in metadata.get('versions', []) if v != current_version]
        except StatisticsError as error:
            st.warning(str(error))
            retry_statistics('retry_statistics_metadata')
            return
    kick_tab, season_tab = st.tabs(['Kick-off-statistik', 'Säsongsstatistik'])
    with kick_tab:
        with st.container(key='statistics_filters_kick'):
            filters, refresh, active = st.columns([1, 1.4, 4], vertical_alignment='center')
            with filters.popover('Filter', icon=':material/tune:', use_container_width=True):
                selected_version = version_filter(known_versions, current_version, 'stats_kick_version')
                venue_col, opponent_col = st.columns(2)
                venue = venue_col.selectbox('Spelplats', ['Alla', 'Hemma', 'Borta'], key='stats_venue')
                opponent = opponent_col.selectbox('Motståndare', ['Alla'] + teams, key='stats_opponent')
            if refresh.button('Uppdatera', key='refresh_statistics', icon=':material/refresh:', use_container_width=True):
                load_statistics.clear()
            active.caption(f'{st.session_state.stats_kick_version} · '
                           f'{"Alla spelplatser" if venue == "Alla" else venue} · '
                           f'{"Alla motståndare" if opponent == "Alla" else opponent}')
        venue_filter = None if venue == 'Alla' else venue
        opponent_filter = None if opponent == 'Alla' else opponent
        data = statistics_data(store, selected_version, venue_filter, opponent_filter, retry_key='retry_kick_statistics')
        league_tab, history_tab = st.tabs(['Lagstatistik', 'Matchhistorik'])
        with league_tab:
            if data is not None:
                summary_cards(kick_summary_cards(data))
                with st.expander('Så räknas nyckeltalen'):
                    st.write('Varje match räknas en gång. Utfallet med högst sannolikhet jämförs med '
                             'det simulerade resultatet. Vid lika sannolikhet prioriteras 1, sedan X, sedan 2. '
                             'Detta är ett mått på simulerade utfall, inte träffsäkerhet på verkliga matcher. '
                             'Utfallsandelarna avrundas och kan därför summera till något annat än 100 %.')
                    if data.get('kick_total', 0) and not data.get('kick_summary'):
                        st.caption('Teknisk information: kör app/sql/003_statistics_summary.sql i Supabase '
                                   'och uppdatera statistiken för att aktivera sammanställningen.')
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
                                 {'Vinst %': '{:.1f}', 'Förväntade vinster': '{:.1f}', 'Vinster − förväntat': '{:+.1f}'}, compact=True)
                st.caption('Båda lagen får ett resultat per match. Förväntade vinster är summan av modellens '
                           'vinstsannolikheter. Spelets bonuspoäng ingår inte här.')
        with history_tab:
            history_team = st.selectbox('Lag för matchhistorik', teams, key='stats_history_team')
            filters = (selected_version, venue, opponent, history_team)
            if st.session_state.get('stats_filters') != filters:
                st.session_state.stats_page = 0
                st.session_state.stats_filters = filters
            page = st.session_state.get('stats_page', 0)
            data = statistics_data(store, selected_version, venue_filter, opponent_filter, history_team, page,
                                   retry_key='retry_kick_history')
            if data is not None:
                st.subheader(f'Matchhistorik · {history_team}')
                history = data.get('history', [])
                if history:
                    labels = {'H': 'Hemmavinst', 'D': 'Oavgjort', 'A': 'Bortavinst'}
                    records = [{'Tid (UTC)': r['occurred_at'], 'Hemma': r['home'], 'Borta': r['away'],
                                'Utfall': labels[r['outcome']], '1 %': 100*float(r['p_h']),
                                'X %': 100*float(r['p_d']), '2 %': 100*float(r['p_a'])} for r in history]
                    statistics_table(pd.DataFrame(records), 'kick_history',
                                     formats={'1 %': '{:.1f}', 'X %': '{:.1f}', '2 %': '{:.1f}'}, compact=True, descending=True)
                    st.caption('Sortering gäller matcherna på den här sidan. Senaste matcherna visas på sida 1.')
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
        with st.container(key='statistics_filters_season'):
            filters, refresh, active = st.columns([1, 1.4, 4], vertical_alignment='center')
            with filters.popover('Filter', icon=':material/tune:', use_container_width=True):
                selected_version = version_filter(known_versions, current_version, 'stats_season_version')
            if refresh.button('Uppdatera', key='refresh_season_statistics', icon=':material/refresh:', use_container_width=True):
                load_statistics.clear()
            active.caption(st.session_state.stats_season_version)
        data = statistics_data(store, selected_version, retry_key='retry_season_statistics')
        if data is not None:
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
            summary_cards(season_summary_cards(rows, data.get('completed_seasons', 0)))
            with st.expander('Så räknas säsongsmåtten'):
                st.write('Avvikelse i poängsnitt är medelvärdet av de absoluta skillnaderna mellan varje lags '
                         'simulerade poängsnitt och lagets verkliga poäng 2017/18. Det är inte medelfelet '
                         'för enskilda säsonger. Största avvikelse visar laget eller lagen med störst '
                         'sådan skillnad. Plus betyder fler poäng än i verkligheten, minus färre.')
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
                                  'Δ poängsnitt': '{:+.1f}', 'Bäst': '{:.0f}', 'Sämst': '{:.0f}'}, compact=True)
                st.caption('Endast fullständiga säsonger med 380 matcher räknas här. Alla 20 lag ingår i varje '
                           'säsong, oavsett vilket lag besökaren valde. Lika vanliga slutplaceringar visas tillsammans.')
                st.caption('Topp 4 (%) = andelen avslutade simuleringar där laget slutat på plats 1–4. '
                           'Exempel: 8 av 10 säsonger ger 80 %. Nedflyttning (%) gäller på samma sätt plats 18–20.')
