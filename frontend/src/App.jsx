import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

const API = 'http://localhost:5000/api'
const LIVE = new Set(['1H', 'HT', '2H', 'ET', 'BT', 'P'])
const SERIES = [
  { key: 'home', label: 'Home', cls: 'home' },
  { key: 'draw', label: 'Draw', cls: 'draw' },
  { key: 'away', label: 'Away', cls: 'away' },
]

const pct = v => `${(v * 100).toFixed(0)}%`

function StatusChip({ status, minute }) {
  if (LIVE.has(status)) {
    return <span className="chip live-chip"><span className="pulse" />{status === 'HT' ? 'HT' : `${minute}′`}</span>
  }
  return <span className="chip">{status}</span>
}

function Cards({ yellows, reds }) {
  if (!yellows && !reds) return null
  return (
    <span className="cards">
      {yellows > 0 && <span className="card yellow">{yellows}</span>}
      {reds > 0 && <span className="card red">{reds}</span>}
    </span>
  )
}

function ProbBar({ label, team, value, cls }) {
  return (
    <div className="bar-row">
      <div className="bar-head">
        <span className="bar-label"><span className={`dot ${cls}`} />{label}{team ? ` · ${team}` : ''}</span>
        <span className="bar-value">{(value * 100).toFixed(1)}%</span>
      </div>
      <div className="bar-track">
        <div className={`bar-fill ${cls}`} style={{ width: `${value * 100}%` }} />
      </div>
    </div>
  )
}

// probability-over-time chart: 3 series, hairline grid, crosshair + tooltip
function History({ history }) {
  const [hover, setHover] = useState(null)
  const svgRef = useRef(null)
  const W = 640, H = 230, L = 40, R = 56, T = 12, B = 26
  const x = m => L + (m / 95) * (W - L - R)
  const y = p => T + (1 - p) * (H - T - B)

  if (history.length < 2) {
    return <p className="muted">Probability history builds as the match is polled…</p>
  }
  const path = key => history.map((h, i) => `${i ? 'L' : 'M'}${x(h.minute).toFixed(1)},${y(h[key]).toFixed(1)}`).join(' ')
  const last = history[history.length - 1]

  const onMove = e => {
    const box = svgRef.current.getBoundingClientRect()
    const mx = ((e.clientX - box.left) / box.width) * W
    let best = 0
    history.forEach((h, i) => { if (Math.abs(x(h.minute) - mx) < Math.abs(x(history[best].minute) - mx)) best = i })
    setHover(best)
  }
  const hv = hover != null ? history[hover] : null

  return (
    <div className="chart-wrap">
      <div className="legend">
        {SERIES.map(s => <span key={s.key} className="legend-item"><span className={`dot ${s.cls}`} />{s.label}</span>)}
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="chart"
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {[0, 0.25, 0.5, 0.75, 1].map(p => (
          <g key={p}>
            <line x1={L} x2={W - R} y1={y(p)} y2={y(p)} className="grid" />
            <text x={L - 6} y={y(p) + 3.5} className="tick" textAnchor="end">{pct(p)}</text>
          </g>
        ))}
        {[0, 15, 30, 45, 60, 75, 90].map(m => (
          <text key={m} x={x(m)} y={H - 8} className="tick" textAnchor="middle">{m}′</text>
        ))}
        {SERIES.map(s => <path key={s.key} d={path(s.key)} className={`line ${s.cls}`} />)}
        {SERIES.map(s => (
          <text key={s.key} x={x(last.minute) + 6} y={y(last[s.key]) + 3.5} className="end-label">
            {pct(last[s.key])}
          </text>
        ))}
        {hv && (
          <g>
            <line x1={x(hv.minute)} x2={x(hv.minute)} y1={T} y2={H - B} className="crosshair" />
            {SERIES.map(s => <circle key={s.key} cx={x(hv.minute)} cy={y(hv[s.key])} r="4" className={`pt ${s.cls}`} />)}
          </g>
        )}
      </svg>
      {hv && (
        <div className="tooltip" style={{ left: `${(x(hv.minute) / W) * 100}%` }}>
          <div className="tooltip-title">{hv.minute}′</div>
          {SERIES.map(s => (
            <div key={s.key} className="tooltip-row">
              <span className={`dot ${s.cls}`} />{s.label}<b>{pct(hv[s.key])}</b>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const STAT_ROWS = [
  ['total_shots', 'Shots'],
  ['shots_on_goal', 'On target'],
  ['expected_goals', 'xG'],
  ['corner_kicks', 'Corners'],
  ['fouls', 'Fouls'],
]

function Stats({ stats }) {
  if (!stats) return null
  const poss = parseInt(stats.h_ball_possession) || null
  const rows = STAT_ROWS.filter(([k]) => stats[`h_${k}`] != null || stats[`a_${k}`] != null)
  if (!poss && rows.length === 0) return null
  return (
    <section className="section">
      <h3>Match stats</h3>
      {poss != null && (
        <div className="poss">
          <span className="poss-val">{poss}%</span>
          <div className="poss-track">
            <div className="poss-home" style={{ width: `${poss}%` }} />
          </div>
          <span className="poss-val">{100 - poss}%</span>
        </div>
      )}
      {rows.map(([k, label]) => (
        <div key={k} className="stat-row">
          <span className="stat-val">{stats[`h_${k}`] ?? 0}</span>
          <span className="stat-label">{label}</span>
          <span className="stat-val">{stats[`a_${k}`] ?? 0}</span>
        </div>
      ))}
    </section>
  )
}

function MatchPanel({ fixtureId }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    setData(null); setErr(null)
    let stop = false
    const poll = () =>
      fetch(`${API}/live/${fixtureId}`)
        .then(r => r.json().then(j => (r.ok ? j : Promise.reject(j.error))))
        .then(j => { if (!stop) { setData(j); setErr(null) } })
        .catch(e => { if (!stop) setErr(String(e)) })
    poll()
    const id = setInterval(poll, 600_000) // backend live cache TTL is 10 min
    return () => { stop = true; clearInterval(id) }
  }, [fixtureId])

  if (err) return <div className="panel"><p className="error">{err}</p></div>
  if (!data) return <div className="panel"><p className="muted">Loading…</p></div>

  if (data.prematch) {
    const started = data.h_goals != null
    return (
      <div className="panel">
        <div className="scoreboard">
          <div className="team"><span className="team-name">{data.home}</span></div>
          <div className="score-mid">
            <span className="score">{started ? `${data.h_goals}–${data.a_goals}` : 'vs'}</span>
            <span className="chip">{data.status === 'NS' ? `${data.kickoff.slice(11, 16)} kick-off` : data.status}</span>
          </div>
          <div className="team right"><span className="team-name">{data.away}</span></div>
        </div>
        {data.probs ? (
          <section className="section">
            <h3>Pre-match odds</h3>
            <div className="tiles odds-tiles">
              {SERIES.map(s => (
                <div key={s.key} className="tile">
                  <span className="tile-value">{data.odds[s.key].toFixed(2)}</span>
                  <span className="tile-label"><span className={`dot ${s.cls}`} /> {s.label}</span>
                </div>
              ))}
            </div>
            <ProbBar label="Home" team={data.home} value={data.probs.home} cls="home" />
            <ProbBar label="Draw" value={data.probs.draw} cls="draw" />
            <ProbBar label="Away" team={data.away} value={data.probs.away} cls="away" />
            <p className="muted note">Market-implied probabilities (margin removed). Live model
              predictions start at kick-off.</p>
          </section>
        ) : (
          <p className="muted note">No odds coverage for this fixture's league.</p>
        )}
      </div>
    )
  }

  return (
    <div className="panel">
      <div className="scoreboard">
        <div className="team">
          <span className="team-name">{data.home}</span>
          <Cards yellows={data.h_yellows} reds={data.h_reds} />
        </div>
        <div className="score-mid">
          <span className="score">{data.h_goals}–{data.a_goals}</span>
          <StatusChip status={data.status} minute={data.minute} />
        </div>
        <div className="team right">
          <span className="team-name">{data.away}</span>
          <Cards yellows={data.a_yellows} reds={data.a_reds} />
        </div>
      </div>

      {data.probs ? (
        <>
          <section className="section">
            <h3>Win probability</h3>
            <ProbBar label="Home" team={data.home} value={data.probs.home} cls="home" />
            <ProbBar label="Draw" value={data.probs.draw} cls="draw" />
            <ProbBar label="Away" team={data.away} value={data.probs.away} cls="away" />
          </section>
          <section className="section">
            <h3>Probability over time</h3>
            <History history={data.history} />
          </section>
          <Stats stats={data.stats} />
        </>
      ) : (
        <section className="section">
          <h3>Match stats</h3>
          <div className="tiles">
            <div className="tile"><span className="tile-value">{data.minute}′</span><span className="tile-label">minute</span></div>
            <div className="tile"><span className="tile-value">{data.h_yellows + data.a_yellows}</span><span className="tile-label">yellow cards</span></div>
            <div className="tile"><span className="tile-value">{data.h_reds + data.a_reds}</span><span className="tile-label">red cards</span></div>
            <div className="tile"><span className="tile-value">{data.status}</span><span className="tile-label">status</span></div>
          </div>
          <p className="muted note">No pre-match odds were captured for this fixture (it was already
            in play when the app started, or its league isn't covered) — predictions need them,
            so live stats are shown instead.</p>
        </section>
      )}
    </div>
  )
}

// ---------- match feed ----------

// API-Football league ids, most popular first; unranked leagues sort after, alphabetically
const RANK = new Map([
  [1, 0],    // World Cup
  [4, 1],    // Euro
  [15, 2],   // Club World Cup
  [2, 3],    // Champions League
  [3, 4],    // Europa League
  [848, 5],  // Conference League
  [5, 6],    // Nations League
  [39, 7],   // Premier League
  [140, 8],  // La Liga
  [135, 9],  // Serie A
  [78, 10],  // Bundesliga
  [61, 11],  // Ligue 1
  [13, 12],  // Copa Libertadores
  [45, 13],  // FA Cup
  [143, 14], // Copa del Rey
  [137, 15], // Coppa Italia
  [81, 16],  // DFB Pokal
  [88, 17],  // Eredivisie
  [94, 18],  // Primeira Liga
  [203, 19], // Süper Lig
  [71, 20],  // Brasileirão Série A
  [128, 21], // Liga Profesional Argentina
  [253, 22], // MLS
])

const FINISHED = new Set(['FT', 'AET', 'PEN'])
const hideImg = e => { e.currentTarget.style.visibility = 'hidden' }
const localTime = iso => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

function Chevron({ className }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M5.5 3l5 5-5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function TimeCell({ f }) {
  if (LIVE.has(f.status)) {
    return (
      <span className="row-time is-live">
        <span className="pulse" />
        {f.status === 'HT' ? 'HT' : f.minute != null ? `${f.minute}′` : 'LIVE'}
      </span>
    )
  }
  if (f.status === 'NS' || f.status === 'TBD') return <span className="row-time">{localTime(f.kickoff)}</span>
  return <span className="row-time done">{FINISHED.has(f.status) ? 'FT' : f.status}</span>
}

function MatchRow({ f, onSelect }) {
  const played = f.h_goals != null
  return (
    <li>
      <button type="button" className={`match-row ${LIVE.has(f.status) ? 'is-live' : ''}`}
              onClick={() => onSelect(f.fixture_id)}
              aria-label={`${f.home} versus ${f.away}, open match center`}>
        <TimeCell f={f} />
        <span className="side home">
          <span className="row-team">{f.home}</span>
          <img className="crest" src={f.home_logo} alt="" loading="lazy" onError={hideImg} />
        </span>
        <span className={`row-score ${played ? '' : 'tbd'}`}>
          {played ? `${f.h_goals} – ${f.a_goals}` : '–'}
        </span>
        <span className="side away">
          <img className="crest" src={f.away_logo} alt="" loading="lazy" onError={hideImg} />
          <span className="row-team">{f.away}</span>
        </span>
        <Chevron className="row-go" />
      </button>
    </li>
  )
}

function Competition({ group, open, onToggle, onSelect }) {
  return (
    <section className="comp">
      <button type="button" className="comp-head" aria-expanded={open} onClick={onToggle}>
        <img className="comp-flag" src={group.flag || group.league_logo} alt="" loading="lazy" onError={hideImg} />
        <span className="comp-name">{group.league}</span>
        <span className="comp-country">{group.country}</span>
        <span className="comp-count">{group.fixtures.length}</span>
        <Chevron className={`comp-chev ${open ? 'open' : ''}`} />
      </button>
      {open && (
        <ul className="matches">
          {group.fixtures.map(f => <MatchRow key={f.fixture_id} f={f} onSelect={onSelect} />)}
        </ul>
      )}
    </section>
  )
}

function TopBar() {
  return (
    <header className="topbar">
      <h1 className="wordmark">Live Football <span>Predictions</span></h1>
      <span className="today">{new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}</span>
    </header>
  )
}

export default function App() {
  const [fixtures, setFixtures] = useState([])
  const [err, setErr] = useState(null)
  const [selected, setSelected] = useState(null)
  const [closed, setClosed] = useState(() => new Set())

  useEffect(() => {
    const poll = () =>
      fetch(`${API}/fixtures`)
        .then(r => r.json().then(j => (r.ok ? j : Promise.reject(j.error))))
        .then(j => { setFixtures(j); setErr(null) })
        .catch(e => setErr(String(e)))
    poll()
    const id = setInterval(poll, 600_000) // backend live cache TTL is 10 min
    return () => clearInterval(id)
  }, [])

  const groups = useMemo(() => {
    const by = new Map()
    for (const f of fixtures) {
      if (!by.has(f.league_id)) {
        by.set(f.league_id, { league_id: f.league_id, league: f.league, country: f.country,
                              flag: f.flag, league_logo: f.league_logo, fixtures: [] })
      }
      by.get(f.league_id).fixtures.push(f)
    }
    const out = [...by.values()]
    out.forEach(g => g.fixtures.sort((a, b) => a.kickoff.localeCompare(b.kickoff)))
    out.sort((a, b) =>
      (RANK.get(a.league_id) ?? 999) - (RANK.get(b.league_id) ?? 999)
      || `${a.country} ${a.league}`.localeCompare(`${b.country} ${b.league}`))
    return out
  }, [fixtures])

  const toggle = id => setClosed(prev => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  if (selected != null) {
    return (
      <div className="app">
        <TopBar />
        <main className="feed">
          <button type="button" className="back" onClick={() => setSelected(null)}>
            <Chevron className="back-chev" /> Today's matches
          </button>
          <MatchPanel fixtureId={selected} />
        </main>
      </div>
    )
  }

  return (
    <div className="app">
      <TopBar />
      <main className="feed">
        {err && <p className="error">{err}</p>}
        {!err && fixtures.length === 0 && <p className="muted">Loading today's matches…</p>}
        {groups.map(g => (
          <Competition key={g.league_id} group={g} open={!closed.has(g.league_id)}
                       onToggle={() => toggle(g.league_id)} onSelect={setSelected} />
        ))}
      </main>
    </div>
  )
}
