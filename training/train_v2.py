"""v2: v1 (or a no-odds twin of it) corrected by live stats from the v2
snapshot JSONL (datasets/new_data/Data). Compares against v1 on the same holdout.

Usage: python training/train_v2.py [--rebuild]
Flattens the JSONL once into data/snapshots_v2.csv (slow: ~19k files), then
trains. Needs models/model.pkl (v1) and data/snapshots.csv (v1's data).
Output: models/model_v2.pkl -> use with app.features.predict_v2.
"""
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, log_loss

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.features import (CLASSES, FEATURE_NAMES_V2, NO_ODDS, base_probs,  # noqa: E402
                          build_features_v2, parse_odds, predict_v2, stack_matrix, stat_num)

SRC = ROOT / 'datasets' / 'new_data' / 'Data'
CSV = ROOT / 'data' / 'snapshots_v2.csv'
HOLDOUT_FRAC = 0.15


def parse_minute(igt):
    """"45+2'" -> 45, 'HT' -> 45, "0'" -> 0; FT/NS/other -> None."""
    s = str(igt).strip()
    if s == 'HT':
        return 45
    digits = ''
    for ch in s:
        if not ch.isdigit():
            break
        digits += ch
    return min(int(digits), 90) if digits else None


def match_rows(path):
    rows = []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    if not rows or rows[-1].get('status') != 'Ended' or rows[-1].get('in_game_time') != 'FT':
        return []  # unfinished, or AET/AP (1X2 settles on 90 min)
    fh_, fa_ = rows[-1]['score']['home'], rows[-1]['score']['away']
    result = 'Home' if fh_ > fa_ else 'Away' if fa_ > fh_ else 'Draw'
    odds = next((parse_odds(r['prematch_odds']) for r in rows if r.get('prematch_odds')), None)
    by_min = {}
    for r in rows[:-1]:
        if r.get('status') in ('NS', 'Not started'):
            continue
        m = parse_minute(r.get('in_game_time'))
        sc = r.get('score') or {}
        h, a = sc.get('home'), sc.get('away')
        if m is None or h is None or a is None:
            continue
        if h > fh_ or a > fa_:
            return []  # snapshot score past the final score: corrupt match, drop it
        stats = r.get('stats') if isinstance(r.get('stats'), dict) else {}

        def card(key, side):  # absent card stat = 0, same as live_feed.match_state
            v = stat_num((stats.get(key) or {}).get(side))
            return 0.0 if v != v else v
        f = build_features_v2(m, h, a, card('yellowCards', 'home'), card('yellowCards', 'away'),
                              card('redCards', 'home'), card('redCards', 'away'), stats, odds)
        f.update(match_id=rows[0]['match_id'], ts=rows[0]['timestamp'], result=result,
                 final_h=fh_, final_a=fa_)  # final score: train_score.py's target
        by_min[m] = f  # several polls in one minute: keep the last
    return list(by_min.values())


def build_csv():
    files = sorted(SRC.glob('*.jsonl'))
    out = []
    for i, p in enumerate(files):
        out.extend(match_rows(p))
        if i % 4000 == 0:
            print(f'  {i}/{len(files)} files, {len(out)} rows')
    df = pd.DataFrame(out)
    df.to_csv(CSV, index=False)
    print(f'wrote {CSV}: {len(df)} rows, {df.match_id.nunique()} matches')


def fit_stack(bundle, d):
    """Fit the two stat-correction LRs on d (in place on bundle)."""
    base, odds = base_probs(bundle, d)
    Z, y = stack_matrix(d, base), d['result'].to_numpy()
    bundle['lr_odds'] = LogisticRegression(max_iter=3000).fit(Z[odds], y[odds])
    bundle['lr_noodds'] = LogisticRegression(max_iter=3000).fit(Z[~odds], y[~odds])


def main():
    if '--rebuild' in sys.argv or not CSV.exists():
        build_csv()
    df = pd.read_csv(CSV)
    df['ts'] = pd.to_datetime(df['ts'])

    v1 = joblib.load(ROOT / 'models' / 'model.pkl')
    old = pd.read_csv(ROOT / 'data' / 'snapshots.csv')
    print(f'fitting no-odds base on v1 data ({old.match_id.nunique()} matches)...')
    noodds = HistGradientBoostingClassifier(random_state=0).fit(old[NO_ODDS], old['result'])
    assert list(noodds.classes_) == CLASSES
    bundle = {'base_odds': v1['model'], 'base_noodds': noodds,
              'features': FEATURE_NAMES_V2, 'classes': CLASSES}

    matches = df.groupby('match_id')['ts'].first().sort_values()
    n_hold = int(len(matches) * HOLDOUT_FRAC)
    hold = df['match_id'].isin(set(matches.index[-n_hold:]))
    train, test = df[~hold], df[hold]
    print(f'{len(matches)} matches -> train {len(train)} rows, holdout {len(test)} rows '
          f'(holdout from {matches.iloc[-n_hold]})')
    fit_stack(bundle, train)

    y = test['result'].to_numpy()
    p2 = predict_v2(bundle, test)
    base, odds = base_probs(bundle, test)  # base = v1 on odds rows, no-odds twin elsewhere
    ll = lambda yy, pp: log_loss(yy, pp, labels=CLASSES)  # noqa: E731
    acc = lambda yy, pp: accuracy_score(yy, np.array(CLASSES)[pp.argmax(1)])  # noqa: E731

    o, t = odds, test[odds]
    mkt = t[['p_away_pre', 'p_draw_pre', 'p_home_pre']].to_numpy()
    print(f'\nholdout WITH odds ({o.sum()} rows, {t.match_id.nunique()} matches):')
    print(f'  v2 {ll(y[o], p2[o]):.4f} (acc {acc(y[o], p2[o]):.3f})   '
          f'v1 {ll(y[o], base[o]):.4f} (acc {acc(y[o], base[o]):.3f})   odds {ll(y[o], mkt):.4f}')
    n = ~odds
    print(f'holdout WITHOUT odds ({n.sum()} rows) - v1 cannot predict these:')
    print(f'  v2 {ll(y[n], p2[n]):.4f} (acc {acc(y[n], p2[n]):.3f})   '
          f'no-odds base {ll(y[n], base[n]):.4f} (acc {acc(y[n], base[n]):.3f})')

    print('\nlogloss by minute, odds rows (v2 / v1):')
    mins = t['minute'].to_numpy()
    for lo in range(0, 91, 15):
        m = (mins >= lo) & (mins < lo + 15) if lo < 90 else mins == 90
        if m.sum() > 50:
            print(f'  {lo:>2}-{min(lo + 14, 90):>2}: {ll(y[o][m], p2[o][m]):.4f} / '
                  f'{ll(y[o][m], base[o][m]):.4f}   n={m.sum()}')

    fit_stack(bundle, df)  # evaluated above; ship the version that saw every match
    out = ROOT / 'models' / 'model_v2.pkl'
    joblib.dump(bundle, out)
    print(f'\nsaved {out}')


if __name__ == '__main__':
    assert parse_minute("45+2'") == 45 and parse_minute('HT') == 45
    assert parse_minute('FT') is None and parse_minute("97'") == 90
    assert np.allclose(parse_odds({'1': '9/25', 'X': 'EVS', '2': '13/2'}), (1.36, 2.0, 7.5))
    assert parse_odds({'1': None, 'X': '2', '2': '3'}) is None
    main()
