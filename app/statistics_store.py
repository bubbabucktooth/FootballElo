"""Shared statistics, without Streamlit or database credentials in simulation code."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4, uuid5


class StatisticsError(Exception):
    """Safe public error: never includes credentials or an HTTP response body."""


def model_version(root):
    digest = sha256(b'beat-the-model-simulation-v1')
    for name in ('model.joblib', 'team_states.csv', 'history_last5.csv',
                 'fixtures_2017.csv', 'historical_scores.csv'):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()[:16]


def new_run_id():
    return str(uuid4())


def probabilities(values):
    result = {key: float(values[key]) for key in ('H', 'D', 'A')}
    if any(not isfinite(x) or not 0 <= x <= 1 for x in result.values()) or abs(sum(result.values()) - 1) > 1e-6:
        raise ValueError('Invalid probabilities')
    return result


def kick_event(home, away, outcome, probs, version):
    if home == away or outcome not in ('H', 'D', 'A'):
        raise ValueError('Invalid match')
    return dict(id=new_run_id(), mode='kick', version=version, home=home, away=away,
                outcome=outcome, probabilities=probabilities(probs),
                occurred_at=datetime.now(timezone.utc).isoformat())


def final_rows(history):
    """Derive final standings from recorded matches, not mutable UI state."""
    table, seen = {}, set()
    for match in history:
        h, a = match['HomeTeam'], match['AwayTeam']
        if h == a or (h, a) in seen:
            raise ValueError('Duplicate fixture')
        seen.add((h, a))
        hg, ag = map(int, match['Score'].split('–'))
        if min(hg, ag) < 0:
            raise ValueError('Negative score')
        for team, gf, ga in ((h, hg, ag), (a, ag, hg)):
            row = table.setdefault(team, dict(team=team, played=0, points=0, gd=0, gf=0))
            row['played'] += 1
            row['points'] += 3 if gf > ga else 1 if gf == ga else 0
            row['gd'] += gf-ga
            row['gf'] += gf
    if len(history) != 380 or len(table) != 20 or any(r['played'] != 38 for r in table.values()):
        raise ValueError('Only complete 380-match seasons may be finalized')
    ordered = sorted(table.values(), key=lambda r: (-r['points'], -r['gd'], -r['gf'], r['team']))
    return [dict(team=r['team'], position=i, points=r['points']) for i, r in enumerate(ordered, 1)]


def season_payload(season, complete=False):
    run_id = season['run_id']
    cursor = season.get('statistics_cursor', 0)
    matches = []
    for match in season['history'][cursor:]:
        hg, ag = map(int, match['Score'].split('–'))
        h, a, date = match['HomeTeam'], match['AwayTeam'], match['Date']
        matches.append(dict(id=str(uuid5(UUID(run_id), f'{date}|{h}|{a}')), mode='season',
            version=season['model_version'], home=h, away=a, match_date=date, hg=hg, ag=ag,
            outcome='H' if hg > ag else 'D' if hg == ag else 'A',
            probabilities=probabilities(match['probabilities'])))
    return dict(season=dict(id=run_id, version=season['model_version'], selected_team=season['team'],
                            complete=bool(complete)), matches=matches,
                results=final_rows(season['history']) if complete else [])


def sync_season(store, season, complete=False):
    """Advance acknowledgements only after success; replayed IDs are idempotent."""
    if 'run_id' not in season or 'model_version' not in season:
        return False  # Older sessions have no reliable model/probability provenance.
    if season.get('statistics_cursor', 0) == len(season['history']) and (not complete or season.get('statistics_complete')):
        return False
    payload = season_payload(season, complete)
    store.ingest(payload)
    season['statistics_cursor'] = len(season['history'])
    season['statistics_complete'] = bool(complete)
    return True


def flush_kicks(store, queue):
    while queue:
        batch = queue[:100]
        store.ingest(dict(matches=batch))
        del queue[:len(batch)]


class SupabaseStore:
    def __init__(self, url, secret_key):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise StatisticsError('Ogiltig Supabase-adress. Använd projektets HTTPS-adress.')
        if not secret_key or secret_key.startswith('sb_publishable_'):
            raise StatisticsError('En hemlig servernyckel behövs för statistikanslutningen.')
        self.url = url.rstrip('/') + '/rest/v1/rpc/'
        self._key = secret_key

    def _rpc(self, name, payload):
        headers = {'apikey': self._key, 'Content-Type': 'application/json'}
        if not self._key.startswith('sb_secret_'):
            headers['Authorization'] = 'Bearer ' + self._key
        request = Request(self.url + name, data=json.dumps(payload, allow_nan=False).encode(), headers=headers, method='POST')
        try:
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        except HTTPError as error:
            raise StatisticsError(f'Statistikdatabasen svarade med HTTP {error.code}. Kontrollera SQL och secrets.') from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise StatisticsError('Statistikdatabasen kunde inte nås. Försök igen senare.') from None

    def ingest(self, payload):
        result = self._rpc('btm_ingest', {'payload': payload})
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise StatisticsError('Statistikdatabasen bekräftade inte sparningen.')
        return result

    def statistics(self, version=None, venue=None, opponent=None, team=None, page=0):
        result = self._rpc('btm_statistics', dict(p_version=version, p_venue=venue,
            p_opponent=opponent, p_team=team, p_page=max(0, int(page))))
        if not isinstance(result, dict) or not all(isinstance(result.get(k), list) for k in ('kick','seasons','history','distribution')):
            raise StatisticsError('Statistikdatabasen gav ett oväntat svar. Kontrollera SQL-versionen.')
        return result
