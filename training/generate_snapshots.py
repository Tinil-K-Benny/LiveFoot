"""full_data.csv (one row per match, event log in INC) -> data/snapshots.csv,
one row per (match, minute in {0,10,...,90}).

Timing rules (mirrors live conditions):
- snapshot at minute t contains only events with minute <= t
- 45+x' events -> effective 45.9, so they appear from the minute-50 snapshot
- 90+x' events -> effective 90.9, so they appear in NO snapshot (at 90' you
  can't yet see the 90+3' goal) but they do affect the final-result label
- Own_Home / Own_Away are own goals credited to the named side's SCORE
"""
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.features import implied_probs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_MINUTES = range(0, 91, 10)
EV_RE = re.compile(r"(\d+)(?:\+(\d+))?' ([A-Za-z_]+)")

COUNTERS = {  # event type -> index in [h_goals, a_goals, h_yel, a_yel, h_red, a_red]
    'Goal_Home': 0, 'Own_Home': 0, 'Goal_Away': 1, 'Own_Away': 1,
    'Yellow_Home': 2, 'Yellow_Away': 3, 'Red_Card_Home': 4, 'Red_Card_Away': 5,
}


def parse_events(inc):
    """[(effective_minute, event_type), ...] sorted by minute."""
    out = [(int(m[0]) + (0.9 if m[1] else 0.0), m[2]) for m in EV_RE.findall(inc)]
    out.sort(key=lambda e: e[0])
    return out


def main():
    df = pd.read_csv(ROOT / 'datasets' / 'Min by min' / 'full_data.csv')
    df = df.dropna(subset=['INC', 'WIN', 'H_BET', 'X_BET', 'A_BET'])

    rows, mismatches = [], 0
    for match_id, row in df.iterrows():
        events = parse_events(row['INC'])
        p_h, p_x, p_a = implied_probs(row['H_BET'], row['X_BET'], row['A_BET'])

        # walk events once; emit a snapshot each time we pass a snapshot minute
        counts = [0] * 6
        ev_i = 0
        for t in SNAPSHOT_MINUTES:
            while ev_i < len(events) and events[ev_i][0] <= t:
                idx = COUNTERS.get(events[ev_i][1])
                if idx is not None:
                    counts[idx] += 1
                ev_i += 1
            hg, ag, hy, ay, hr, ar = counts
            rows.append((match_id, row['League'], row['Date'], t,
                         hg, ag, hg - ag, hy, ay, hr, ar, hr - ar,
                         row['H_BET'], row['X_BET'], row['A_BET'],
                         p_h, p_x, p_a, row['WIN']))

        # self-check: all events (incl. 90+x) must reproduce the final score
        final = counts[:]
        for eff, ev in events[ev_i:]:
            idx = COUNTERS.get(ev)
            if idx is not None:
                final[idx] += 1
        if not pd.isna(row['H_Score']) and (final[0], final[1]) != (row['H_Score'], row['A_Score']):
            mismatches += 1

    snaps = pd.DataFrame(rows, columns=[
        'match_id', 'league', 'date', 'minute',
        'h_goals', 'a_goals', 'goal_diff', 'h_yellows', 'a_yellows',
        'h_reds', 'a_reds', 'red_diff', 'odds_h', 'odds_x', 'odds_a',
        'p_home_pre', 'p_draw_pre', 'p_away_pre', 'result'])

    out = ROOT / 'data' / 'snapshots.csv'
    out.parent.mkdir(exist_ok=True)
    snaps.to_csv(out, index=False)
    print(f'matches: {len(df)}, snapshot rows: {len(snaps)}, '
          f'score mismatches: {mismatches} ({mismatches / len(df):.2%})')


if __name__ == '__main__':
    main()
