import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

const API = import.meta.env.VITE_API || 'http://localhost:5000/api'
const LIST_POLL_MIN = 10  // match list (backend fixture/live-feed caches)
const MATCH_POLL_MIN = 8  // open match only: score, stats and a new prediction
const LIVE = new Set(['1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE'])
const FINISHED = new Set(['FT', 'AET', 'PEN'])
const STATUS_WORD = {
  '1H': '1st half', '2H': '2nd half', HT: 'Half-time', ET: 'Extra time', BT: 'Break',
  P: 'Penalties', NS: 'Upcoming', TBD: 'Upcoming', FT: 'Full time', AET: 'After extra time',
  PEN: 'Penalties', PST: 'Postponed', CANC: 'Cancelled', SUSP: 'Suspended', ABD: 'Abandoned',
}
const OUTCOMES = ['home', 'draw', 'away']
const NONE = []

const pct = v => Math.round(v * 100)
const hideImg = e => { e.currentTarget.style.visibility = 'hidden' }
const localTime = iso => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })

function fetchJson(path) {
  return fetch(`${API}${path}`).then(r => r.json().then(j => (r.ok ? j : Promise.reject(j.error))))
}

// poll `path` every `minutes`; returns [data, error]
function usePoll(path, minutes) {
  const [state, setState] = useState([null, null])
  useEffect(() => {
    setState([null, null])
    let stop = false
    const poll = () => fetchJson(path)
      .then(j => { if (!stop) setState([j, null]) })
      .catch(e => { if (!stop) setState(([d]) => [d, String(e)]) })
    poll()
    const id = setInterval(poll, minutes * 60_000)
    return () => { stop = true; clearInterval(id) }
  }, [path, minutes])
  return state
}

const Chevron = ({ dir = 'right', className = '' }) => (
  <svg className={`chev chev-${dir} ${className}`} width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

const Logo = ({ src, size = 24 }) => (
  <span className="logo" style={{ width: size, height: size }}>
    {src && <img src={src} alt="" loading="lazy" onError={hideImg} />}
  </span>
)

const LiveDot = () => <span className="dot" aria-hidden="true" />

// ---------- home ----------

function minuteLabel(f) {
  if (f.status === 'HT') return 'HT'
  if (LIVE.has(f.status)) return f.minute != null ? `${f.minute}′` : 'LIVE'
  if (f.status === 'NS' || f.status === 'TBD') return localTime(f.kickoff)
  return FINISHED.has(f.status) ? 'FT' : f.status
}

function LiveCard({ f, onSelect }) {
  const label = f.status === 'HT' ? 'Half-time' : `${minuteLabel(f)} · ${STATUS_WORD[f.status] ?? 'Live'}`
  return (
    <button type="button" className="live-card" onClick={() => onSelect(f.fixture_id)}>
      <span className="live-card-top">
        <span className="live-card-league">{f.league}</span>
        <span className="live-pill num"><LiveDot />{label}</span>
      </span>
      <span className="live-card-teams">
        <span className="team-line"><Logo src={f.home_logo} /><span className="ellipsis">{f.home}</span></span>
        <span className="big-score num">{f.h_goals}</span>
        <span className="team-line"><Logo src={f.away_logo} /><span className="ellipsis">{f.away}</span></span>
        <span className="big-score num">{f.a_goals}</span>
      </span>
    </button>
  )
}

function MatchRow({ f, onSelect }) {
  const live = LIVE.has(f.status)
  const ft = FINISHED.has(f.status)
  const played = f.h_goals != null
  const winner = ft ? (f.h_goals > f.a_goals ? 'home' : f.a_goals > f.h_goals ? 'away' : null) : null
  const side = s => (winner === s ? 'won' : winner ? 'lost' : '')
  return (
    <button type="button" className="match-row" onClick={() => onSelect(f.fixture_id)}>
      <span className="row-time">
        <span className={`row-minute num ${ft ? 'muted' : ''}`}>{live && <LiveDot />}{minuteLabel(f)}</span>
        <span className={`row-status ${live ? 'is-live' : ''}`}>{STATUS_WORD[f.status] ?? f.status}</span>
      </span>
      <span className="row-teams">
        <span className={`team-line ${side('home')}`}><Logo src={f.home_logo} size={20} /><span className="ellipsis">{f.home}</span></span>
        <span className={`row-score num ${side('home')}`}>{played ? f.h_goals : ''}</span>
        <span className={`team-line ${side('away')}`}><Logo src={f.away_logo} size={20} /><span className="ellipsis">{f.away}</span></span>
        <span className={`row-score num ${side('away')}`}>{played ? f.a_goals : ''}</span>
      </span>
      <Chevron className="row-chev" />
    </button>
  )
}

function Competition({ group, open, onToggle, onSelect }) {
  return (
    <section className="comp">
      <button type="button" className="comp-head" aria-expanded={open} onClick={onToggle}>
        <img className="comp-flag" src={group.flag || group.league_logo} alt="" loading="lazy" onError={hideImg} />
        <span className="comp-name">{group.league}</span>
        <span className="comp-country">{group.country}</span>
        <span className="comp-count num">{group.fixtures.length}</span>
        <Chevron dir={open ? 'down' : 'right'} />
      </button>
      {open && group.fixtures.map(f => <MatchRow key={f.fixture_id} f={f} onSelect={onSelect} />)}
    </section>
  )
}

// API-Football league ids, most popular first; unranked leagues sort after, alphabetically
const RANK = new Map([
  [1, 0], [4, 1], [15, 2], [2, 3], [3, 4], [848, 5], [5, 6], [39, 7], [140, 8], [135, 9],
  [78, 10], [61, 11], [13, 12], [45, 13], [143, 14], [137, 15], [81, 16], [88, 17], [94, 18],
  [203, 19], [71, 20], [128, 21], [253, 22],
])

const FILTERS = [
  ['all', 'All', () => true],
  ['live', 'Live', f => LIVE.has(f.status)],
  ['upcoming', 'Upcoming', f => f.status === 'NS' || f.status === 'TBD'],
  ['ft', 'Finished', f => FINISHED.has(f.status)],
]

function Home({ fixtures, err, onSelect }) {
  const [filter, setFilter] = useState('all')
  const [closed, setClosed] = useState(() => new Set())
  const list = fixtures ?? NONE

  const groups = useMemo(() => {
    const keep = FILTERS.find(([id]) => id === filter)[2]
    const by = new Map()
    for (const f of list.filter(keep)) {
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
  }, [list, filter])

  const live = list.filter(f => LIVE.has(f.status))
    .sort((a, b) => (RANK.get(a.league_id) ?? 999) - (RANK.get(b.league_id) ?? 999))
  const toggle = id => setClosed(prev => {
    const next = new Set(prev)
    if (!next.delete(id)) next.add(id)
    return next
  })

  return (
    <main className="page">
      {err && <p className="error">{err}</p>}
      {!fixtures && !err && <p className="muted">Loading today's matches…</p>}

      {live.length > 0 && (
        <section className="block" aria-labelledby="live-h">
          <div className="block-head">
            <LiveDot />
            <h2 id="live-h" className="h2">Live now</h2>
            <span className="muted small">{live.length} {live.length === 1 ? 'match' : 'matches'} in play</span>
          </div>
          <div className="live-grid">
            {live.map(f => <LiveCard key={f.fixture_id} f={f} onSelect={onSelect} />)}
          </div>
        </section>
      )}

      {fixtures && (
        <section className="block" aria-labelledby="all-h">
          <div className="block-head spread">
            <h2 id="all-h" className="h2">All matches</h2>
            <div className="seg-group" role="group" aria-label="Filter matches">
              {FILTERS.map(([id, label, keep]) => (
                <button key={id} type="button" className="seg" aria-pressed={filter === id}
                        onClick={() => setFilter(id)}>
                  {label}<span className="seg-count num">{list.filter(keep).length}</span>
                </button>
              ))}
            </div>
          </div>
          {groups.length === 0 && <p className="muted">No matches for this filter.</p>}
          {groups.map(g => (
            <Competition key={g.league_id} group={g} open={!closed.has(g.league_id)}
                         onToggle={() => toggle(g.league_id)} onSelect={onSelect} />
          ))}
        </section>
      )}
    </main>
  )
}

// ---------- match ----------

function Cards({ yellows = 0, reds = 0 }) {
  if (!yellows && !reds) return <span className="cards" />
  const label = [yellows && `${yellows} yellow`, reds && `${reds} red`].filter(Boolean).join(', ')
  return (
    <span className="cards" role="img" aria-label={`${label} card${yellows + reds > 1 ? 's' : ''}`}>
      {Array.from({ length: yellows }, (_, i) => <span key={`y${i}`} className="card yellow" />)}
      {Array.from({ length: reds }, (_, i) => <span key={`r${i}`} className="card red" />)}
    </span>
  )
}

function Scoreboard({ data, fixture, center }) {
  return (
    <section className="scoreboard" aria-label="Score">
      <div className="sb-team">
        <Logo src={fixture?.home_logo} size={64} />
        <span className="sb-name">{data.home}</span>
        <Cards yellows={data.h_yellows} reds={data.h_reds} />
      </div>
      <div className="sb-mid">{center}</div>
      <div className="sb-team">
        <Logo src={fixture?.away_logo} size={64} />
        <span className="sb-name">{data.away}</span>
        <Cards yellows={data.a_yellows} reds={data.a_reds} />
      </div>
    </section>
  )
}

function WinProbability({ data }) {
  const { probs, pre } = data
  const labels = { home: data.home, draw: 'Draw', away: data.away }
  return (
    <section className="block" aria-labelledby="wp-h">
      <div className="block-head spread">
        <h2 id="wp-h" className="h2">Win probability</h2>
        {pre && <span className="muted small">Change vs pre-match market</span>}
      </div>
      <div className="wp-bar" aria-hidden="true">
        {OUTCOMES.map(k => (
          <span key={k} className={`wp-seg ${k} num`} style={{ width: `${probs[k] * 100}%` }}>
            {probs[k] >= 0.1 ? `${pct(probs[k])}%` : ''}
          </span>
        ))}
      </div>
      <div className="wp-cols">
        {OUTCOMES.map(k => {
          const d = pre ? pct(probs[k]) - pct(pre[k]) : null
          return (
            <div key={k} className={`wp-col ${k}`}>
              <span className="label ellipsis">{labels[k]}</span>
              <span className="wp-pct num">{pct(probs[k])}%</span>
              {d != null && (
                <span className={`wp-delta num ${d < 0 ? 'down' : ''}`}>
                  {d > 0 ? '+' : d < 0 ? '−' : '±'}{Math.abs(d)} pts
                </span>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}

// most likely final scores: top one big, the rest as bars scaled to it, coloured by who wins
function FinalScore({ scores }) {
  const [top, ...rest] = scores
  const side = s => (s.home > s.away ? 'home' : s.home < s.away ? 'away' : 'draw')
  return (
    <section className="panel fs" aria-labelledby="fs-h">
      <h2 id="fs-h" className="h3">Likely final score</h2>
      <div className="fs-top">
        <span className="fs-score num">{top.home}–{top.away}</span>
        <span className="fs-pct num">{pct(top.p)}%</span>
      </div>
      <ul className="fs-list">
        {rest.map(s => (
          <li key={`${s.home}-${s.away}`} className="fs-row">
            <span className="num fs-line">{s.home}–{s.away}</span>
            <span className="fs-track"><span className={`fs-bar ${side(s)}`} style={{ width: `${(s.p / top.p) * 100}%` }} /></span>
            <span className="num fs-p">{pct(s.p)}%</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

// probability-over-time chart: 3 series, HT line, goal markers, crosshair + tooltip
function History({ history, home, away }) {
  const [hover, setHover] = useState(null)
  const [W, setW] = useState(760) // drawn at the container's real width so labels stay 11px on phones
  const svgRef = useRef(null)
  const ro = useRef(null)
  const measure = el => {
    ro.current?.disconnect()
    if (!el) return
    ro.current = new ResizeObserver(([e]) => setW(Math.max(300, Math.round(e.contentRect.width))))
    ro.current.observe(el)
  }
  if (history.length < 2) {
    return <p className="muted small">The chart builds as the match is polled (every {MATCH_POLL_MIN} minutes).</p>
  }
  const H = W < 500 ? 240 : 290, L = 44, R = W - 56, T = 32, B = H - 30
  const maxMin = Math.max(90, history[history.length - 1].minute)
  const x = m => L + (m / maxMin) * (R - L)
  const y = p => T + (1 - p) * (B - T)
  const path = k => history.map((h, i) => `${i ? 'L' : 'M'}${x(h.minute).toFixed(1)},${y(h[k]).toFixed(1)}`).join(' ')
  const last = history[history.length - 1]
  const goals = history.filter((h, i) => i > 0 && h.h_goals != null
    && (h.h_goals !== history[i - 1].h_goals || h.a_goals !== history[i - 1].a_goals))
  const series = [['home', home], ['draw', 'Draw'], ['away', away]]

  const onMove = e => {
    const box = svgRef.current.getBoundingClientRect()
    const mx = ((e.clientX - box.left) / box.width) * W
    let best = 0
    history.forEach((h, i) => { if (Math.abs(x(h.minute) - mx) < Math.abs(x(history[best].minute) - mx)) best = i })
    setHover(best)
  }
  const hv = hover != null ? history[hover] : null

  return (
    <div className="chart-wrap" ref={measure}>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="chart" role="img"
           aria-label={`${home} ${pct(history[0].home)}% to ${pct(last.home)}%, ${away} ${pct(history[0].away)}% to ${pct(last.away)}%`}
           onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {[0, 0.25, 0.5, 0.75, 1].map(p => (
          <g key={p}>
            <line x1={L} x2={R} y1={y(p)} y2={y(p)} className="grid" />
            <text x={L - 8} y={y(p) + 4} className="tick" textAnchor="end">{pct(p)}%</text>
          </g>
        ))}
        {[0, 15, 30, 45, 60, 75, 90].map(m => (
          <text key={m} x={x(m)} y={H - 8} className="tick" textAnchor="middle">{m}′</text>
        ))}
        {last.minute >= 45 && (
          <g>
            <line x1={x(45)} x2={x(45)} y1={T} y2={B} className="ht-line" />
            <text x={x(45) + 5} y={B - 6} className="tick">HT</text>
          </g>
        )}
        {goals.map(g => {
          const text = `GOAL · ${g.h_goals}–${g.a_goals}`
          const w = text.length * 7 + 12
          return (
            <g key={g.minute}>
              <line x1={x(g.minute)} x2={x(g.minute)} y1={T} y2={B} className="goal-line" />
              <rect x={x(g.minute) - w / 2} y={T - 26} width={w} height={20} className="goal-tag" />
              <text x={x(g.minute)} y={T - 12} className="goal-text" textAnchor="middle">{text}</text>
            </g>
          )
        })}
        {['draw', 'away', 'home'].map(k => <path key={k} d={path(k)} className={`line ${k}`} />)}
        {series.map(([k]) => (
          <g key={k}>
            <circle cx={x(last.minute)} cy={y(last[k])} r="4" className={`pt ${k}`} />
            <text x={x(last.minute) + 10} y={y(last[k]) + 4} className="end-label">{pct(last[k])}%</text>
          </g>
        ))}
        {hv && (
          <g>
            <line x1={x(hv.minute)} x2={x(hv.minute)} y1={T} y2={B} className="crosshair" />
            {series.map(([k]) => <circle key={k} cx={x(hv.minute)} cy={y(hv[k])} r="4" className={`pt ${k}`} />)}
          </g>
        )}
      </svg>
      {hv && (
        <div className="tooltip" style={{ left: `${(x(hv.minute) / W) * 100}%` }}>
          <div className="tooltip-title num">{hv.minute}′{hv.h_goals != null && ` · ${hv.h_goals}–${hv.a_goals}`}</div>
          {series.map(([k, label]) => (
            <div key={k} className="tooltip-row"><span className={`key ${k}`} />{label}<b className="num">{pct(hv[k])}%</b></div>
          ))}
        </div>
      )}
    </div>
  )
}

// [stats key, label, which is better, read by the model]
const STAT_ROWS = [
  ['total_shots', 'Shots', 'high', true],
  ['shots_on_goal', 'Shots on target', 'high', true],
  ['ball_possession', 'Possession', 'high', false],
  ['expected_goals', 'Expected goals', 'high', false],
  ['corner_kicks', 'Corners', 'high', true],
  ['fouls', 'Fouls', 'low', false],
]

function TeamStats({ data, fixture }) {
  const s = data.stats || {}
  const rows = STAT_ROWS
    .filter(([k]) => s[`h_${k}`] != null || s[`a_${k}`] != null)
    .map(([k, label, better, model]) => [label, s[`h_${k}`] ?? 0, s[`a_${k}`] ?? 0, better, model])
  rows.push(['Yellow cards', data.h_yellows ?? 0, data.a_yellows ?? 0, 'low', true],
            ['Red cards', data.h_reds ?? 0, data.a_reds ?? 0, 'low', true])

  return (
    <section className="panel stats" aria-labelledby="st-h">
      <div className="stats-head">
        <Logo src={fixture?.home_logo} size={28} />
        <h2 id="st-h" className="h3">Team stats</h2>
        <Logo src={fixture?.away_logo} size={28} />
      </div>
      {rows.map(([label, hv, av, better, model]) => {
        const h = parseFloat(hv) || 0, a = parseFloat(av) || 0
        const t = h + a || 1
        const homeBetter = h !== a && (better === 'high' ? h > a : h < a)
        const winner = h === a ? null : homeBetter ? 'home' : 'away'
        return (
          <div key={label} className="stat">
            <div className="stat-line num">
              <span className={`stat-val ${winner === 'home' ? 'best home' : ''}`}>{hv}</span>
              <span className="stat-label">{model && <span className="model-dot" title="Read by the model" />}{label}</span>
              <span className={`stat-val right ${winner === 'away' ? 'best away' : ''}`}>{av}</span>
            </div>
            <div className="stat-bars" aria-hidden="true">
              <span className="track left"><span className={winner === 'home' ? 'home' : ''} style={{ width: `${(h / t) * 100}%` }} /></span>
              <span className="track"><span className={winner === 'away' ? 'away' : ''} style={{ width: `${(a / t) * 100}%` }} /></span>
            </div>
          </div>
        )
      })}
      <span className="muted small legend"><span className="model-dot" />Read by the model</span>
    </section>
  )
}

function countdown(iso) {
  const mins = Math.round((new Date(iso) - Date.now()) / 60000)
  if (mins <= 0) return null
  return mins >= 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`
}

function Prematch({ data, fixture }) {
  const left = data.status === 'NS' ? countdown(data.kickoff) : null
  const labels = { home: data.home, draw: 'Draw', away: data.away }
  return (
    <>
      <Scoreboard data={data} fixture={fixture} center={
        <>
          <span className="sb-kickoff num">{data.status === 'NS' ? localTime(data.kickoff) : `${data.h_goals ?? 0}–${data.a_goals ?? 0}`}</span>
          <span className="chip">{left ? `Kick-off in ${left}` : STATUS_WORD[data.status] ?? data.status}</span>
        </>
      } />
      {data.probs ? (
        <section className="block" aria-labelledby="mk-h">
          <div className="block-head spread">
            <h2 id="mk-h" className="h2">What the market expects</h2>
            <span className="muted small">Bookmaker margin removed</span>
          </div>
          <div className="market-grid">
            {OUTCOMES.map(k => (
              <div key={k} className={`panel market ${k}`}>
                <span className="label ellipsis">{labels[k]}</span>
                <span className="market-line">
                  <span className="wp-pct num">{pct(data.probs[k])}%</span>
                  <span className="num">odds {data.odds[k].toFixed(2)}</span>
                </span>
              </div>
            ))}
          </div>
          <div className="wp-bar thin" aria-hidden="true">
            {OUTCOMES.map(k => <span key={k} className={`wp-seg ${k}`} style={{ width: `${data.probs[k] * 100}%` }} />)}
          </div>
        </section>
      ) : (
        <section className="panel note-panel">
          <h2 className="h2">No market odds for this league</h2>
          <p className="muted">The model will still predict once the match starts, using the score,
            cards, shots and corners. Expect it to be less sure early on than for matches with odds.</p>
        </section>
      )}
      {data.scores?.length > 0 && <FinalScore scores={data.scores} />}
      {!FINISHED.has(data.status) && (
        <section className="panel kickoff-strip">
          <span className="h3">Live predictions start at kick-off</span>
          <span className="muted small">This page updates every {MATCH_POLL_MIN} minutes during the match.</span>
        </section>
      )}
    </>
  )
}

function Live({ data, fixture }) {
  const live = LIVE.has(data.status)
  return (
    <>
      <Scoreboard data={data} fixture={fixture} center={
        <>
          <span className="sb-score num">{data.h_goals}–{data.a_goals}</span>
          {live
            ? <span className="live-pill big num"><LiveDot />{data.status === 'HT' ? 'Half-time' : `${data.minute}′ · ${STATUS_WORD[data.status] ?? 'Live'}`}</span>
            : <span className="chip">{STATUS_WORD[data.status] ?? data.status}</span>}
        </>
      } />
      {data.probs && <WinProbability data={data} />}
      <div className="split">
        <div className="main-col">
        {data.probs && (
          <section className="panel chart-panel" aria-labelledby="ch-h">
            <div className="block-head spread">
              <h2 id="ch-h" className="h3">Probability over time</h2>
              <div className="legend-row small">
                <span><span className="key home" />{data.home}</span>
                <span><span className="key draw" />Draw</span>
                <span><span className="key away" />{data.away}</span>
              </div>
            </div>
            <History history={data.history} home={data.home} away={data.away} />
          </section>
        )}
        {data.scores?.length > 0 && <FinalScore scores={data.scores} />}
        </div>
        <TeamStats data={data} fixture={fixture} />
      </div>
      <p className="muted small footnote">Model v2 starts from the pre-match odds (when the league has them),
        then updates on the score, cards, shots, shots on target and corners. Probabilities are for the
        result after 90 minutes; likely final scores include stoppage time.</p>
    </>
  )
}

function Match({ fixtureId, fixture, onBack }) {
  const [data, err] = usePoll(`/live/${fixtureId}`, MATCH_POLL_MIN)
  return (
    <main className="page">
      <nav className="crumbs">
        <button type="button" className="back" onClick={onBack}><Chevron dir="left" />Today's matches</button>
        {fixture && <span className="crumb-league">{fixture.league} · {fixture.country}</span>}
      </nav>
      {err && <p className="error">{err}</p>}
      {!data && !err && <p className="muted">Loading…</p>}
      {data && (data.prematch ? <Prematch data={data} fixture={fixture} /> : <Live data={data} fixture={fixture} />)}
    </main>
  )
}

// ---------- shell ----------

function TopBar({ every }) {
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="brand">
          <span className="wordmark">Live Football Predictions</span>
          <span className="tag">Model v2</span>
        </div>
        <div className="topbar-meta">
          <span>{new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}</span>
          <span>Updates every {every} min</span>
        </div>
      </div>
    </header>
  )
}

export default function App() {
  const [selected, setSelected] = useState(null)
  const [fixtures, err] = usePoll('/fixtures', LIST_POLL_MIN) // also gives the match view its logos/league
  const open = id => { setSelected(id); window.scrollTo(0, 0) }

  return (
    <div className="app">
      <TopBar every={selected == null ? LIST_POLL_MIN : MATCH_POLL_MIN} />
      {selected == null
        ? <Home fixtures={fixtures} err={err} onSelect={open} />
        : <Match fixtureId={selected} fixture={fixtures?.find(f => f.fixture_id === selected)}
                 onBack={() => setSelected(null)} />}
    </div>
  )
}
