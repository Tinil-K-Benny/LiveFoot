"""Live feed data (free, no key) — the serving-side source for live
score/minute/cards/stats.

One live-feed call covers every live match worldwide; per-match statistics
are fetched on demand, only for the match a user opens.
"""
import difflib
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from curl_cffi import requests as curl

from app.features import parse_odds

LIVE_TTL = 60  # one live-feed call covers every match, so at most 1/min however many users
STATS_TTL = 480  # per-match stats: the open match page re-polls every 8 min


def _feed_root():
    """LIVE_FEED_URL (the feed's site root) from the environment, else from the
    gitignored .env at the project root. Unset -> '' and every request fails,
    so the server falls back to the API-Football backup."""
    url = os.environ.get('LIVE_FEED_URL', '')
    env = Path(__file__).resolve().parents[1] / '.env'
    if not url and env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            key, _, val = line.partition('=')
            if key.strip() == 'LIVE_FEED_URL':
                url = val.strip().strip('"' + "'")
    return url.rstrip('/')


FEED_ROOT = _feed_root()
BASE = f'{FEED_ROOT}/api/v1'
# UA / sec-ch-ua left to curl_cffi so they match the TLS fingerprint
HEADERS = {
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.6',
    'referer': f'{FEED_ROOT}/',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'sec-gpc': '1',
}

# TLS fingerprints: some profiles get 403s (currently Chrome), so rotate on 403 and stick with
# the one that works.
IMPERSONATE_PROFILES = ['safari184', 'safari180', 'safari184_ios', 'chrome146', 'firefox144']
_profile = [0]
_session = curl.Session()


MIN_GAP = 2.0  # seconds between any two live-feed requests, so clicks/tabs never burst
_lock = threading.Lock()
_last = [0.0]


def _get(path):
    if not FEED_ROOT:
        raise RuntimeError('LIVE_FEED_URL is not set (environment or .env)')
    with _lock:  # one request in flight at a time, spaced MIN_GAP apart (retries too)
        for _ in IMPERSONATE_PROFILES:
            wait = MIN_GAP - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            try:
                r = _session.get(f'{BASE}/{path}', headers=HEADERS, timeout=15,
                                 impersonate=IMPERSONATE_PROFILES[_profile[0]])
            finally:
                _last[0] = time.time()
            if r.status_code != 403:
                break
            _profile[0] = (_profile[0] + 1) % len(IMPERSONATE_PROFILES)
        r.raise_for_status()
        return r.json()


_live = {'t': 0.0, 'events': []}


def live_events():
    if time.time() - _live['t'] < LIVE_TTL:
        return _live['events']
    try:
        events = _get('sport/football/events/live').get('events', [])
    except Exception:
        _live['t'] = time.time()  # back off a full TTL instead of retrying on every request
        raise
    _live.update(t=time.time(), events=events)
    return events


def _similar(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


_map_cache = {'t': -1.0, 'map': {}}


def map_to_fixtures(fixtures):
    """{api-football fixture_id: live-feed event}. Candidates are limited
    to kickoffs within 30 min of the event start, then matched by team-name
    similarity (same 1.2 threshold as the odds matcher). Cached per live-feed
    refresh."""
    events = live_events()
    if _map_cache['t'] == _live['t']:
        return _map_cache['map']
    timed = [(datetime.fromisoformat(f['fixture']['date']).timestamp(), f) for f in fixtures]
    out = {}
    for ev in events:
        start = ev.get('startTimestamp', 0)
        cands = [f for ko, f in timed if abs(ko - start) < 1800]

        def score(f):
            return (_similar(f['teams']['home']['name'], ev['homeTeam']['name'])
                    + _similar(f['teams']['away']['name'], ev['awayTeam']['name']))

        best = max(cands, key=score, default=None)
        if best and score(best) > 1.2:
            out[best['fixture']['id']] = ev
    _map_cache.update(t=_live['t'], map=out)
    return out


_stats_cache = {}  # event_id -> (fetched_at, flat)


def statistics(event_id):
    """Flat {key: {'home': v, 'away': v}} from the ALL-period statistics.
    Uncovered (lower-tier) matches cache an empty dict so we don't re-hit."""
    hit = _stats_cache.get(event_id)
    if hit and time.time() - hit[0] < STATS_TTL:
        return hit[1]
    flat = {}
    try:
        data = _get(f'event/{event_id}/statistics')
        for group in (data.get('statistics') or [{}])[0].get('groups', []):
            for item in group.get('statisticsItems', []):
                flat[item['key']] = {'home': item.get('homeValue', item.get('home', 0)),
                                     'away': item.get('awayValue', item.get('away', 0))}
    except Exception:
        pass
    _stats_cache[event_id] = (time.time(), flat)
    return flat


_timeline_cache = {}  # event_id -> (fetched_at, stream)


def timeline(event_id):
    """[(effective_minute, counter_index)] like api_client.event_stream:
    0 h_goal, 1 a_goal, 2 h_yellow, 3 a_yellow, 4 h_red, 5 a_red. Stoppage
    time -> base + 0.9 (45+2 -> 45.9), the training data's rule. Goal sides
    come from the running score, so own goals land on the right team."""
    hit = _timeline_cache.get(event_id)
    if hit and time.time() - hit[0] < STATS_TTL:
        return hit[1]
    out = []
    try:
        incs = _get(f'event/{event_id}/incidents').get('incidents', [])
        eff = lambda i: (i.get('time') or 0) + (0.9 if i.get('addedTime') else 0.0)  # noqa: E731
        h = a = 0
        for i in sorted((i for i in incs if i.get('incidentType') == 'goal'), key=eff):
            hs, as_ = i.get('homeScore', h), i.get('awayScore', a)
            out += [(eff(i), 0)] * max(0, hs - h) + [(eff(i), 1)] * max(0, as_ - a)
            h, a = max(h, hs), max(a, as_)
        for i in incs:
            if i.get('incidentType') == 'card' and i.get('isHome') is not None:
                red = i.get('incidentClass') in ('red', 'yellowRed')
                out.append((eff(i), (4 if red else 2) + (0 if i['isHome'] else 1)))
    except Exception:
        pass
    out.sort()
    _timeline_cache[event_id] = (time.time(), out)
    return out


_odds_cache = {}  # event_id -> (fetched_at, odds or None)


def prematch_odds(event_id):
    """Opening 1X2 line as decimal (h, x, a), or None. The feed keeps it
    during the match (the frozen initialFractionalValue) and it's the line v2
    was trained on. One call per match: a found line is cached for good, a
    miss or error is retried at most every STATS_TTL."""
    hit = _odds_cache.get(event_id)
    if hit and (hit[1] is not None or time.time() - hit[0] < STATS_TTL):
        return hit[1]
    odds = None
    try:
        for m in _get(f'event/{event_id}/odds/1/all').get('markets', []):
            if m.get('marketName') == 'Full time' and not m.get('isLive'):
                odds = parse_odds({c.get('name'): c.get('initialFractionalValue')
                                   for c in m.get('choices', [])})
                break
    except Exception:
        pass
    _odds_cache[event_id] = (time.time(), odds)
    return odds


# Live-feed status description -> API-Football short code (what the frontend keys off)
_STATUS = {'1st half': '1H', 'Halftime': 'HT', '2nd half': '2H',
           'Extra time': 'ET', 'Awaiting extra time': 'BT',
           'Awaiting penalties': 'BT', 'Penalties': 'P', 'Ended': 'FT'}


def short_status(ev):
    desc = ev.get('status', {}).get('description', '')
    return _STATUS.get(desc, '2H')  # unknown live status: assume in play


def minute(ev, now=None):
    """Game clock computed from currentPeriodStartTimestamp."""
    now = now or time.time()
    desc = ev.get('status', {}).get('description', '')
    if desc in ('Halftime', 'Awaiting extra time', 'Awaiting penalties'):
        return 45 if desc == 'Halftime' else 90
    start = ev.get('time', {}).get('currentPeriodStartTimestamp')
    if not start:
        return 0
    elapsed = int(now - start) // 60
    if desc == '1st half':
        return min(45, elapsed)
    if desc == '2nd half':
        return min(90, 45 + elapsed)
    if desc == 'Extra time':
        return 90 + elapsed
    return min(90, elapsed)


def _int_stat(stats, key, side):
    try:
        return int(stats.get(key, {}).get(side) or 0)
    except (TypeError, ValueError):
        return 0


def match_state(ev):
    """Flatten a live-feed event into training-style counts.
    Cards come from the statistics endpoint; matches without stats coverage
    report 0 cards (goals/minute stay correct)."""
    stats = statistics(ev['id'])
    return {
        'minute': minute(ev),
        'status': short_status(ev),
        'home': ev['homeTeam']['name'],
        'away': ev['awayTeam']['name'],
        'h_goals': ev.get('homeScore', {}).get('current', 0) or 0,
        'a_goals': ev.get('awayScore', {}).get('current', 0) or 0,
        'h_yellows': _int_stat(stats, 'yellowCards', 'home'),
        'a_yellows': _int_stat(stats, 'yellowCards', 'away'),
        'h_reds': _int_stat(stats, 'redCards', 'home'),
        'a_reds': _int_stat(stats, 'redCards', 'away'),
    }


# live-feed stat key -> the h_/a_ suffix the frontend's Stats section expects
_DISPLAY = {'ballPossession': 'ball_possession', 'totalShotsOnGoal': 'total_shots',
            'shotsOnGoal': 'shots_on_goal', 'expectedGoals': 'expected_goals',
            'cornerKicks': 'corner_kicks', 'fouls': 'fouls'}


def display_stats(event_id):
    stats = statistics(event_id)
    out = {}
    for sk, fk in _DISPLAY.items():
        if sk in stats:
            out[f'h_{fk}'] = stats[sk]['home']
            out[f'a_{fk}'] = stats[sk]['away']
    return out or None


if __name__ == '__main__':  # offline self-check: clock math + card parsing + mapping score
    now = time.time()
    ev = {'id': 1, 'status': {'description': '2nd half'},
          'time': {'currentPeriodStartTimestamp': now - 20 * 60},
          'homeTeam': {'name': 'Arsenal'}, 'awayTeam': {'name': 'Chelsea'},
          'homeScore': {'current': 2}, 'awayScore': {'current': 1}}
    assert minute(ev, now) == 65
    assert minute({'status': {'description': 'Halftime'}}, now) == 45
    assert short_status(ev) == '2H'
    _stats_cache[1] = (time.time(), {'yellowCards': {'home': '3', 'away': 1},
                                     'redCards': {'home': None, 'away': 'x'}})
    st = match_state(ev)
    assert (st['h_yellows'], st['a_yellows'], st['h_reds'], st['a_reds']) == (3, 1, 0, 0)
    assert st['h_goals'] == 2 and st['minute'] == 65
    FEED_ROOT = 'https://feed.test'  # requests are faked below
    _last[0] = 0.0  # throttle: three back-to-back calls take >= 2 * MIN_GAP
    _session.get = lambda *a, **k: type('R', (), {'status_code': 200, 'raise_for_status': lambda s: None, 'json': lambda s: {}})()
    t0 = time.time()
    for _ in range(3):
        _get('x')
    assert time.time() - t0 >= 2 * MIN_GAP - 0.05
    tried = []  # rotation: first profile 403s, second sticks
    _session.get = lambda *a, **k: (tried.append(k['impersonate']), type('R', (), {
        'status_code': 403 if len(tried) == 1 else 200,
        'raise_for_status': lambda s: None, 'json': lambda s: {}})())[1]
    _profile[0], _last[0] = 0, 0.0
    _get('x')
    _get('y')
    assert tried == ['safari184', 'safari180', 'safari180'], tried
    assert _similar('Arsenal', 'Arsenal FC') + _similar('Chelsea', 'Chelsea') > 1.2
    print('live_feed.py self-check ok')
