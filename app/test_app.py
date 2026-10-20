"""Run with the app environment: python -m unittest discover -s app -p test_app.py."""
import ast
import unittest
from unittest.mock import patch
from html import escape
import xml.etree.ElementTree as ET
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

APP = Path(__file__).with_name('app.py')


def simulation_namespace():
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    names = {'predict', 'starting_states', 'empty_table', 'sample_score', 'apply_table',
             'update_states', 'simulate_day', 'advance_to_own_match', 'standings',
             'season_results', 'winning_streak', 'league_position', 'result_reaction',
             'play_next_match', 'match_context', 'title_status', 'relegation_notice', 'team_badge',
             'actual_snapshot', 'points_difference', 'season_comparison', 'simulate_batch', 'results_text',
             'kick_challenges', 'kick_reaction', 'kick_round_track'}
    functions = ast.Module(body=[node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names) or
           (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'TEAM_KITS' for t in node.targets))], type_ignores=[])
    initial = pd.read_csv(APP.parent / 'team_states.csv').set_index('Team')
    scope = dict(pd=pd, np=np, escape=escape, TEAM_KITS={}, initial=initial, teams=sorted(initial.index),
                 FEATURES=['EloDiff_Enhanced', 'FormDiff', 'GoalDiff5'], K=10, HOME_ADV=100,
                 model=joblib.load(APP.parent / 'model.joblib'))
    exec(compile(functions, str(APP), 'exec'), scope)
    return scope


class SeasonTests(unittest.TestCase):
    def test_kick_challenges_use_distinct_opponents_and_honest_risk_labels(self):
        scope = self.scope
        states = {team: {} for team in ('Us', 'Easy', 'Even', 'Hard', 'Last')}
        def probabilities(home, away, _):
            opponent = away if home == 'Us' else home
            pw = {'Easy': .65, 'Even': .4, 'Hard': .15, 'Last': .55}[opponent]
            return {'H': pw if home == 'Us' else .8-pw, 'D': .2,
                    'A': .8-pw if home == 'Us' else pw}
        with patch.dict(scope, predict=probabilities):
            choices = scope['kick_challenges']('Us', states, [{'Hemma': 'Us', 'Borta': 'Last'}])
        self.assertEqual([c['opponent'] for c in choices], ['Easy', 'Even', 'Hard'])
        self.assertEqual([c['title'] for c in choices], ['Favorit', 'Jämn match', 'Underdog'])
        for choice in choices:
            self.assertEqual(choice['reward'], round(100/choice['pw']))
            self.assertEqual(choice['penalty'], round(100*choice['pw']))
        with patch.dict(scope, predict=lambda *_: {'H': .7, 'D': .2, 'A': .1}):
            # Make the team the home side for the strongest option; check each
            # advertised label against the actual probabilities, not the rank.
            for choice in scope['kick_challenges']('Us', states, []):
                if choice['title'] == 'Underdog':
                    self.assertLess(choice['pw'], choice['pa'])
                if choice['title'] == 'Jämn match':
                    self.assertLessEqual(abs(choice['pw']-choice['pa']), .15)

    def test_kick_reactions_and_round_track_use_the_round_history(self):
        scope = self.scope
        history = [{'Hemma': 'Us', 'Borta': 'Easy', 'Utfall': 'Vinst', 'Poäng': 150, 'Totalpoäng': 150},
                   {'Hemma': 'Us', 'Borta': 'Even', 'Utfall': 'Vinst', 'Poäng': 250, 'Totalpoäng': 400}]
        text = scope['kick_reaction']('Us', 'Hard', 'Us', 'A', 667, 1067, .15, .65, history)
        self.assertIn('Underdogseger', text)
        self.assertIn('första bortaseger', text)
        self.assertIn('3 raka vinster', text)
        text = scope['kick_reaction']('Us', 'Us', 'Easy', 'H', 150, 550, .65, .15, [])
        self.assertIn('Nytt omgångsbästa', text)
        text = scope['kick_reaction']('Us', 'Us', 'Hard', 'A', -15, 385, .15, .65, history)
        self.assertIn('Segersviten på 2 matcher bröts', text)
        self.assertIn('15 poäng från omgångens bästa', text)
        markup = scope['kick_round_track'](10, history)
        self.assertEqual(markup.count('role="listitem"'), 10)
        self.assertEqual(markup.count('aria-current="step"'), 1)
        self.assertIn('Match 3: nästa match', markup)
        self.assertNotIn('aria-current', scope['kick_round_track'](2, history))

    def test_result_counts_use_singular_and_plural(self):
        label = simulation_namespace()['results_text']
        self.assertEqual(label(1, 1, 1), '1 vinst · 1 oavgjord · 1 förlust')
        self.assertEqual(label(0, 2, 3), '0 vinster · 2 oavgjorda · 3 förluster')

    @classmethod
    def setUpClass(cls):
        cls.scope = simulation_namespace()

    def test_complete_season_and_result_snapshots(self):
        s = self.scope
        fixtures = pd.read_csv(APP.parent / 'fixtures_2017.csv')
        fixtures['Date'] = pd.to_datetime(fixtures['Date'])
        fixtures = fixtures.sort_values(['Date', 'HomeTeam', 'AwayTeam'])
        dates = list(fixtures.Date.drop_duplicates())
        scores = pd.read_csv(APP.parent / 'historical_scores.csv')
        pools = {k: scores.loc[scores.Outcome.eq(k), ['FTHG', 'FTAG']].to_numpy(dtype=int)
                 for k in ('H', 'D', 'A')}
        season = dict(team='Arsenal', states=s['starting_states'](), date_idx=0,
                      table=s['empty_table'](), history=[], rng_seed=81)
        s['advance_to_own_match'](season, fixtures, dates, pools)
        for played in range(38):
            previous = s['league_position'](season['table'], 'Arsenal') if played else None
            points = season['table']['Arsenal']['Pts']
            result = s['play_next_match'](season, fixtures, dates, pools)
            self.assertEqual(season['table']['Arsenal']['P'], played + 1)
            self.assertEqual(result['place_before'], previous)
            self.assertEqual(result['place_after'], s['league_position'](season['table'], 'Arsenal'))
            self.assertEqual(result['points'] - points, {'Vinst': 3, 'Oavgjort': 1, 'Förlust': 0}[result['result']])
            self.assertEqual(result['finished'], played == 37)
            self.assertTrue(result['reaction'])
        self.assertEqual(len(season['history']), 380)
        self.assertTrue(all(row['P'] == 38 for row in season['table'].values()))
        self.assertEqual(sum(row['GF'] for row in season['table'].values()),
                         sum(row['GA'] for row in season['table'].values()))

    def test_reactions_and_streak_reset(self):
        s = self.scope
        self.assertEqual(s['winning_streak'](['V', 'O', 'V', 'V']), 2)
        self.assertEqual(s['winning_streak'](['V', 'V', 'F']), 0)
        result = dict(result='Vinst', streak=1, win_probability=.2)
        self.assertIn('trotsade', s['result_reaction'](result, []))
        result.update(streak=3)
        self.assertIn('3 raka', s['result_reaction'](result, ['V', 'V']))
        self.assertIn('bruten', s['result_reaction'](dict(result='Förlust'), ['V']))
        result.update(streak=1, win_probability=.6)
        self.assertIn('bortasegern', s['result_reaction'](result, ['F'], first_away_win=True))
        self.assertIn('första seger', s['result_reaction'](result, ['F']))

    def test_form_uses_team_perspective_and_season_only(self):
        history = [dict(HomeTeam='Arsenal', AwayTeam='Chelsea', Score='2–0'),
                   dict(HomeTeam='Chelsea', AwayTeam='Arsenal', Score='1–1'),
                   dict(HomeTeam='Arsenal', AwayTeam='Chelsea', Score='0–1')]
        results = self.scope['season_results'](history)
        self.assertEqual(results['Arsenal'], ['V', 'O', 'F'])
        self.assertEqual(results['Chelsea'], ['F', 'O', 'V'])
        self.assertEqual(results['Liverpool'], [])

    def test_title_requires_strict_points_lead_and_accounts_for_games_in_hand(self):
        s = self.scope
        fixtures = pd.read_csv(APP.parent / 'fixtures_2017.csv')
        table = s['empty_table']()
        for row in table.values():
            row['P'] = 37
        table['Arsenal']['Pts'] = 80
        table['Chelsea']['Pts'] = 80
        self.assertEqual(s['title_status'](table, 'Arsenal', fixtures, 'Liverpool'), (False, False))
        # A win over the only rival denies that rival three possible points.
        self.assertEqual(s['title_status'](table, 'Arsenal', fixtures, 'Chelsea'), (False, True))
        table['Arsenal']['Pts'] = 83
        self.assertFalse(s['title_status'](table, 'Arsenal', fixtures)[0])
        table['Arsenal']['Pts'] = 84
        self.assertTrue(s['title_status'](table, 'Arsenal', fixtures)[0])
        table['Chelsea']['P'] = 36
        self.assertFalse(s['title_status'](table, 'Arsenal', fixtures)[0])
        for row in table.values():
            row['P'] = 38
        self.assertTrue(s['title_status'](table, 'Arsenal', fixtures)[0])

    def test_relegation_messages_distinguish_risk_and_final_outcome(self):
        notice = self.scope['relegation_notice']
        self.assertIsNone(notice(17))
        self.assertIn('inte avgjord', notice(18, before=17))
        self.assertIn('nedflyttningsplats', notice(19))
        self.assertIn('innebär nedflyttning', notice(20, finished=True))

    def test_player_poses_are_valid_distinct_svg(self):
        images = []
        for pose in ('neutral', 'win', 'sad', 'trophy'):
            svg = self.scope['team_badge']('Arsenal', pose)
            ET.fromstring(svg)
            self.assertEqual(ET.fromstring(svg).attrib['viewBox'], '0 -8 24 38')
            self.assertIn(f'pose-{pose}', svg)
            images.append(svg)
        self.assertEqual(len(set(images)), 4)

    def test_actual_table_cutoff_and_final_totals(self):
        s = self.scope
        actual = pd.read_csv(APP.parent / 'actual_results_2017.csv')
        actual['Date'] = pd.to_datetime(actual['Date'])
        fixtures = pd.read_csv(APP.parent / 'fixtures_2017.csv')
        fixtures['Date'] = pd.to_datetime(fixtures['Date'])
        keys = ['Date','HomeTeam','AwayTeam']
        self.assertEqual(len(actual), 380)
        self.assertFalse(actual.duplicated(keys).any())
        self.assertTrue(fixtures.merge(actual, on=keys, how='outer', indicator=True)['_merge'].eq('both').all())
        empty, _ = s['actual_snapshot'](actual)
        self.assertTrue(all(row['P'] == 0 for row in empty.values()))
        first, form = s['actual_snapshot'](actual, '2017-08-11')
        self.assertEqual(first['Arsenal']['Pts'], 3)
        self.assertEqual(first['Leicester']['Pts'], 0)
        self.assertEqual(first['Liverpool']['P'], 0)
        self.assertEqual(form['Arsenal'], ['V'])
        simulated = s['empty_table']()
        s['apply_table'](simulated, 'Arsenal', 'Leicester', 1, 1)
        delta = s['points_difference'](simulated, first)
        self.assertEqual(delta['Arsenal'], -2)
        self.assertEqual(delta['Leicester'], 1)
        # Every snapshot includes only completed calendar dates, including postponed games.
        for date in fixtures.Date.drop_duplicates():
            table, _ = s['actual_snapshot'](actual, date)
            expected = fixtures.loc[fixtures.Date.le(date)]
            counts = pd.concat([expected.HomeTeam, expected.AwayTeam]).value_counts()
            self.assertTrue(all(row['P'] == counts.get(team, 0) for team,row in table.items()))
        final, _ = s['actual_snapshot'](actual, actual.Date.max())
        self.assertTrue(all(row['P'] == 38 for row in final.values()))
        self.assertEqual(final['Man City']['Pts'], 100)
        self.assertEqual(final['West Brom']['Pts'], 31)
        self.assertEqual(s['league_position'](final, 'Liverpool'), 4)

    def test_season_comparison_uses_absolute_errors(self):
        s = self.scope
        actual = s['empty_table']()
        simulated = s['empty_table']()
        for team in actual:
            actual[team]['Pts'] = simulated[team]['Pts'] = 50
        simulated['Arsenal']['Pts'] += 10
        simulated['Chelsea']['Pts'] -= 10
        comparison, error = s['season_comparison'](simulated, actual)
        self.assertEqual(error, 1.0)  # (10 + 10) / 20, never zero.
        self.assertEqual(comparison.set_index('Lag').loc['Chelsea', 'Skillnad'], -10)
        self.assertEqual(comparison.set_index('Lag').loc['Arsenal', 'Skillnad'], 10)
        _, zero = s['season_comparison'](actual, actual)
        self.assertEqual(zero, 0)

    def test_batch_pauses_at_title_and_resumes_without_replaying(self):
        s = self.scope
        season = dict(date_idx=0)
        batch = dict(remaining=5, results=[])
        calls = []
        progress = []
        def play(season, *_):
            season['date_idx'] += 1
            calls.append(season['date_idx'])
            return dict(new_champion=season['date_idx'] == 2, finished=season['date_idx'] == 5)
        with patch.dict(s, play_next_match=play):
            title = s['simulate_batch'](season, None, list(range(5)), None, batch,
                                        on_progress=lambda done, total: progress.append((done, total)))
            self.assertTrue(title['quick_title'])
            self.assertTrue(title['quick_pause'])
            self.assertEqual(calls, [1,2])
            self.assertEqual(batch['remaining'], 3)
            self.assertIsNone(s['simulate_batch'](season, None, list(range(5)), None, batch,
                                                on_progress=lambda done, total: progress.append((done, total))))
        self.assertEqual(calls, [1,2,3,4,5])
        self.assertEqual(len(batch['results']), 5)
        self.assertEqual(batch['remaining'], 0)
        self.assertEqual(progress, [(n, 5) for n in range(1, 6)])

    def test_batch_stops_at_end_and_keeps_final_title_celebration(self):
        s = self.scope
        season = dict(date_idx=0)
        batch = dict(remaining=5, results=[])
        def play(season, *_):
            season['date_idx'] += 1
            return dict(new_champion=True, finished=True)
        with patch.dict(s, play_next_match=play):
            result = s['simulate_batch'](season, None, [0], None, batch)
            self.assertTrue(result['quick_title'])
            self.assertFalse(result['quick_pause'])
            self.assertIsNone(s['simulate_batch'](season, None, [0], None, batch))
        self.assertEqual(len(batch['results']), 1)


class InterfaceTests(unittest.TestCase):
    def test_kick_choice_updates_preview_and_next_match_changes_suggestions(self):
        app = AppTest.from_file(str(APP), default_timeout=60)
        app.session_state['app_started'] = True
        app.run()
        next(b for b in app.button if b.key == 'choose_round_10').click().run()
        next(b for b in app.button if b.label == 'Starta omgång').click().run()
        choice = dict(app.session_state['kick']['challenges'][2])
        next(b for b in app.button if b.key == 'kick_challenge_2').click().run()
        self.assertEqual(app.session_state['kick_opponent'], choice['opponent'])
        self.assertEqual(app.session_state['kick_venue'], choice['venue'])
        outcome = 'H' if choice['venue'] == 'Hemma' else 'A'
        with patch('numpy.random.choice', return_value=outcome):
            next(b for b in app.button if b.label == 'Spela match').click().run()
        self.assertEqual(app.session_state['kick']['points'], choice['reward'])
        self.assertEqual(app.session_state['pending_result']['delta'], choice['reward'])
        self.assertIn('reaction', app.session_state['pending_result'])
        self.assertTrue(all(c['opponent'] != choice['opponent'] for c in app.session_state['kick']['challenges']))
        next(b for b in app.button if b.key == 'close_result').click().run()
        before = app.session_state['kick']['points']
        app.run()
        self.assertEqual(app.session_state['kick']['points'], before)
        self.assertEqual(app.session_state['kick']['matches'], 1)
        self.assertFalse(app.exception)

    def test_main_menu_preserves_both_games_and_supports_direct_navigation(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        next(b for b in app.button if b.label == 'Spela nu').click().run()
        self.assertEqual(app.session_state['app_view'], 'menu')
        self.assertEqual(len([b for b in app.button if str(b.key).startswith('choose_mode_')]), 3)
        next(b for b in app.button if b.key == 'choose_mode_kick').click().run()
        next(b for b in app.button if b.key == 'choose_round_10').click().run()
        next(b for b in app.button if b.label == 'Starta omgång').click().run()
        next(b for b in app.button if b.label == 'Spela match').click().run()
        next(b for b in app.button if b.key == 'close_result').click().run()
        kick_id = app.session_state['kick']['run_id']
        kick_points = app.session_state['kick']['points']
        next(b for b in app.button if b.label == 'Huvudmeny').click().run()
        card = next(b for b in app.button if b.key == 'choose_mode_kick')
        self.assertIn('1 av 10 matcher', card.label)
        next(b for b in app.button if b.key == 'choose_mode_season').click().run()
        next(b for b in app.button if b.label == 'Starta säsong').click().run()
        season_id = app.session_state['season']['run_id']
        app.get('button_group')[0].set_value('Statistik').run()
        next(b for b in app.button if b.key == 'go_home').click().run()
        self.assertIn('0 av 38 matcher', next(b for b in app.button if b.key == 'choose_mode_season').label)
        next(b for b in app.button if b.key == 'choose_mode_kick').click().run()
        self.assertEqual(app.session_state['kick']['run_id'], kick_id)
        self.assertEqual(app.session_state['kick']['points'], kick_points)
        self.assertEqual(app.session_state['kick']['matches'], 1)
        app.get('button_group')[0].set_value('Säsong').run()
        self.assertEqual(app.session_state['season']['run_id'], season_id)
        self.assertEqual(app.session_state['season']['table']['Arsenal']['P'], 0)
        self.assertFalse(app.exception)

    def test_about_dialog_closes_without_starting_game(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        next(b for b in app.button if b.key == 'open_about').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('# BEAT THE MODEL' in m.value for m in app.markdown))
        next(b for b in app.button if b.key == 'close_about').click().run()
        self.assertFalse(app.exception)
        self.assertIn('Spela nu', [b.label for b in app.button])

    def test_kick_round_lengths_locked_team_sides_and_finish(self):
        for limit in (10, 20, 30):
            with self.subTest(limit=limit):
                app = AppTest.from_file(str(APP), default_timeout=60)
                app.session_state['app_started'] = True
                app.run()
                self.assertFalse(app.selectbox)
                next(b for b in app.button if b.key == f'choose_round_{limit}').click().run()
                next(s for s in app.selectbox if s.key == 'kick_team_choice').set_value('Crystal Palace').run()
                next(b for b in app.button if b.label == 'Starta omgång').click().run()
                self.assertEqual(app.session_state['kick']['team'], 'Crystal Palace')
                self.assertFalse(any(s.label == 'Ditt lag' for s in app.selectbox))
                opponent = next(s for s in app.selectbox if s.key == 'kick_opponent').value
                for venue in ('Hemma', 'Borta'):
                    next(g for g in app.get('button_group') if g.key == 'kick_venue').set_value(venue).run()
                    card = next(m.value for m in app.markdown if 'KICK-OFF · DIN MATCH' in m.value)
                    home, away = ('Crystal Palace', opponent) if venue == 'Hemma' else (opponent, 'Crystal Palace')
                    self.assertLess(card.index(f'class="team-name">{home}'), card.index(f'class="team-name">{away}'))
                # Exercise the last match boundary without simulating thirty UI dialogs per case.
                state = dict(app.session_state['kick'])
                state['matches'] = limit - 1
                app.session_state['kick'] = state
                with patch('numpy.random.choice', return_value='D'):
                    next(b for b in app.button if b.label == 'Spela match').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state['kick']['matches'], limit)
                self.assertTrue(app.session_state['pending_result']['finished'])
                next(b for b in app.button if b.key == 'close_result').click().run()
                self.assertIn('Omgången är klar!', [h.value for h in app.subheader])
                self.assertFalse(any(b.label == 'Spela match' for b in app.button))
                app.run()
                self.assertEqual(app.session_state['kick']['matches'], limit)
                next(b for b in app.button if b.label == 'Ny omgång').click().run()
                self.assertNotIn('kick', app.session_state.filtered_state)
                self.assertFalse(app.exception)

    def test_quick_three_then_finish_and_new_season(self):
        app = AppTest.from_file(str(APP), default_timeout=60).run()
        next(b for b in app.button if b.label == 'Spela nu').click().run()
        next(b for b in app.button if b.key == 'choose_mode_kick').click().run()
        app.get('button_group')[0].set_value('Säsong').run()
        next(b for b in app.button if b.label == 'Starta säsong').click().run()
        self.assertTrue(any('Inte spelat ännu' in m.value for m in app.markdown))
        next(b for b in app.button if b.label == 'Snabbsimulera').click().run()
        self.assertEqual(app.session_state['season']['table']['Arsenal']['P'], 0)
        next(b for b in app.button if b.label == 'Simulera 3 matcher').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['season']['table']['Arsenal']['P'], 3)
        self.assertIn('3 matcher spelade', [h.value for h in app.subheader])
        self.assertTrue(any(e.label == 'Fler alternativ' for e in app.expander))
        next(b for b in app.button if b.key == 'close_quick').click().run()
        self.assertEqual(app.session_state['season']['table']['Arsenal']['P'], 3)
        app.run()
        self.assertEqual(app.session_state['season']['table']['Arsenal']['P'], 3)
        # Use the visible setup for the remainder; handle a possible title pause.
        next(b for b in app.button if b.label == 'Snabbsimulera').click().run()
        next(r for r in app.radio if r.label == 'Antal matcher').set_value('Resten av säsongen').run()
        next(b for b in app.button if b.label == 'Simulera 35 matcher').click().run()
        self.assertFalse(app.exception)
        if any(b.key == 'continue_quick' for b in app.button):
            self.assertTrue(any('pose-trophy' in m.value for m in app.markdown))
            next(b for b in app.button if b.key == 'continue_quick').click().run()
        self.assertFalse(app.exception)
        if any(b.key == 'continue_quick' for b in app.button):
            next(b for b in app.button if b.key == 'continue_quick').click().run()
        self.assertEqual(app.session_state['season']['table']['Arsenal']['P'], 38)
        self.assertEqual(len(app.session_state['season']['history']), 380)
        next(b for b in app.button if b.key == 'close_quick').click().run()
        next(b for b in app.button if b.label == 'Starta ny säsong').click().run()
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state['season'])
        self.assertIn('Starta säsong', [b.label for b in app.button])

    def test_finished_season_shows_comparison(self):
        s = simulation_namespace()
        actual = pd.read_csv(APP.parent / 'actual_results_2017.csv')
        actual['Date'] = pd.to_datetime(actual['Date'])
        table, _ = s['actual_snapshot'](actual, actual.Date.max())
        history = [dict(Date=r.Date.strftime('%Y-%m-%d'), HomeTeam=r.HomeTeam,
                        AwayTeam=r.AwayTeam, Score=f'{r.FTHG}–{r.FTAG}') for r in actual.itertuples()]
        app = AppTest.from_file(str(APP), default_timeout=30)
        app.session_state['app_started'] = True
        app.session_state['season'] = dict(team='Arsenal', table=table, history=history,
            states=s['starting_states'](), date_idx=actual.Date.nunique(), rng_seed=1, last_match=None)
        app.run()
        app.get('button_group')[0].set_value('Säsong').run()
        self.assertFalse(app.exception)
        self.assertIn('Hur nära verkligheten?', [h.value for h in app.subheader])
        self.assertTrue(any('0,0 poäng' in m.value for m in app.caption))
        self.assertTrue(any('Slutplacering' in m.value and 'V / O / F' in m.value for m in app.markdown))
        self.assertIn('Jämför alla 20 lag', [e.label for e in app.expander])

    def test_kickoff_overlay_all_outcomes_and_away_perspective(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        next(b for b in app.button if b.label == 'Spela nu').click().run()
        next(b for b in app.button if b.key == 'choose_mode_kick').click().run()
        next(b for b in app.button if b.key == 'choose_round_10').click().run()
        next(b for b in app.button if b.label.startswith('Starta om')).click().run()
        for index, (venue, outcome, label) in enumerate([
                ('Hemma','H','Vinst'), ('Hemma','D','Oavgjort'),
                ('Hemma','A','Förlust'), ('Borta','H','Förlust')], start=1):
            app.get('button_group')[1].set_value(venue).run()
            with patch('numpy.random.choice', return_value=outcome):
                next(b for b in app.button if b.label == 'Spela match').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['kick']['matches'], index)
            self.assertTrue(any('result-label' in m.value and label in m.value for m in app.markdown))
            self.assertTrue(any('Poäng den här matchen' in m.value for m in app.markdown))
            self.assertTrue(any('Ditt lag' in m.value for m in app.markdown))
            self.assertFalse(any('Tabellplats' in m.value for m in app.markdown))
            delta = app.session_state['kick']['last'][1]
            self.assertTrue(delta > 0 if label == 'Vinst' else delta == 0 if label == 'Oavgjort' else -100 <= delta <= 0)
            points = app.session_state['kick']['points']
            next(b for b in app.button if b.key == 'close_result').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['kick']['matches'], index)
            self.assertEqual(app.session_state['kick']['points'], points)

    def test_start_play_dialog_close_and_home(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertIn('Spela nu', [b.label for b in app.button])
        next(b for b in app.button if b.label == 'Spela nu').click().run()
        next(b for b in app.button if b.key == 'choose_mode_kick').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(next(b for b in app.button if b.key == 'go_home').label.startswith('![Till huvudmenyn](data:image/svg+xml;base64,'))
        app.get('button_group')[0].set_value('Säsong').run()
        next(b for b in app.button if b.label == 'Starta säsong').click().run()
        self.assertFalse(app.exception)
        next(b for b in app.button if b.label == 'Spela match').click().run()
        self.assertFalse(app.exception)
        season = app.session_state['season']
        played = season['table'][season['team']]['P']
        self.assertEqual(played, 1)
        self.assertEqual([t.label for t in app.tabs], ['Din säsong', 'Faktisk tabell'])
        self.assertTrue(any('Simulerade poäng minus verkliga poäng' in m.value for m in app.markdown))
        self.assertTrue(any('Matchpoäng' in m.value for m in app.markdown))
        next(b for b in app.button if b.label == 'Till nästa match').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['season']['table'][season['team']]['P'], played)
        self.assertFalse(any('Matchpoäng' in m.value for m in app.markdown))
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['season']['table'][season['team']]['P'], played)
        self.assertFalse(any('Matchpoäng' in m.value for m in app.markdown))
        next(b for b in app.button if b.key == 'go_home').click().run()
        self.assertTrue(any(b.key == 'choose_mode_season' for b in app.button))
        self.assertEqual(app.session_state['season']['table'][season['team']]['P'], played)


if __name__ == '__main__':
    unittest.main()
