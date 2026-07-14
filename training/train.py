"""Train the live-outcome model from snapshots.csv.

Usage: python training/train.py [path/to/snapshots.csv]
Default input: data/snapshots.csv (your Phase-1 output).
Output: models/model.pkl  ({'model': ..., 'features': ..., 'classes': ...})
"""
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, log_loss

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.features import FEATURE_NAMES  # noqa: E402

HOLDOUT_FRAC = 0.15


def main():
    csv = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'data' / 'snapshots.csv'
    df = pd.read_csv(csv)
    df['date'] = pd.to_datetime(df['date'], format='%d.%m.%Y')

    # time-based split on whole matches: newest 15% of matches are the holdout
    matches = df.groupby('match_id')['date'].first().sort_values()
    n_hold = int(len(matches) * HOLDOUT_FRAC)
    hold_ids = set(matches.index[-n_hold:])
    test_mask = df['match_id'].isin(hold_ids)
    train, test = df[~test_mask], df[test_mask]
    print(f'{len(matches)} matches -> train {len(train)} rows, holdout {len(test)} rows '
          f'(holdout from {matches.iloc[-n_hold].date()})')

    model = HistGradientBoostingClassifier(random_state=0)
    model.fit(train[FEATURE_NAMES], train['result'])

    classes = list(model.classes_)  # e.g. ['Away', 'Draw', 'Home']
    proba = model.predict_proba(test[FEATURE_NAMES])
    base = test[['p_away_pre', 'p_draw_pre', 'p_home_pre']].to_numpy()
    base = base[:, [classes.index(c) for c in ['Away', 'Draw', 'Home']]] \
        if classes != ['Away', 'Draw', 'Home'] else base
    y = test['result'].to_numpy()

    pred = np.array(classes)[proba.argmax(axis=1)]
    print(f"\noverall: model logloss {log_loss(y, proba, labels=classes):.4f} "
          f"vs odds baseline {log_loss(y, base, labels=['Away', 'Draw', 'Home']):.4f}, "
          f"model accuracy {accuracy_score(y, pred):.3f}")

    print('\nper-minute logloss (model vs pre-match odds baseline):')
    for minute in sorted(test['minute'].unique()):
        m = (test['minute'] == minute).to_numpy()
        ll_m = log_loss(y[m], proba[m], labels=classes)
        ll_b = log_loss(y[m], base[m], labels=['Away', 'Draw', 'Home'])
        print(f"  min {minute:>2}: model {ll_m:.4f}  baseline {ll_b:.4f}  "
              f"{'BEATS' if ll_m < ll_b else 'loses to'} baseline")

    out = ROOT / 'models' / 'model.pkl'
    joblib.dump({'model': model, 'features': FEATURE_NAMES, 'classes': classes}, out)
    print(f'\nsaved {out}')


if __name__ == '__main__':
    main()
