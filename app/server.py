"""Flask backend for the React app.

    python app/server.py            # http://localhost:5000

GET /api/fixtures          today's fixtures (API-Football, 1 call/day) with live
                           score/minute overlaid from sofascore's free feed
GET /api/live/<fixture_id> live state + model probabilities + history; all live
                           data (score, clock, cards, stats) comes from sofascore
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from flask import Flask, jsonify
from flask_cors import CORS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import api_client, sofascore  # noqa: E402
from app.features import (build_features_v2, goals_to_come, implied_probs,  # noqa: E402
                          predict_v2, scorelines)

app = Flask(__name__)
CORS(app)

BUNDLE = joblib.load(ROOT / 'models' / 'model_v2.pkl')  # training/train_v2.py
CLASSES = BUNDLE['classes']
SCORE = joblib.load(ROOT / 'models' / 'score_model.pkl')  # training/train_score.py


def probs_dict(p):
    return {c.lower(): round(float(v), 4) for c, v in zip(CLASSES, p)}


def history_curve(stream, state, odds, now_probs):
    """Win probability for every minute so far, rebuilt from the goal/card
    timeline on each request, so every match has a full chart whether or not
    anyone had it open. Past minutes use score, cards and odds (live stats are
    only known for now); the last point is the live prediction."""
    rows = []
    for m in range(min(state['minute'], 90)):
        c = [0] * 6
        for eff, i in stream:
            if eff <= m:
                c[i] += 1
        rows.append(build_features_v2(m, *c, None, odds))
    pts = [{'minute': r['minute'], **probs_dict(p), 'h_goals': r['h_goals'], 'a_goals': r['a_goals']}
           for r, p in zip(rows, predict_v2(BUNDLE, pd.DataFrame(rows)) if rows else [])]
    pts.append({'minute': state['minute'], **now_probs,
                'h_goals': state['h_goals'], 'a_goals': state['a_goals']})
    return pts


@app.errorhandler(api_client.ApiError)
def api_error(e):
    return jsonify({'error': str(e)}), 502


# API-Football stat name (flattened) -> the sofascore key model v2 reads
API_TO_SOFA = {'shotsOnGoal': 'shots_on_goal', 'totalShotsOnGoal': 'total_shots',
               'cornerKicks': 'corner_kicks'}


def kicked_off(fx):
    return datetime.fromisoformat(fx['fixture']['date']).timestamp() <= time.time()


IN_PLAY = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE'}


def cached_status(fx):
    """The daily fixture cache freezes whatever status a match had when it was
    fetched. Called only when no live feed has the match: still in play 130
    min after kick-off (a 90-min match ends by ~115) means it has finished.
    ponytail: the score stays the cached one; a sofascore by-date call would give finals."""
    s = fx['fixture']['status']['short']
    started = datetime.fromisoformat(fx['fixture']['date']).timestamp()
    return 'FT' if s in IN_PLAY and time.time() - started > 130 * 60 else s


def api_live():
    """Backup live feed (API-Football, free plan): {fixture_id: live fixture}.
    One call covers every match, 10-min cached, only used when sofascore fails."""
    try:
        return {f['fixture']['id']: f for f in api_client.live_matches()}
    except Exception:
        return {}


@app.get('/api/fixtures')
def fixtures():
    fxs = api_client.todays_fixtures()
    live, backup = {}, {}
    try:  # the daily fixture cache goes stale; overlay sofascore's free live feed
        live = sofascore.map_to_fixtures(fxs)
    except Exception:
        backup = api_live()
    out = []
    for f in fxs:
        ev = live.get(f['fixture']['id'])
        fresh = backup.get(f['fixture']['id'])  # same shape as the cached fixture, live
        f = fresh or f
        out.append({
            'fixture_id': f['fixture']['id'],
            'kickoff': f['fixture']['date'],
            'status': (sofascore.short_status(ev) if ev
                       else f['fixture']['status']['short'] if fresh else cached_status(f)),
            'minute': sofascore.minute(ev) if ev else f['fixture']['status'].get('elapsed'),
            'league_id': f['league']['id'],
            'league': f['league']['name'],
            'country': f['league']['country'],
            'league_logo': f['league']['logo'],
            'flag': f['league']['flag'],
            'home': f['teams']['home']['name'],
            'away': f['teams']['away']['name'],
            'home_logo': f['teams']['home']['logo'],
            'away_logo': f['teams']['away']['logo'],
            'h_goals': ev.get('homeScore', {}).get('current') if ev else f['goals']['home'],
            'a_goals': ev.get('awayScore', {}).get('current') if ev else f['goals']['away'],
        })
    return jsonify(out)


@app.get('/api/live/<int:fixture_id>')
def live(fixture_id):
    fxs = api_client.todays_fixtures()
    fx = next((f for f in fxs if f['fixture']['id'] == fixture_id), None)
    if fx is None:
        return jsonify({'error': 'fixture not in today\'s list'}), 404
    state = model_stats = stats = None
    stream = []  # goal/card timeline for the chart
    try:
        ev = sofascore.map_to_fixtures(fxs).get(fixture_id)
    except Exception:
        ev = None
    if ev is not None:
        state = sofascore.match_state(ev)
        model_stats = sofascore.statistics(ev['id'])
        stats = sofascore.display_stats(ev['id'])
        stream = sofascore.timeline(ev['id'])
    elif fx['fixture']['status']['short'] != 'NS' or kicked_off(fx):
        lf = api_live().get(fixture_id)  # sofascore down or doesn't cover it: API-Football backup
        if lf is not None:
            state = api_client.match_state(lf)
            stream = api_client.event_stream(lf)

    if state is None:  # not live anywhere: show the pre-match market instead
        odds = api_client.prematch_odds(fx)
        probs = None
        if odds:
            p = implied_probs(*odds)
            probs = {'home': round(p[0], 4), 'draw': round(p[1], 4), 'away': round(p[2], 4)}
        return jsonify({
            'prematch': True, 'fixture_id': fixture_id,
            'home': fx['teams']['home']['name'], 'away': fx['teams']['away']['name'],
            'status': cached_status(fx), 'kickoff': fx['fixture']['date'],
            'h_goals': fx['goals']['home'], 'a_goals': fx['goals']['away'],
            'odds': odds and {'home': odds[0], 'draw': odds[1], 'away': odds[2]},
            'probs': probs,
        })

    # names/id from api-football so they match the fixtures list exactly
    state.update(fixture_id=fixture_id,
                 home=fx['teams']['home']['name'], away=fx['teams']['away']['name'])
    if stats is None:
        try:  # sofascore down / no coverage: 1 api-football call per match, 10-min cached
            stats = api_client.fixture_statistics(fx)['flat'] or None
            if stats:  # its card counts also beat the 0s sofascore reported
                for sk, fk in (('h_yellows', 'h_yellow_cards'), ('a_yellows', 'a_yellow_cards'),
                               ('h_reds', 'h_red_cards'), ('a_reds', 'a_red_cards')):
                    state[sk] = int(stats.get(fk) or 0)
                if not model_stats:  # the model reads sofascore keys
                    model_stats = {sk: {'home': stats.get(f'h_{ak}'), 'away': stats.get(f'a_{ak}')}
                                   for sk, ak in API_TO_SOFA.items()}
        except Exception:
            pass
    # sofascore's opening line (v2's training source, still served mid-match),
    # else The Odds API's pre-kickoff cache; None -> v2 uses its no-odds base
    odds = (sofascore.prematch_odds(ev['id']) if ev is not None else None) \
        or api_client.prematch_odds(fx)

    # model saw minutes 0-90 in training; extra time must not extrapolate
    feats = build_features_v2(min(state['minute'], 90), state['h_goals'], state['a_goals'],
                              state['h_yellows'], state['a_yellows'],
                              state['h_reds'], state['a_reds'], model_stats, odds)
    X = pd.DataFrame([feats])
    probs = probs_dict(predict_v2(BUNDLE, X)[0])
    more_h, more_a = (float(v[0]) for v in goals_to_come(SCORE, X))
    scores = [{'home': h, 'away': a, 'p': round(p, 4)}
              for h, a, p in scorelines(state['h_goals'], state['a_goals'], more_h, more_a)]

    pre = None  # market-implied, for the "change since kick-off" deltas
    if odds:
        pre = {c: round(p, 4) for c, p in zip(('home', 'draw', 'away'), implied_probs(*odds))}

    return jsonify({**state, 'probs': probs, 'pre': pre, 'stats': stats, 'scores': scores,
                    'history': history_curve(stream, state, odds, probs)})


if __name__ == '__main__':
    api_client.warm_odds_caches()
    app.run(port=5000)
