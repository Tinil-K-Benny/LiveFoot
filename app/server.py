"""Flask backend for the React app.

    python app/server.py            # http://localhost:5000

GET /api/fixtures          today's fixtures (cached, 1 upstream call/day)
GET /api/live/<fixture_id> live state + model probabilities + history
"""
import sys
import time
from pathlib import Path

import joblib
import pandas as pd
from flask import Flask, jsonify
from flask_cors import CORS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import api_client  # noqa: E402
from app.features import build_features, implied_probs  # noqa: E402

app = Flask(__name__)
CORS(app)

_bundle = joblib.load(ROOT / 'models' / 'model.pkl')
MODEL, FEATURES, CLASSES = _bundle['model'], _bundle['features'], _bundle['classes']

_history = {}  # fixture_id -> [{minute, home, draw, away, at}, ...]


@app.errorhandler(api_client.ApiError)
def api_error(e):
    return jsonify({'error': str(e)}), 502


@app.get('/api/fixtures')
def fixtures():
    live = {}
    try:  # the daily fixture cache goes stale; overlay the live feed (already 10-min cached)
        live = {f['fixture']['id']: f for f in api_client.live_matches()}
    except Exception:
        pass
    out = []
    for f in api_client.todays_fixtures():
        f = live.get(f['fixture']['id'], f)
        out.append({
            'fixture_id': f['fixture']['id'],
            'kickoff': f['fixture']['date'],
            'status': f['fixture']['status']['short'],
            'minute': f['fixture']['status'].get('elapsed'),
            'league_id': f['league']['id'],
            'league': f['league']['name'],
            'country': f['league']['country'],
            'league_logo': f['league']['logo'],
            'flag': f['league']['flag'],
            'home': f['teams']['home']['name'],
            'away': f['teams']['away']['name'],
            'home_logo': f['teams']['home']['logo'],
            'away_logo': f['teams']['away']['logo'],
            'h_goals': f['goals']['home'],
            'a_goals': f['goals']['away'],
        })
    return jsonify(out)


@app.get('/api/live/<int:fixture_id>')
def live(fixture_id):
    match = next((f for f in api_client.live_matches()
                  if f['fixture']['id'] == fixture_id), None)
    if match is None:  # not live: show the pre-match market instead
        fx = next((f for f in api_client.todays_fixtures()
                   if f['fixture']['id'] == fixture_id), None)
        if fx is None:
            return jsonify({'error': 'fixture not in today\'s list'}), 404
        odds = api_client.prematch_odds(fx)
        probs = None
        if odds:
            p = implied_probs(*odds)
            probs = {'home': round(p[0], 4), 'draw': round(p[1], 4), 'away': round(p[2], 4)}
        return jsonify({
            'prematch': True, 'fixture_id': fixture_id,
            'home': fx['teams']['home']['name'], 'away': fx['teams']['away']['name'],
            'status': fx['fixture']['status']['short'], 'kickoff': fx['fixture']['date'],
            'h_goals': fx['goals']['home'], 'a_goals': fx['goals']['away'],
            'odds': odds and {'home': odds[0], 'draw': odds[1], 'away': odds[2]},
            'probs': probs,
        })

    state = api_client.match_state(match)
    odds = api_client.prematch_odds(match)
    if odds is None:  # no odds -> no prediction, but live stats still render
        return jsonify({**state, 'probs': None, 'history': []})

    # model saw minutes 0-90 in training; extra time must not extrapolate
    feats = build_features(min(state['minute'], 90), state['h_goals'], state['a_goals'],
                           state['h_yellows'], state['a_yellows'],
                           state['h_reds'], state['a_reds'], *odds)
    proba = MODEL.predict_proba(pd.DataFrame([feats])[FEATURES])[0]
    probs = {c.lower(): round(float(p), 4) for c, p in zip(CLASSES, proba)}

    hist = _history.setdefault(fixture_id, [])
    if not hist or hist[-1]['minute'] != state['minute']:
        hist.append({'minute': state['minute'], **probs, 'at': int(time.time())})

    try:  # display-only; odds-covered matches earn the extra stats call
        stats = api_client.fixture_statistics(match)['flat']
    except Exception:
        stats = None
    return jsonify({**state, 'probs': probs, 'history': hist, 'stats': stats})


if __name__ == '__main__':
    api_client.warm_odds_caches()
    app.run(port=5000)
