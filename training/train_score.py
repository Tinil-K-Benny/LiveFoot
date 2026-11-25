"""Final-score model: goals each side still scores -> likely final scorelines.

Usage: python training/train_score.py
Needs data/snapshots.csv (v1's data) and data/snapshots_v2.csv with final_h /
final_a (python training/train_v2.py --rebuild). Output: models/score_model.pkl,
used through app.features.goals_to_come + scorelines.

Stage 1: Poisson boosted model per side on v1's 95k matches, target = goals
between the snapshot and 90' (that data has no stoppage-time goals). Odds are
hidden for a third of the matches so the same model serves no-odds games.
Stage 2: Poisson GLM per side on the v2 dataset's true final scores,
correcting stage 1 with live stats and the time left (see score_matrix).
"""
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import PoissonRegressor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.features import FEATURE_NAMES, base_goals, goals_to_come, score_matrix  # noqa: E402

HOLDOUT_FRAC = 0.15
ODDS = ['p_home_pre', 'p_draw_pre', 'p_away_pre']


def old_rows():
    old = pd.read_csv(ROOT / 'data' / 'snapshots.csv')
    end = old[old.minute == 90].set_index('match_id')[['h_goals', 'a_goals']]
    old = old[old.minute < 90].join(end, on='match_id', rsuffix='_90')
    old['more_h'] = old.h_goals_90 - old.h_goals
    old['more_a'] = old.a_goals_90 - old.a_goals
    ids = old.match_id.unique()
    hide = np.random.default_rng(0).choice(ids, size=len(ids) // 3, replace=False)
    old.loc[old.match_id.isin(hide), ODDS] = np.nan
    return old


def fit_stage2(bundle, d):
    Z = score_matrix(d, *base_goals(bundle, d))
    for side in 'ha':
        bundle[f'glm_{side}'] = PoissonRegressor(alpha=1e-4, max_iter=3000).fit(Z, d[f'more_{side}'])


def report(name, d, lh, la):
    """Exact-score log-likelihood and most-likely-score hit rate, by minute."""
    mh, ma = d.more_h.to_numpy(), d.more_a.to_numpy()
    ll = poisson.logpmf(mh, lh) + poisson.logpmf(ma, la)
    hit = (np.floor(lh) == mh) & (np.floor(la) == ma)  # joint mode of independent Poissons
    mins = d.minute.to_numpy()
    cells = []
    for lo in (0, 30, 60, 75):
        m = (mins >= lo) & (mins < {0: 30, 30: 60, 60: 75, 75: 91}[lo])
        cells.append(f'{-ll[m].mean():.3f}/{hit[m].mean():.0%}')
    print(f'  {name:24s} all {-ll.mean():.3f}/{hit.mean():.0%}   ' + '  '.join(cells))


def draw_check(d, lh, la, cap=10):
    k = np.arange(cap + 1)
    ph = poisson.pmf(k[None, :], lh[:, None])
    pa = poisson.pmf(k[None, :], la[:, None])
    diff = (d.h_goals - d.a_goals).to_numpy()
    p_draw = np.array([sum(ph[r, i] * pa[r, j] for i in k for j in k if diff[r] + i - j == 0)
                       for r in range(len(d))])
    print(f'  draws: predicted {p_draw.mean():.3f}, actual {(d.final_h == d.final_a).mean():.3f}')


def main():
    old = old_rows()
    print(f'stage 1 on {old.match_id.nunique()} matches, {len(old)} rows...')
    bundle = {s: HistGradientBoostingRegressor(loss='poisson', max_iter=300, learning_rate=0.05,
                                               random_state=0).fit(old[FEATURE_NAMES], old[f'more_{s[-1]}'])
              for s in ('base_h', 'base_a')}

    new = pd.read_csv(ROOT / 'data' / 'snapshots_v2.csv')
    assert 'final_h' in new, 'rebuild the v2 CSV: python training/train_v2.py --rebuild'
    new['ts'] = pd.to_datetime(new['ts'])
    new['more_h'] = new.final_h - new.h_goals
    new['more_a'] = new.final_a - new.a_goals
    matches = new.groupby('match_id')['ts'].first().sort_values()
    n_hold = int(len(matches) * HOLDOUT_FRAC)
    hold = new.match_id.isin(set(matches.index[-n_hold:]))
    train, test = new[~hold], new[hold]
    fit_stage2(bundle, train)

    # naive baseline: the training set's goal rate per side, spread over the time left
    left_tr = (94 - np.minimum(train.minute, 90)).to_numpy()
    left_te = (94 - np.minimum(test.minute, 90)).to_numpy()
    rate_h, rate_a = train.more_h.sum() / left_tr.sum(), train.more_a.sum() / left_tr.sum()
    print(f'\nholdout {len(test)} rows / {test.match_id.nunique()} matches - '
          'exact final score: neg log-lik (lower = better) / most-likely score correct')
    print(f'  {"":24s} {"":10s}  0-29\'         30-59\'        60-74\'        75-90\'')
    report('naive goal rate', test, rate_h * left_te, rate_a * left_te)
    report('stage 1 (old data only)', test, *base_goals(bundle, test))
    lh, la = goals_to_come(bundle, test)
    report('final model', test, lh, la)
    draw_check(test, lh, la)

    fit_stage2(bundle, new)  # evaluated above; ship the version that saw every match
    out = ROOT / 'models' / 'score_model.pkl'
    joblib.dump(bundle, out)
    print(f'\nsaved {out}')


if __name__ == '__main__':
    main()
