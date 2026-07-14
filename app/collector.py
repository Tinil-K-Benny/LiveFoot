"""Unattended live-match collector -> data/live.db.

Polls `fixtures?live=all` every POLL_S seconds and upserts each live match's
raw event log into `matches`. When a match finishes, its events are expanded
into MINUTE-BY-MINUTE rows (0..90) in `snapshots` — same timing rules as the
training data (45+x visible from minute 46, 90+x in no snapshot but in the
result) — so no extra API calls are needed for minute-level data.
Exits after WINDOW_H hours so Task Scheduler can relaunch it nightly.

Task Scheduler (run daily at your match window, e.g. 18:00):
  schtasks /create /tn "football-collector" /sc daily /st 18:00 ^
    /tr "python \"P:\\Projects\\live football prediction model\\app\\collector.py\""
(API_FOOTBALL_KEY and ODDS_API_KEY must be set as user environment variables.)
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import api_client  # noqa: E402

POLL_S = 600
WINDOW_H = 4
MAX_STATS_MATCHES = 5  # in-play stats cost 1 call/match/poll; only odds-covered matches, capped
DB = ROOT / 'data' / 'live.db'

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    fixture_id INTEGER PRIMARY KEY,
    league TEXT, home TEXT, away TEXT, kickoff TEXT,
    odds_h REAL, odds_x REAL, odds_a REAL,
    events TEXT,            -- latest raw event JSON from the live feed
    result TEXT             -- Home/Draw/Away once finished, else NULL
);
CREATE TABLE IF NOT EXISTS raw_api (   -- every payload as received, for future re-processing
    captured_at INTEGER,
    kind TEXT,              -- 'live' (full live=all response) or 'result' (one fixture at FT)
    payload TEXT            -- raw JSON; ponytail: plain text, gzip it if the DB ever bothers you
);
CREATE TABLE IF NOT EXISTS snapshots (
    fixture_id INTEGER, minute INTEGER,
    h_goals INTEGER, a_goals INTEGER,
    h_yellows INTEGER, a_yellows INTEGER,
    h_reds INTEGER, a_reds INTEGER,
    odds_h REAL, odds_x REAL, odds_a REAL,
    result TEXT,
    PRIMARY KEY (fixture_id, minute)
);
CREATE TABLE IF NOT EXISTS snapshot_for_training (
    fixture_id INTEGER, minute INTEGER,
    h_goals INTEGER, a_goals INTEGER,
    h_yellows INTEGER, a_yellows INTEGER,
    h_reds INTEGER, a_reds INTEGER,
    odds_h REAL, odds_x REAL, odds_a REAL,
    h_possession INTEGER, a_possession INTEGER,
    h_shots_on_goal INTEGER, a_shots_on_goal INTEGER,
    h_total_shots INTEGER, a_total_shots INTEGER,
    h_corner_kicks INTEGER, a_corner_kicks INTEGER,
    result TEXT,
    PRIMARY KEY (fixture_id, minute)
);
"""

FINISHED = {'FT', 'AET', 'PEN'}


def expand_snapshots(con, fixture, odds):
    """Events -> one snapshot row per minute 0..90. Skips (with a warning) if
    the reconstructed final score disagrees with the API score, so bad
    own-goal/event data never lands in training rows."""
    fid = fixture['fixture']['id']
    h, a = fixture['goals']['home'], fixture['goals']['away']
    result = 'Home' if h > a else 'Away' if a > h else 'Draw'
    events = api_client.event_stream(fixture)

    if (sum(1 for _, i in events if i == 0), sum(1 for _, i in events if i == 1)) != (h, a):
        print(f'  ! {fid}: events do not reproduce final score {h}-{a}; snapshots skipped')
        return result

    counts = [0] * 6
    ev_i = 0
    rows = []
    for t in range(91):
        while ev_i < len(events) and events[ev_i][0] <= t:
            counts[events[ev_i][1]] += 1
            ev_i += 1
        rows.append((fid, t, *counts, *odds, result))
    con.executemany('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    return result


def finalize(con, fixture_id):
    fx = api_client.fixture_result(fixture_id)
    if not fx:
        return
    con.execute('INSERT INTO raw_api VALUES (?,?,?)',
                (int(time.time()), 'result', json.dumps(fx)))
    if fx['fixture']['status']['short'] not in FINISHED:
        con.commit()
        return  # postponed/abandoned or not actually over; leave result NULL
    odds = api_client.prematch_odds(fx) or (None, None, None)
    result = expand_snapshots(con, fx, odds)
    con.execute('UPDATE matches SET result=?, events=? WHERE fixture_id=?',
                (result, json.dumps(fx.get('events') or []), fixture_id))
    con.execute('UPDATE snapshot_for_training SET result=? WHERE fixture_id=?',
                (result, fixture_id))
    con.commit()
    print(f"  finalized {fixture_id}: {fx['goals']['home']}-{fx['goals']['away']} ({result})")


def main():
    DB.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    api_client.warm_odds_caches()  # grab pre-match lines before kickoffs
    seen = set()
    stat_tracked = set()  # fixtures we sample in-play stats for (odds-covered, first come)
    deadline = time.time() + WINDOW_H * 3600

    while time.time() < deadline:
        try:
            live = api_client.live_matches()
            con.execute('INSERT INTO raw_api VALUES (?,?,?)',
                        (int(time.time()), 'live', json.dumps(live)))
            ids = set()
            for fx in live:
                fid = fx['fixture']['id']
                ids.add(fid)
                odds = api_client.prematch_odds(fx) or (None, None, None)
                con.execute(
                    'INSERT OR REPLACE INTO matches VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                    (fid, fx['league']['name'], fx['teams']['home']['name'],
                     fx['teams']['away']['name'], fx['fixture']['date'],
                     *odds, json.dumps(fx.get('events') or [])))
                if odds[0] and (fid in stat_tracked or len(stat_tracked) < MAX_STATS_MATCHES):
                    stat_tracked.add(fid)
                    try:
                        stats = api_client.fixture_statistics(fx)
                        st = api_client.match_state(fx)
                        con.execute('INSERT INTO raw_api VALUES (?,?,?)',
                                    (int(time.time()), 'stats', json.dumps(
                                        {'fixture_id': fid, 'minute': st['minute'],
                                         'response': stats['raw']})))
                        
                        fstats = stats['flat']
                        def ext(k):
                            v = fstats.get(k)
                            if v is None: return 0
                            if isinstance(v, str) and v.endswith('%'): return int(v[:-1])
                            try: return int(v)
                            except Exception: return 0

                        con.execute('''
                            INSERT OR REPLACE INTO snapshot_for_training
                            (fixture_id, minute, h_goals, a_goals, h_yellows, a_yellows, h_reds, a_reds,
                             odds_h, odds_x, odds_a, h_possession, a_possession, h_shots_on_goal, a_shots_on_goal,
                             h_total_shots, a_total_shots, h_corner_kicks, a_corner_kicks, result)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        ''', (
                            fid, st['minute'], st['h_goals'], st['a_goals'],
                            st['h_yellows'], st['a_yellows'], st['h_reds'], st['a_reds'],
                            odds[0], odds[1], odds[2],
                            ext('h_ball_possession'), ext('a_ball_possession'),
                            ext('h_shots_on_goal'), ext('a_shots_on_goal'),
                            ext('h_total_shots'), ext('a_total_shots'),
                            ext('h_corner_kicks'), ext('a_corner_kicks')
                        ))
                    except Exception as e:  # one match's stats failing shouldn't skip the rest
                        print(f'  stats fetch failed for {fid}: {e}')
            con.commit()
            stat_tracked &= ids  # finished matches free their stats slot
            print(f'{time.strftime("%H:%M")} tracking {len(ids)} live matches, '
                  f'stats for {len(stat_tracked)}')
            for gone in seen - ids:  # dropped out of live feed -> finished
                finalize(con, gone)
            seen = ids
        except Exception as e:  # ponytail: log and keep polling; API blips are routine
            print(f'poll failed: {e}')
        time.sleep(POLL_S)

    for fid in seen:  # window ended with matches still live; stamp what's done
        finalize(con, fid)
    con.close()


if __name__ == '__main__':
    main()
