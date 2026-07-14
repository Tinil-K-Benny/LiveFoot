"""The one place match state becomes a model feature vector (train/serve parity)."""

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


if __name__ == '__main__':
    f = build_features(60, 2, 0, 1, 3, 0, 1, 2.15, 2.95, 2.4)
    assert list(f) == FEATURE_NAMES
    assert f['goal_diff'] == 2 and f['red_diff'] == -1
    assert abs(f['p_home_pre'] + f['p_draw_pre'] + f['p_away_pre'] - 1) < 1e-9
    print('features.py self-check ok')
