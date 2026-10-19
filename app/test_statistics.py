import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import pandas as pd
from streamlit.testing.v1 import AppTest
from statistics_store import (StatisticsError, SupabaseStore, kick_event, final_rows,
                              season_payload, sync_season, flush_kicks, new_run_id, probabilities)

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
            'history': [], 'kick_total': 2, 'history_total': 0, 'completed_seasons': 2}

    def highscores(self, version):
        return []

    def save_round(self, payload):
        self.completed.setdefault(payload['id'], payload)
        if self.lose_round_ack:
            self.lose_round_ack = False
            raise StatisticsError('Lost round acknowledgement')
        return {'ok': True}


class PersistenceTests(unittest.TestCase):
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
        self.assertTrue(any('inte aktiverad' in x.value for x in app.info))
        self.assertEqual([x.label for x in app.tabs], ['Kick-off-statistik', 'Säsongsstatistik'])
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
            self.assertEqual(len(tables), 2)
            self.assertIn('Vanligaste placering', tables[1])
            self.assertIn('1, 2', tables[1])
            self.assertNotIn('None', ''.join(tables))
            charts = app.get('vega_lite_chart')
            self.assertEqual(len(charts), 1)
            padding = json.loads(charts[0].proto.spec)['padding']
            self.assertEqual(padding, dict(top=20, right=20, bottom=20, left=20))


if __name__ == '__main__':
    unittest.main()
