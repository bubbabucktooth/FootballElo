import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import pandas as pd
import altair as alt
from streamlit.testing.v1 import AppTest
from statistics_store import (StatisticsError, SupabaseStore, kick_event, final_rows,
                              season_payload, sync_season, flush_kicks, new_run_id, probabilities)
from statistics_page import season_overview_charts, season_summary_cards, kick_summary_cards
from ui_tables import render_table, mobile_history_cards

ROOT = Path(__file__).parent


def complete_season():
    actual = pd.read_csv(ROOT / 'actual_results_2017.csv')
    history = [dict(Date=r.Date, HomeTeam=r.HomeTeam, AwayTeam=r.AwayTeam,
                    Score=f'{r.FTHG}–{r.FTAG}', probabilities={'H': .5, 'D': .2, 'A': .3})
               for r in actual.itertuples()]
    return dict(run_id=new_run_id(), model_version='test-model', team='Arsenal', history=history)


class MemoryStore:
    """Database substitute to exercise acknowledgements and ambiguous network failures."""
    url = 'https://test.supabase.co/rest/v1/rpc/'

    def __init__(self):
        self.matches, self.completed, self.calls = {}, {}, []
        self.lose_ack = False
        self.lose_round_ack = False

    def ingest(self, payload):
        self.calls.append(copy.deepcopy(payload))
        for event in payload['matches']:
            self.matches.setdefault(event['id'], event)
        if payload.get('season', {}).get('complete'):
            self.completed.setdefault(payload['season']['id'], payload['results'])
        if self.lose_ack:
            self.lose_ack = False
            raise StatisticsError('Lost acknowledgement')
        return {'ok': True}

    def statistics(self, *args):
        return {'kick': [{'team': 'Arsenal', 'played': 2, 'wins': 1, 'draws': 1,
                         'losses': 0, 'expected_wins': 1.2}], 'seasons': [
            {'team': 'Arsenal', 'seasons': 2, 'avg_position': 1.5, 'best_position': 1,
             'worst_position': 2, 'titles': 1, 'top_four': 2, 'relegations': 0,
             'avg_points': 85, 'common_positions': [1,2]}],
            'distribution': [{'team':'Arsenal', 'position':1, 'count':1},
                             {'team':'Arsenal', 'position':2, 'count':1}],
            'history': [], 'kick_total': 2, 'history_total': 0, 'completed_seasons': 2,
            'kick_summary': {'home_wins': 1, 'draws': 1, 'away_wins': 0, 'correct_predictions': 1}}

    def highscores(self, version):
        return []

    def save_round(self, payload):
        self.completed.setdefault(payload['id'], payload)
        if self.lose_round_ack:
            self.lose_round_ack = False
            raise StatisticsError('Lost round acknowledgement')
        return {'ok': True}


class PersistenceTests(unittest.TestCase):
    def test_kick_summary_uses_complete_aggregate_not_history_page(self):
        data = {'kick_total': 100, 'history': [{'outcome': 'A'}],
                'kick_summary': {'home_wins': 50, 'draws': 30, 'away_wins': 20,
                                 'correct_predictions': 61}}
        cards = kick_summary_cards(data)
        self.assertEqual(cards[0][1], '100')
        self.assertEqual(cards[1][1], '50 / 30 / 20 %')
        self.assertEqual(cards[2][1], '61,0 %')
        self.assertEqual(kick_summary_cards({'kick_total': 0})[1][1], '–')
        # Existing databases without migration 003 must remain usable.
        self.assertEqual(kick_summary_cards({'kick_total': 100})[2][1], '–')

    def test_season_summary_handles_ties_and_absolute_average_differences(self):
        rows = [{'Lag': team, 'Säsonger': 4, 'Seriesegrar': 2, 'Δ poängsnitt': delta}
                for team, delta in [('Arsenal', 10), ('Chelsea', -10)]]
        cards = season_summary_cards(rows, 4)
        self.assertEqual(cards[1][1], 'Delad förstaplats')
        self.assertIn('50,0 %', cards[1][2])
        self.assertEqual(cards[2][1], '10,0 p')  # Opposite errors must not cancel.
        self.assertIn('Arsenal +10,0 p', cards[3][2])
        self.assertIn('Chelsea -10,0 p', cards[3][2])
        self.assertTrue(all(c[1] == '–' for c in season_summary_cards([], 0)[1:]))
        for row in rows:
            row['Δ poängsnitt'] = 0
        self.assertEqual(season_summary_cards(rows, 4)[3][1], '0,0 p')

    def test_table_highlight_requires_an_explicit_selected_team(self):
        with patch('ui_tables.st') as ui:
            ui.columns.return_value = (MagicMock(), MagicMock())
            ui.selectbox.return_value = 'Hemma'
            ui.segmented_control.return_value = 'Stigande'
            render_table(pd.DataFrame([{'Hemma': 'Arsenal', 'Borta': 'Chelsea'}]), 'history')
            self.assertNotIn('class="own-team"', ui.markdown.call_args.args[0])
            ui.selectbox.return_value = 'Lag'
            render_table(pd.DataFrame([{'Lag': 'Arsenal'}, {'Lag': 'Chelsea'}]), 'teams', 'Arsenal')
            self.assertEqual(ui.markdown.call_args.args[0].count('class="own-team"'), 1)

    def test_season_charts_values_schema_and_empty_state(self):
        rows = [dict(Lag=team, Säsonger=4, Seriesegrar=titles,
                     Poängsnitt=points, **{'Verkliga poäng': 60, 'Δ poängsnitt': points-60})
                for team, titles, points in [('Arsenal', 3, 70), ('Chelsea', 1, 50)]]
        with patch('statistics_page.st') as ui:
            season_overview_charts(rows, 4)
            self.assertEqual(ui.vega_lite_chart.call_count, 2)
            for call in ui.vega_lite_chart.call_args_list:
                frame, spec = call.args
                self.assertEqual(frame['Titelandel (%)'].tolist(), [75, 25])
                self.assertEqual(frame['Δ poängsnitt'].tolist(), [10, -10])
                alt.Chart.from_dict({**spec, 'data': {'values': frame.to_dict('records')}})
            ui.reset_mock()
            season_overview_charts([], 0)
            ui.vega_lite_chart.assert_not_called()
            ui.info.assert_called_once()

    def test_kick_identity_and_retry_after_lost_ack(self):
        event = kick_event('Arsenal', 'Chelsea', 'H', {'H': .5, 'D': .2, 'A': .3}, 'v1')
        other = kick_event('Arsenal', 'Chelsea', 'H', {'H': .5, 'D': .2, 'A': .3}, 'v1')
        self.assertNotEqual(event['id'], other['id'])
        store, queue = MemoryStore(), [event]
        store.lose_ack = True
        with self.assertRaises(StatisticsError):
            flush_kicks(store, queue)
        self.assertEqual(len(queue), 1)
        flush_kicks(store, queue)
        self.assertEqual(queue, [])
        self.assertEqual(len(store.matches), 1)

    def test_partial_then_complete_season_is_idempotent(self):
        season, store = complete_season(), MemoryStore()
        full = season['history']
        season['history'] = full[:10]
        sync_season(store, season)
        self.assertEqual(season['statistics_cursor'], 10)
        self.assertEqual(len(store.matches), 10)
        self.assertEqual(store.completed, {})
        self.assertFalse(sync_season(store, season))
        season['history'] = full
        store.lose_ack = True
        with self.assertRaises(StatisticsError):
            sync_season(store, season, complete=True)
        self.assertEqual(season['statistics_cursor'], 10)
        sync_season(store, season, complete=True)
        self.assertEqual(len(store.matches), 380)
        self.assertEqual(len(store.completed), 1)
        self.assertFalse(sync_season(store, season, complete=True))
        self.assertEqual(len(store.completed[season['run_id']]), 20)

    def test_deterministic_match_ids_and_final_points(self):
        season = complete_season()
        first, second = season_payload(season, True), season_payload(season, True)
        self.assertEqual(first, second)
        results = {r['team']: r for r in first['results']}
        self.assertEqual(results['Man City']['position'], 1)
        self.assertEqual(results['Man City']['points'], 100)
        self.assertEqual(results['Arsenal']['points'], 63)
        season['run_id'] = new_run_id()
        self.assertNotEqual(first['matches'][0]['id'], season_payload(season)['matches'][0]['id'])

    def test_incomplete_or_duplicate_seasons_cannot_finalize(self):
        history = complete_season()['history']
        with self.assertRaises(ValueError):
            final_rows(history[:-1])
        with self.assertRaises(ValueError):
            final_rows(history[:-1] + [history[0]])
        self.assertFalse(sync_season(MemoryStore(), dict(history=history), complete=True))

    def test_invalid_probabilities_are_rejected(self):
        for values in ({'H': float('nan'), 'D': .2, 'A': .3}, {'H': .9, 'D': .2, 'A': .3}):
            with self.assertRaises(ValueError):
                probabilities(values)

    def test_http_uses_secret_header_and_redacts_failures(self):
        store = SupabaseStore('https://example.supabase.co', 'sb_secret_test-secret')
        response = io.BytesIO(b'{"ok":true}')
        with patch('statistics_store.urlopen', return_value=response) as send:
            store.ingest({'matches': []})
            request = send.call_args.args[0]
            self.assertEqual(request.get_header('Apikey'), 'sb_secret_test-secret')
            self.assertIsNone(request.get_header('Authorization'))
            self.assertEqual(json.loads(request.data), {'payload': {'matches': []}})
        error = HTTPError(store.url, 401, 'sb_secret_test-secret', {}, io.BytesIO(b'secret response'))
        with patch('statistics_store.urlopen', side_effect=error):
            with self.assertRaises(StatisticsError) as caught:
                store.statistics()
        self.assertNotIn('test-secret', str(caught.exception))
        self.assertNotIn('secret response', str(caught.exception))
        with patch('statistics_store.urlopen', return_value=io.BytesIO(b'{"ok":false}')):
            with self.assertRaises(StatisticsError):
                store.ingest({'matches': []})


class StatisticsInterfaceTests(unittest.TestCase):
    def test_mobile_history_shows_short_utc_dates_and_escaped_match_data(self):
        cards = mobile_history_cards([{'Tid (UTC)': '2026-10-20T14:17:26.249018+00:00',
            'Hemma': '<Arsenal>', 'Borta': 'Chelsea', 'Utfall': 'Hemmavinst',
            '1 %': 50, 'X %': 25, '2 %': 25}])
        self.assertIn('20/10 14:17 UTC', cards)
        self.assertIn('&lt;Arsenal&gt;', cards)
        self.assertNotIn('<Arsenal>', cards)
        self.assertIn('<details>', cards)
        self.assertIn('1: 50.0 %', cards)

    def test_match_dates_sort_chronologically_across_months_and_years(self):
        app = AppTest.from_string('''
import pandas as pd
from ui_tables import render_table
render_table(pd.DataFrame({'Datum': ['09/09 2017', '19/08 2017', '01/01 2018', '27/08 2017'],
                           'Hemma': ['September', 'AugustFirst', 'January', 'AugustLast']}),
             'date_regression', compact=True, date_columns=('Datum',))
''').run()
        self.assertFalse(app.exception)
        table = next(m.value for m in app.markdown if '<table' in m.value)
        positions = [table.index(name) for name in ('AugustFirst', 'AugustLast', 'September', 'January')]
        self.assertEqual(positions, sorted(positions))
        app.get('button_group')[0].set_value('Fallande').run()
        table = next(m.value for m in app.markdown if '<table' in m.value)
        self.assertLess(table.index('January'), table.index('AugustFirst'))

    def test_statistics_error_has_a_working_retry(self):
        store = MemoryStore()
        store.url = 'https://retry-ui-test.supabase.co/rest/v1/rpc/'
        actual_statistics = store.statistics
        store.statistics = MagicMock(side_effect=[StatisticsError('Tillfälligt anslutningsfel')])
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['statistics'] = dict(enabled=True, url='https://retry-ui-test.supabase.co', secret_key='sb_secret_test')
        app.session_state['app_started'] = True
        with patch('statistics_store.SupabaseStore', return_value=store):
            app.run()
            app.get('button_group')[0].set_value('Statistik').run()
            self.assertTrue(any('Tillfälligt' in w.value for w in app.warning))
            store.statistics = actual_statistics
            next(b for b in app.button if b.key == 'retry_statistics_metadata').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(t.label == 'Kick-off-statistik' for t in app.tabs))

    def test_simultaneous_statistics_failures_have_independent_retry_buttons(self):
        store = MemoryStore()
        store.url = 'https://parallel-errors-ui-test.supabase.co/rest/v1/rpc/'
        original_statistics = store.statistics
        def unavailable_all_versions(version, *args):
            if version is None:
                raise StatisticsError('Tillfälligt anslutningsfel')
            return original_statistics(version, *args)
        store.statistics = unavailable_all_versions
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['statistics'] = dict(enabled=True, url='https://parallel-errors-ui-test.supabase.co', secret_key='sb_secret_test')
        app.session_state['app_started'] = True
        app.session_state['game_mode'] = 'Statistik'
        app.session_state['stats_kick_version'] = 'Alla modellversioner'
        app.session_state['stats_season_version'] = 'Alla modellversioner'
        with patch('statistics_store.SupabaseStore', return_value=store):
            app.run()
        self.assertFalse(app.exception)
        keys = {b.key for b in app.button if b.label == 'Försök igen'}
        self.assertEqual(keys, {'retry_kick_statistics', 'retry_kick_history', 'retry_season_statistics'})

    def test_placement_chart_changes_data_when_team_changes(self):
        from statistics_page import st as page_st
        store = MemoryStore()
        store.url = 'https://placement-test.supabase.co/rest/v1/rpc/'
        original_statistics = store.statistics
        def distinct_distributions(*args):
            data = original_statistics(*args)
            data['distribution'] = [{'team': 'Arsenal', 'position': 1, 'count': 2},
                                    {'team': 'Chelsea', 'position': 5, 'count': 2}]
            return data
        store.statistics = distinct_distributions
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['statistics'] = dict(enabled=True, url='https://placement-test.supabase.co', secret_key='sb_secret_test')
        app.session_state['app_started'] = True
        with patch('statistics_store.SupabaseStore', return_value=store), \
             patch('statistics_page.st.vega_lite_chart', wraps=page_st.vega_lite_chart) as charts:
            app.run()
            app.get('button_group')[0].set_value('Statistik').run()
            first_spec = charts.call_args.args[0]
            first = pd.DataFrame(first_spec['layer'][0]['data']['values'])
            first_key = charts.call_args.kwargs['key']
            self.assertEqual(first.loc[first.Placering.eq(1), 'Säsonger'].iloc[0], 2)
            next(s for s in app.selectbox if s.key == 'stats_distribution_team').set_value('Chelsea').run()
            second_spec = charts.call_args.args[0]
            second = pd.DataFrame(second_spec['layer'][0]['data']['values'])
            self.assertEqual(second.loc[second.Placering.eq(1), 'Säsonger'].iloc[0], 0)
            self.assertEqual(second.loc[second.Placering.eq(5), 'Säsonger'].iloc[0], 2)
            self.assertNotEqual(first_key, charts.call_args.kwargs['key'])
            self.assertNotEqual(first_spec['layer'][0]['data'], second_spec['layer'][0]['data'])
            sent_spec = json.loads(app.get('vega_lite_chart')[-1].proto.spec)
            self.assertEqual(sent_spec['layer'][0]['data'], second_spec['layer'][0]['data'])
            alt.Chart.from_dict(second_spec)
            self.assertFalse(app.exception)

    def test_statistics_filters_are_independent_and_next_to_their_views(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['statistics'] = dict(enabled=True, url='https://test.supabase.co', secret_key='sb_secret_test')
        app.session_state['app_started'] = True
        with patch('statistics_store.SupabaseStore', return_value=MemoryStore()):
            app.run()
            app.get('button_group')[0].set_value('Statistik').run()
            self.assertFalse(app.exception)
            next(s for s in app.selectbox if s.key == 'stats_season_version').set_value('Alla modellversioner').run()
            self.assertEqual(next(s for s in app.selectbox if s.key == 'stats_kick_version').value, 'Aktuell modell')
            next(s for s in app.selectbox if s.key == 'stats_history_team').set_value('Chelsea').run()
            self.assertEqual(next(s for s in app.selectbox if s.key == 'stats_distribution_team').value, 'Arsenal')
            app.session_state['stats_page'] = 2
            next(s for s in app.selectbox if s.key == 'stats_distribution_team').set_value('Tottenham').run()
            self.assertEqual(app.session_state['stats_page'], 2)
            next(s for s in app.selectbox if s.key == 'stats_venue').set_value('Borta').run()
            self.assertEqual(app.session_state['stats_page'], 0)
            self.assertEqual(next(s for s in app.selectbox if s.key == 'stats_history_team').value, 'Chelsea')
            self.assertEqual(next(s for s in app.selectbox if s.key == 'stats_season_version').value, 'Alla modellversioner')
            self.assertFalse(app.exception)

    def test_completed_round_is_saved_once_after_match_sync(self):
        store = MemoryStore()
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['statistics'] = dict(enabled=True, url='https://test.supabase.co', secret_key='sb_secret_test')
        app.session_state['app_started'] = True
        with patch('statistics_store.SupabaseStore', return_value=store):
            app.run()
            next(b for b in app.button if b.key == 'choose_round_10').click().run()
            next(b for b in app.button if b.label == 'Starta omgång').click().run()
            state = dict(app.session_state['kick'])
            events = [kick_event('Arsenal', 'Chelsea', 'D', {'H': .5, 'D': .2, 'A': .3}, state['model_version']) for _ in range(9)]
            store.ingest({'matches': events})
            state['event_ids'] = [event['id'] for event in events]
            state['matches'] = 9
            state['draws'] = 9
            app.session_state['kick'] = state
            store.lose_round_ack = True
            with patch('numpy.random.choice', return_value='D'):
                next(b for b in app.button if b.label == 'Spela match').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(store.matches), 10)
            self.assertEqual(len(store.completed), 1)
            payload = store.completed[state['run_id']]
            self.assertEqual(payload['length'], 10)
            self.assertEqual(len(set(payload['match_ids'])), 10)
            self.assertTrue(all(event in store.matches for event in payload['match_ids']))
            self.assertEqual(len(app.session_state['pending_kick_rounds']), 1)
            next(b for b in app.button if b.key == 'retry_statistics').click().run()
            self.assertEqual(app.session_state['pending_kick_rounds'], [])
            app.run()
            self.assertEqual(len(store.completed), 1)

    def test_failed_write_retry_does_not_duplicate_match(self):
        store = MemoryStore()
        store.lose_ack = True
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['statistics'] = dict(enabled=True, url='https://test.supabase.co', secret_key='sb_secret_test')
        app.session_state['app_started'] = True
        with patch('statistics_store.SupabaseStore', return_value=store):
            app.run()
            next(b for b in app.button if b.key == 'choose_round_10').click().run()
            next(b for b in app.button if b.label.startswith('Starta om')).click().run()
            next(b for b in app.button if b.label == 'Spela match').click().run()
            self.assertTrue(app.session_state['statistics_error'])
            self.assertEqual(len(app.session_state['statistics_outbox']), 1)
            next(b for b in app.button if b.key == 'retry_statistics').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(store.matches), 1)
            self.assertEqual(app.session_state['statistics_outbox'], [])

    def test_completed_season_is_saved_once_on_reruns(self):
        store = MemoryStore()
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['statistics'] = dict(enabled=True, url='https://test.supabase.co', secret_key='sb_secret_test')
        app.session_state['app_started'] = True
        season = complete_season()
        app.session_state['season'] = season
        with patch('statistics_store.SupabaseStore', return_value=store):
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(len(store.matches), 380)
            self.assertEqual(len(store.completed), 1)
            app.run()
            self.assertEqual(len(store.matches), 380)
            self.assertEqual(len(store.completed), 1)

    def test_no_database_statistics_page_and_game_still_work(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.session_state['app_started'] = True
        app.run()
        app.get('button_group')[0].set_value('Statistik').run()
        self.assertFalse(app.exception)
        self.assertTrue(any('inte tillgänglig lokalt' in x.value for x in app.info))
        self.assertFalse(app.tabs)
        self.assertFalse(app.selectbox)
        self.assertFalse(any(b.key in ('refresh_statistics', 'refresh_season_statistics') for b in app.button))
        app.get('button_group')[0].set_value('Kick-off').run()
        next(b for b in app.button if b.key == 'choose_round_10').click().run()
        next(b for b in app.button if b.label.startswith('Starta om')).click().run()
        next(b for b in app.button if b.label == 'Spela match').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['kick']['matches'], 1)

    def test_configured_kick_is_recorded_once_and_aggregates_render(self):
        store = MemoryStore()
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60)
        app.secrets['statistics'] = dict(enabled=True, url='https://test.supabase.co', secret_key='sb_secret_test')
        app.session_state['app_started'] = True
        with patch('statistics_store.SupabaseStore', return_value=store):
            app.run()
            next(b for b in app.button if b.key == 'choose_round_10').click().run()
            next(b for b in app.button if b.label.startswith('Starta om')).click().run()
            next(b for b in app.button if b.label == 'Spela match').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(store.matches), 1)
            next(b for b in app.button if b.key == 'close_result').click().run()
            app.run()
            self.assertEqual(len(store.matches), 1)
            app.get('button_group')[0].set_value('Statistik').run()
            self.assertFalse(app.exception)
            tables = [m.value for m in app.markdown if m.value.startswith('<div class="league-wrap statistics-table-wrap">')]
            self.assertEqual(len(tables), 3)
            season_table = next(t for t in tables if 'Vanligaste placering' in t)
            self.assertIn('1, 2', season_table)
            self.assertNotIn('None', ''.join(tables))
            charts = app.get('vega_lite_chart')
            self.assertEqual(len(charts), 3)
            for chart in charts:
                self.assertEqual(json.loads(chart.proto.spec)['padding'],
                                 dict(top=20, right=20, bottom=20, left=20))
            self.assertTrue(any('Underlag: 2 avslutade' in c.value for c in app.caption))
            next(s for s in app.selectbox if s.key == 'kick_statistics_sort').set_value('Matcher').run()
            for direction, first, second in [('Stigande', 'Bournemouth', 'Arsenal'),
                                             ('Fallande', 'Arsenal', 'Bournemouth')]:
                next(g for g in app.get('button_group') if g.key == 'kick_statistics_direction').set_value(direction).run()
                self.assertFalse(app.exception)
                html = next(m.value for m in app.markdown if m.value.startswith('<div class="league-wrap statistics-table-wrap">'))
                self.assertLess(html.index(f'<td>{first}</td>'), html.index(f'<td>{second}</td>'))


if __name__ == '__main__':
    unittest.main()
