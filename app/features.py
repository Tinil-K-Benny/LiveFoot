"""The one place match state becomes a model feature vector (train/serve parity)."""
import math

import numpy as np

FEATURE_NAMES = [
    'minute',
    'h_goals', 'a_goals', 'goal_diff',
    'h_yellows', 'a_yellows',
    'h_reds', 'a_reds', 'red_diff',
    'p_home_pre', 'p_draw_pre', 'p_away_pre',
]


def implied_probs(odds_h, odds_x, odds_a):
    """Decimal odds -> (p_home, p_draw, p_away), bookmaker margin removed."""
    inv = (1 / odds_h, 1 / odds_x, 1 / odds_a)
    total = sum(inv)
    return tuple(v / total for v in inv)


def parse_odds(o):
    """Live-feed 1X2 line {'1': '9/25', 'X': '15/4', '2': '13/2'} -> decimal
    (h, x, a), or None. Used by training (v2 dataset rows) and serving (live)."""
    def dec(v):
        s = str(v).strip().upper()
        if s in ('EVS', 'EVENS'):
            return 2.0
        if '/' in s:
            n, d = s.split('/')
            return 1 + float(n) / float(d)
        return float(s)
    try:
        out = tuple(dec(o[k]) for k in ('1', 'X', '2'))
        return out if all(v > 1 for v in out) else None
    except Exception:
        return None


def build_features(minute, h_goals, a_goals, h_yellows, a_yellows,
                   h_reds, a_reds, odds_h, odds_x, odds_a):
    p_h, p_x, p_a = implied_probs(odds_h, odds_x, odds_a)
    return {
        'minute': minute,
        'h_goals': h_goals, 'a_goals': a_goals, 'goal_diff': h_goals - a_goals,
        'h_yellows': h_yellows, 'a_yellows': a_yellows,
        'h_reds': h_reds, 'a_reds': a_reds, 'red_diff': h_reds - a_reds,
        'p_home_pre': p_h, 'p_draw_pre': p_x, 'p_away_pre': p_a,
    }


# --- v2 = a base model (v1 when odds exist, else a no-odds v1 twin) whose
# log-probs are corrected by a logistic regression on live stat differences.
# Only ~6k matches carry stats: a boosted tree on them overfits, this doesn't.
# ponytail: 3 stats; xG/possession/big chances added nothing at this data size,
# retry once the v2 dataset is several times bigger.
STAT_KEYS = ['shotsOnGoal', 'totalShotsOnGoal', 'cornerKicks']
NO_ODDS = [f for f in FEATURE_NAMES if not f.startswith('p_')]
CLASSES = ['Away', 'Draw', 'Home']
NAN = float('nan')


def stat_num(v):
    """'73%' -> 73, '45/68 (66%)' -> 45, '1.24' -> 1.24, 5 -> 5, junk -> NaN.
    Training rows hold live-feed display strings, live API gives numbers."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or '').strip()
    num = ''
    for ch in s:
        if ch.isdigit() or (ch == '.' and num):
            num += ch
        else:
            break
    return float(num) if num else NAN


FEATURE_NAMES_V2 = FEATURE_NAMES + [f'{s}_{k}' for k in STAT_KEYS for s in 'ha']


def build_features_v2(minute, h_goals, a_goals, h_yellows, a_yellows, h_reds, a_reds,
                      stats, odds=None):
    """stats: flat {key: {'home': v, 'away': v}} (live_feed.statistics format).
    odds: (dec_h, dec_x, dec_a) or None -> NaN probs (selects the no-odds base)."""
    f = build_features(minute, h_goals, a_goals, h_yellows, a_yellows, h_reds, a_reds,
                       *(odds or (2, 2, 2)))
    if not odds:
        f['p_home_pre'] = f['p_draw_pre'] = f['p_away_pre'] = NAN
    for k in STAT_KEYS:
        v = (stats or {}).get(k) or {}
        f[f'h_{k}'], f[f'a_{k}'] = stat_num(v.get('home')), stat_num(v.get('away'))
    return f


def stack_matrix(X, base):
    """log base probs + stat diffs, raw and scaled by time remaining (missing -> 0)."""
    diff = np.column_stack([(X[f'h_{k}'] - X[f'a_{k}']).fillna(0) for k in STAT_KEYS])
    rem = ((90 - X['minute']) / 90).to_numpy()[:, None]
    return np.hstack([np.log(np.clip(base, 1e-6, 1)), diff, diff * rem])


def base_probs(bundle, X):
    """(n,3) CLASSES-ordered probs from v1 where odds exist, the no-odds twin elsewhere."""
    odds = X['p_home_pre'].notna().to_numpy()
    base = bundle['base_noodds'].predict_proba(X[NO_ODDS])
    if odds.any():
        v1 = bundle['base_odds']
        idx = [list(v1.classes_).index(c) for c in CLASSES]
        base[odds] = v1.predict_proba(X[odds][FEATURE_NAMES])[:, idx]
    return base, odds


def predict_v2(bundle, X):
    """X: DataFrame with FEATURE_NAMES_V2 columns -> (n,3) probs in CLASSES order."""
    base, odds = base_probs(bundle, X)
    Z = stack_matrix(X, base)
    out = bundle['lr_noodds'].predict_proba(Z)
    if odds.any():
        out[odds] = bundle['lr_odds'].predict_proba(Z[odds])
    return out


# --- final-score model (training/train_score.py): goals each side still scores.
# Stage 1: a Poisson boosted model per side on v1's features (95k matches).
# Stage 2: a Poisson GLM per side on the v2 dataset's true final scores,
# correcting stage 1 with live stats and the time left, like v2 does for 1X2.
def score_matrix(X, lam_h, lam_a):
    """log base goals-to-come for both sides, stat diffs raw and x time remaining,
    log of minutes left (+4: stoppage), and whether pre-match odds exist."""
    diff = np.column_stack([(X[f'h_{k}'] - X[f'a_{k}']).fillna(0) for k in STAT_KEYS])
    rem = ((90 - X['minute']) / 90).to_numpy()[:, None]
    left = np.log(94 - np.minimum(X['minute'].to_numpy(), 90))[:, None]
    has_odds = X['p_home_pre'].notna().to_numpy(dtype=float)[:, None]
    return np.hstack([np.log(lam_h)[:, None], np.log(lam_a)[:, None], diff, diff * rem, left, has_odds])


def base_goals(bundle, X):
    return [np.clip(m.predict(X[FEATURE_NAMES]), 1e-4, None) for m in (bundle['base_h'], bundle['base_a'])]


def goals_to_come(bundle, X):
    """(expected further home goals, expected further away goals), one per row of X."""
    Z = score_matrix(X, *base_goals(bundle, X))
    return bundle['glm_h'].predict(Z), bundle['glm_a'].predict(Z)


def scorelines(h_goals, a_goals, more_h, more_a, top=6, cap=10):
    """Most likely final scores [(home, away, p)], current score plus
    independent Poisson goals to come.
    ponytail: independent Poisson slightly under-rates draws; add a
    Dixon-Coles low-score correction if the draw check in train_score.py drifts."""
    def pmf(lam):
        return [math.exp(-lam) * lam ** k / math.factorial(k) for k in range(cap + 1)]
    ph, pa = pmf(more_h), pmf(more_a)
    grid = [(h_goals + i, a_goals + j, ph[i] * pa[j]) for i in range(cap + 1) for j in range(cap + 1)]
    return sorted(grid, key=lambda t: -t[2])[:top]


if __name__ == '__main__':
    f = build_features(60, 2, 0, 1, 3, 0, 1, 2.15, 2.95, 2.4)
    assert list(f) == FEATURE_NAMES
    assert f['goal_diff'] == 2 and f['red_diff'] == -1
    assert abs(f['p_home_pre'] + f['p_draw_pre'] + f['p_away_pre'] - 1) < 1e-9
    assert stat_num('73%') == 73 and stat_num('45/68 (66%)') == 45
    assert stat_num('1.24') == 1.24 and stat_num(5) == 5 and stat_num(None) != stat_num(None)
    import pandas as pd
    st = {'shotsOnGoal': {'home': '4', 'away': 1}}
    g = build_features_v2(30, 1, 0, 2, 0, 0, 0, st, (2.0, 3.4, 4.0))
    assert list(g) == FEATURE_NAMES_V2
    assert g['h_shotsOnGoal'] == 4 and g['a_cornerKicks'] != g['a_cornerKicks']  # absent -> NaN
    g = build_features_v2(30, 1, 0, 0, 0, 0, 0, None)
    assert g['p_home_pre'] != g['p_home_pre']
    Z = stack_matrix(pd.DataFrame([build_features_v2(30, 1, 0, 2, 0, 0, 0, st)]),
                     [[0.2, 0.3, 0.5]])
    assert Z.shape == (1, 9) and Z[0, 3] == 3 and abs(Z[0, 6] - 2) < 1e-9 and Z[0, 4] == 0
    s = scorelines(1, 0, 0.6, 0.3)
    assert s[0][:2] == (1, 0) and abs(s[0][2] - math.exp(-0.9)) < 1e-12  # no more goals most likely
    assert abs(sum(p for *_, p in scorelines(1, 0, 0.6, 0.3, top=200)) - 1) < 1e-9
    assert scorelines(2, 2, 1e-4, 1e-4)[0][:2] == (2, 2)
    print('features.py self-check ok')
