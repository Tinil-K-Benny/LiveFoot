"""Data-provider clients.

API-Football (v3.football.api-sports.io), key from API_FOOTBALL_KEY, 100 req/day:
- today's fixtures: cached to data/fixtures_<date>.json, 1 call/day
- live matches: one `fixtures?live=all` call covers every live match; the
  10-minute TTL below caps upstream usage at ~6 calls/hour total
- final result: 1 call per finished match (collector)

The Odds API (the-odds-api.com), key from ODDS_API_KEY, 500 credits/month:
- pre-match h2h odds, fetched once per league per day, matched to fixtures
  by team name + kickoff, then cached per fixture forever
"""
import difflib
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

BASE = 'https://v3.football.api-sports.io'
ODDS_BASE = 'https://api.the-odds-api.com/v4'
DATA = Path(__file__).resolve().parents[1] / 'data'
LIVE_TTL = 600  # seconds; shared poll cadence for server + collector


class ApiError(RuntimeError):
    pass


def _key(name):
    val = os.environ.get(name)
    if not val and sys.platform == 'win32':
        # setx writes HKCU\Environment; parents launched before setx don't see it
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as k:
                val = winreg.QueryValueEx(k, name)[0]
        except OSError:
            pass
    if not val:
        raise ApiError(f'{name} env var not set')
    return val


def _get(endpoint, params=None):
    key = _key('API_FOOTBALL_KEY')
    r = requests.get(f'{BASE}/{endpoint}', params=params,
                     headers={'x-apisports-key': key}, timeout=15)
    r.raise_for_status()
    body = r.json()
    if body.get('errors'):
        raise ApiError(str(body['errors']))
    return body['response']


def todays_fixtures():
    cache = DATA / f'fixtures_{date.today().isoformat()}.json'
    if cache.exists():
        return json.loads(cache.read_text(encoding='utf-8'))
    fixtures = _get('fixtures', {'date': date.today().isoformat()})
    DATA.mkdir(exist_ok=True)
    cache.write_text(json.dumps(fixtures), encoding='utf-8')
    return fixtures


_live_cache = {'t': 0.0, 'data': None}


def live_matches():
    now = time.time()
    if _live_cache['data'] is not None and now - _live_cache['t'] < LIVE_TTL:
        return _live_cache['data']
    data = _get('fixtures', {'live': 'all'})
    _live_cache.update(t=now, data=data)
    return data


def fixture_result(fixture_id):
    """Final state of a single fixture (used by collector after FT)."""
    resp = _get('fixtures', {'id': fixture_id})
    return resp[0] if resp else None


# ---------- The Odds API ----------

def _odds_get(path, **params):
    r = requests.get(f'{ODDS_BASE}/{path}',
                     params={'apiKey': _key('ODDS_API_KEY'), **params}, timeout=15)
    r.raise_for_status()
    return r.json()


def _read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def _write_json(path, obj):
    DATA.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj), encoding='utf-8')


def _similar(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _sport_key(league, country):
    """Fuzzy-map an API-Football league to an Odds API soccer sport key."""
    cache = DATA / f'oddsapi_sports_{date.today().isoformat()}.json'
    sports = _read_json(cache, None)
    if sports is None:
        sports = [s for s in _odds_get('sports') if s['key'].startswith('soccer_')]
        _write_json(cache, sports)
    target = f'{country} {league}'

    def score(s):  # keys are structured (soccer_sweden_allsvenskan); descriptions are vague
        return max(_similar(target, s['key'].removeprefix('soccer_').replace('_', ' ')),
                   _similar(target, s['description']))

    best = max(sports, key=score, default=None)
    if best and score(best) > 0.5:
        return best['key']
    return None


def _league_events(sport_key):
    """{'fetched_at': epoch, 'events': [...]} for a league's h2h odds,
    cached per day (1 credit per league per day)."""
    cache = DATA / f'oddsapi_{sport_key}_{date.today().isoformat()}.json'
    data = _read_json(cache, None)
    if data is None:
        data = {'fetched_at': time.time(),
                'events': _odds_get(f'sports/{sport_key}/odds', regions='eu', markets='h2h')}
        _write_json(cache, data)
    return data


def _commence_epoch(event):
    from datetime import datetime
    return datetime.fromisoformat(event['commence_time'].replace('Z', '+00:00')).timestamp()


def prematch_odds(fixture):
    """(odds_h, odds_x, odds_a) for an API-Football fixture dict, from The Odds
    API, or None if the league/match isn't covered. Cached per fixture forever."""
    cache = DATA / 'odds_cache.json'
    cached = _read_json(cache, {})
    k = str(fixture['fixture']['id'])
    if k in cached:
        return tuple(cached[k]) if cached[k] else None

    odds = None
    sport = _sport_key(fixture['league']['name'], fixture['league']['country'])
    if sport:
        data = _league_events(sport)
        home, away = fixture['teams']['home']['name'], fixture['teams']['away']['name']
        # ponytail: pure name similarity; add a team-alias table if mismatches show up
        ev = max(data['events'],
                 key=lambda e: _similar(home, e['home_team']) + _similar(away, e['away_team']),
                 default=None)
        if (ev and _similar(home, ev['home_team']) + _similar(away, ev['away_team']) > 1.2
                # odds fetched after kickoff are LIVE odds, not pre-match: never use them
                and _commence_epoch(ev) > data['fetched_at']):
            for book in ev.get('bookmakers', []):
                for mkt in book.get('markets', []):
                    if mkt['key'] != 'h2h':
                        continue
                    px = {o['name']: float(o['price']) for o in mkt['outcomes']}
                    if {ev['home_team'], 'Draw', ev['away_team']} <= px.keys():
                        odds = (px[ev['home_team']], px['Draw'], px[ev['away_team']])
                if odds:
                    break
    cached[k] = odds
    _write_json(cache, cached)
    return odds


_stats_cache = {}  # fixture_id -> (fetched_at, stats)


def fixture_statistics(fixture):
    """In-play stats keyed h_/a_ (possession, shots, ...) for one fixture.
    1 API call per fixture per LIVE_TTL — only call for matches worth the budget."""
    fid = fixture['fixture']['id']
    hit = _stats_cache.get(fid)
    if hit and time.time() - hit[0] < LIVE_TTL:
        return hit[1]
    resp = _get('fixtures/statistics', {'fixture': fid})
    sides = {fixture['teams']['home']['id']: 'h', fixture['teams']['away']['id']: 'a'}
    flat = {}
    for block in resp:
        side = sides.get(block['team']['id'])
        if side:
            for s in block.get('statistics') or []:
                flat[f"{side}_{s['type'].lower().replace(' ', '_')}"] = s['value']
    stats = {'raw': resp, 'flat': flat}
    _stats_cache[fid] = (time.time(), stats)
    return stats


def warm_odds_caches():
    """Fetch league odds BEFORE kickoffs so the daily caches hold pre-match
    lines (once a match starts, its pre-match odds are unobtainable). Call at
    server/collector startup. <=1 credit per covered league per day."""
    done = set()
    for fx in todays_fixtures():
        lg = (fx['league']['name'], fx['league']['country'])
        if lg in done:
            continue
        done.add(lg)
        try:
            sport = _sport_key(*lg)
            if sport:
                _league_events(sport)
        except Exception as e:  # warming is best-effort
            print(f'odds warm failed for {lg}: {e}')


def event_stream(fixture):
    """[(effective_minute, counter_index)] from a fixture's events, sorted.
    Index: 0 h_goal, 1 a_goal, 2 h_yellow, 3 a_yellow, 4 h_red, 5 a_red.
    Stoppage time -> base + 0.9 (45+2 -> 45.9), same rule as training data.
    Own goals: API-Football credits the event to the team that benefits."""
    home_id = fixture['teams']['home']['id']
    out = []
    for e in fixture.get('events') or []:
        t = e.get('time') or {}
        eff = (t.get('elapsed') or 0) + (0.9 if t.get('extra') else 0.0)
        is_home = e['team']['id'] == home_id
        if e['type'] == 'Goal' and e.get('detail') != 'Missed Penalty':
            out.append((eff, 0 if is_home else 1))
        elif e['type'] == 'Card' and e.get('detail') == 'Yellow Card':
            out.append((eff, 2 if is_home else 3))
        elif e['type'] == 'Card' and e.get('detail') == 'Red Card':
            out.append((eff, 4 if is_home else 5))
    out.sort(key=lambda x: x[0])
    return out


def match_state(fixture):
    """Flatten one API-Football live fixture into training-style counts."""
    ev = fixture.get('events') or []

    def count(team_key, kind, detail=None):
        n = 0
        for e in ev:
            if e['team']['id'] != fixture['teams'][team_key]['id']:
                continue
            if e['type'] == kind and (detail is None or e.get('detail') == detail):
                n += 1
        return n

    status = fixture['fixture']['status']
    return {
        'fixture_id': fixture['fixture']['id'],
        # elapsed is null during breaks/shootouts; a live match with no clock is late, not early
        'minute': status.get('elapsed') if status.get('elapsed') is not None
                  else (0 if status['short'] == 'NS' else 90),
        'status': status['short'],
        'home': fixture['teams']['home']['name'],
        'away': fixture['teams']['away']['name'],
        'h_goals': fixture['goals']['home'] or 0,
        'a_goals': fixture['goals']['away'] or 0,
        'h_yellows': count('home', 'Card', 'Yellow Card'),
        'a_yellows': count('away', 'Card', 'Yellow Card'),
        'h_reds': count('home', 'Card', 'Red Card'),
        'a_reds': count('away', 'Card', 'Red Card'),
    }
