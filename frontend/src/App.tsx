import { useState, useEffect, useRef, useCallback, MouseEvent as RMouseEvent } from 'react'

const API_BASE = (((import.meta as unknown as { env?: { VITE_API_URL?: string } }).env?.VITE_API_URL) || '').replace(/\/+$/, '')

// ── Types ─────────────────────────────────────────────────────────────────────

interface TelemetryEvent {
  stage: string
  model: string | null
  tokens_in: number
  tokens_out: number
  latency_ms: number
  cost_usd: number
  vendor: string | null
  payload?: Record<string, unknown> | null
}

interface OrgProfile {
  org_name: string
  org_type: string
  mission: string
  focus_areas: string[]
  location: string
  target_population: string
  keywords: string[]
}

interface GrantScore {
  grant_id: string
  grant_title: string
  agency: string
  funding_amount: string
  deadline: string
  match_score: number
  match_verdict: 'STRONG_MATCH' | 'PARTIAL_MATCH' | 'LOW_MATCH'
  matching_criteria: string[]
  missing_criteria: string[]
  rationale: string
  advice: string | null
  opportunity_url: string | null
}

interface FindResult {
  run_id: string
  org_url: string
  profile: OrgProfile | null
  grants: GrantScore[]
  total_grants_found: number
  strong_matches: number
  total_funding_available: string
  best_match_title: string | null
  best_match_score: number | null
  status: string
  telemetry_summary?: Record<string, unknown>
}

interface AttemptUsage {
  attempts: number
  cost: number
  tokens: number
}

interface RunStats {
  totalCost: number
  totalTokens: number
  calls: number
  elapsedMs: number
  modelUsage: Record<string, AttemptUsage>
  toolUsage: Record<string, AttemptUsage>
}

// ── Constants ─────────────────────────────────────────────────────────────────

const VERDICT_META = {
  STRONG_MATCH: {
    label: 'Strong Match',
    color: 'var(--verdict-good)',
    bg: 'var(--verdict-good-soft)',
    description: 'Organization clearly meets eligibility requirements and mission aligns well with the grant focus.',
  },
  PARTIAL_MATCH: {
    label: 'Partial Match',
    color: 'var(--verdict-warn)',
    bg: 'var(--verdict-warn-soft)',
    description: 'Organization meets some criteria but gaps exist — worth reviewing and potentially applying with a strong narrative.',
  },
  LOW_MATCH: {
    label: 'Low Match',
    color: 'var(--verdict-bad)',
    bg: 'var(--verdict-bad-soft)',
    description: 'Significant eligibility gaps make this grant unlikely to be a fit for this organization.',
  },
}

const EXAMPLE_URL = 'https://www.khanacademy.org'

// ── Helpers ───────────────────────────────────────────────────────────────────

function scoreColor(score: number) {
  if (score >= 0.75) return 'var(--verdict-good)'
  if (score >= 0.40) return 'var(--verdict-warn)'
  return 'var(--verdict-bad)'
}

function fmtCost(n: number) { return `$${n.toFixed(4)}` }
function fmtMs(ms: number) { return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s` }

function modelLabel(model: string) {
  if (model.includes('claude')) return 'Claude Sonnet 4.6'
  return model
}

function toolLabelForStage(stage: string) {
  if (stage === 'search') return 'Grants.gov search'
  if (stage === 'ingest') return 'Page scrape'
  return null
}

function RotatingText({ phrases, intervalMs = 2800 }: { phrases: string[]; intervalMs?: number }) {
  const [i, setI] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setI(idx => (idx + 1) % phrases.length), intervalMs)
    return () => clearInterval(t)
  }, [phrases.length, intervalMs])
  return <span key={i} className="rotate-fade">{phrases[i]}</span>
}

function Tip({ text }: { text: string }) {
  return (
    <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4, lineHeight: 1.5 }}>
      {text}
    </div>
  )
}

function StatBox({ label, value, tip, mono }: { label: string; value: string; tip: string; mono?: boolean }) {
  return (
    <div style={{ textAlign: 'left', padding: '16px 22px' }} title={tip}>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 6, textTransform: 'uppercase', fontWeight: 600, letterSpacing: '0.12em' }}>
        {label}
      </div>
      <div style={{ fontSize: 22, fontWeight: 600, fontFamily: mono ? 'var(--font-mono)' : 'var(--font-serif)', color: 'var(--text)', letterSpacing: '-0.02em', lineHeight: 1 }}>
        {value}
      </div>
    </div>
  )
}

function CostStatBox({ stats }: { stats: RunStats }) {
  const rows = [
    ...Object.entries(stats.modelUsage).map(([name, usage]) => ({ key: `model:${name}`, label: modelLabel(name), usage, kind: 'model' })),
    ...Object.entries(stats.toolUsage).map(([name, usage]) => ({ key: `tool:${name}`, label: name, usage, kind: 'tool' })),
  ].sort((a, b) => b.usage.cost - a.usage.cost || b.usage.attempts - a.usage.attempts)

  return (
    <div style={{ textAlign: 'center', padding: '14px 8px' }}>
      <div style={{ fontSize: 19, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>
        {fmtCost(stats.totalCost)}
      </div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2, fontWeight: 600 }}>LLM spend</div>
      <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'center' }}>
        {rows.length > 0 ? rows.slice(0, 4).map(({ key, label, usage, kind }) => (
          <div key={key} style={{
            maxWidth: '100%', fontSize: 10, color: 'var(--text-2)',
            background: 'rgba(255,255,255,0.55)', border: '1px solid var(--border)',
            borderRadius: 9999, padding: '2px 8px', whiteSpace: 'nowrap',
            overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            {label} {kind === 'tool' ? 'tool' : 'model'} · {usage.attempts} attempt{usage.attempts === 1 ? '' : 's'}
            {' · '}{kind === 'tool' ? 'not tracked' : fmtCost(usage.cost)}
          </div>
        )) : (
          <div style={{ fontSize: 10, color: '#9ca3af' }}>Waiting for attempts</div>
        )}
      </div>
    </div>
  )
}

// ── GrantCard ─────────────────────────────────────────────────────────────────

function GrantCard({ g, animIn }: { g: GrantScore; animIn: boolean }) {
  const [detailOpen, setDetailOpen] = useState(false)
  const [adviceOpen, setAdviceOpen] = useState(false)
  const pct = Math.round(g.match_score * 100)
  const meta = VERDICT_META[g.match_verdict]

  return (
    <div className="glass" style={{
      borderRadius: 18, padding: '20px 22px',
      transition: 'opacity 0.4s ease, transform 0.4s ease',
      opacity: animIn ? 1 : 0,
      transform: animIn ? 'translateY(0)' : 'translateY(20px)',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10, gap: 12 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 16, color: 'var(--text)', lineHeight: 1.3, marginBottom: 3 }}>
            {g.grant_title}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--font-sans)' }}>
            {g.agency}
          </div>
        </div>
        <div style={{ color: scoreColor(g.match_score), fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 26, lineHeight: 1, letterSpacing: '-0.02em', flexShrink: 0 }}>
          {pct}%
        </div>
      </div>

      {/* Score bar */}
      <div style={{ height: 2, background: 'rgba(15,23,42,0.06)', borderRadius: 2, marginBottom: 12, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: scoreColor(g.match_score), transition: 'width 0.8s ease', opacity: 0.7 }} />
      </div>

      {/* Verdict + funding row */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
        <span title={meta.description} style={{
          background: meta.bg, color: meta.color, borderRadius: 9999,
          padding: '3px 11px', fontSize: 11, fontWeight: 600, cursor: 'help',
          letterSpacing: '-0.005em',
        }}>
          {meta.label}
        </span>
        <span style={{ fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
          {g.funding_amount}
        </span>
        {g.deadline && g.deadline !== 'See listing' && (
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>
            Due {g.deadline}
          </span>
        )}
        {g.opportunity_url && (
          <a href={g.opportunity_url} target="_blank" rel="noreferrer" style={{
            marginLeft: 'auto', fontSize: 10, color: 'var(--accent)',
            textDecoration: 'none', fontWeight: 500,
          }}>
            View on Grants.gov ↗
          </a>
        )}
      </div>

      {/* Rationale */}
      <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.55, marginBottom: 10, fontFamily: 'var(--font-serif)' }}>
        {g.rationale}
      </div>

      {/* Match detail toggle */}
      {(g.matching_criteria.length > 0 || g.missing_criteria.length > 0) && (
        <>
          <button onClick={() => setDetailOpen(x => !x)} className="pill" style={{ height: 30, fontSize: 12, padding: '0 14px', marginRight: 6 }}>
            {detailOpen ? 'Hide criteria' : 'Match criteria'}
          </button>
          {detailOpen && (
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {g.matching_criteria.length > 0 && (
                <div style={{ padding: '10px 12px', background: 'var(--verdict-good-soft)', borderRadius: 10 }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--verdict-good)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>
                    Why it fits
                  </div>
                  {g.matching_criteria.map((c, i) => (
                    <div key={i} style={{ fontSize: 12, color: 'var(--text)', lineHeight: 1.5, marginBottom: 3 }}>
                      <span style={{ color: 'var(--verdict-good)', fontWeight: 600 }}>✓</span> {c}
                    </div>
                  ))}
                </div>
              )}
              {g.missing_criteria.length > 0 && (
                <div style={{ padding: '10px 12px', background: 'var(--verdict-warn-soft)', borderRadius: 10 }}>
                  <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--verdict-warn)', textTransform: 'uppercase', letterSpacing: '0.1em', marginBottom: 6 }}>
                    Gaps to address
                  </div>
                  {g.missing_criteria.map((c, i) => (
                    <div key={i} style={{ fontSize: 12, color: 'var(--text)', lineHeight: 1.5, marginBottom: 3 }}>
                      <span style={{ color: 'var(--verdict-warn)', fontWeight: 600 }}>△</span> {c}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Next step toggle */}
      {g.advice && (
        <button onClick={() => setAdviceOpen(x => !x)} className="pill" style={{ height: 30, fontSize: 12, padding: '0 14px', marginTop: 8 }}>
          {adviceOpen ? 'Hide next step' : 'Next step →'}
        </button>
      )}
      {adviceOpen && g.advice && (
        <div style={{
          marginTop: 10, padding: '14px 16px',
          background: 'var(--surface-2)', borderRadius: 12,
          border: '1px solid var(--border)',
          fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.65,
          fontFamily: 'var(--font-serif)',
        }}>
          {g.advice}
        </div>
      )}
    </div>
  )
}

// ── OrgProfileCard ────────────────────────────────────────────────────────────

function OrgProfileCard({ p }: { p: OrgProfile }) {
  return (
    <div style={{
      padding: '14px 16px',
      background: 'rgba(99,102,241,0.06)',
      borderRadius: 12,
      border: '1px solid rgba(99,102,241,0.15)',
    }}>
      <div style={{ fontSize: 9, fontWeight: 700, color: 'var(--accent)', textTransform: 'uppercase', letterSpacing: '0.12em', marginBottom: 8 }}>
        Org Profile Extracted
      </div>
      <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 15, color: 'var(--text)', marginBottom: 4 }}>
        {p.org_name || 'Unknown org'}
      </div>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6 }}>
        {p.org_type} · {p.location}
      </div>
      {p.mission && (
        <div style={{ fontSize: 12, color: 'var(--text-2)', lineHeight: 1.5, marginBottom: 8, fontStyle: 'italic' }}>
          {p.mission}
        </div>
      )}
      {p.focus_areas.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {p.focus_areas.slice(0, 6).map((area, i) => (
            <span key={i} style={{
              fontSize: 10, color: 'var(--accent)', background: 'var(--accent-soft)',
              padding: '2px 8px', borderRadius: 9999, fontWeight: 500,
            }}>
              {area}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────

type Phase = 'idle' | 'running' | 'done'

export default function App() {
  const [orgUrl, setOrgUrl] = useState('')
  const [urlError, setUrlError] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [stats, setStats] = useState<RunStats>({
    totalCost: 0, totalTokens: 0, calls: 0, elapsedMs: 0,
    modelUsage: {}, toolUsage: {},
  })
  const [findResult, setFindResult] = useState<FindResult | null>(null)
  const [animedIn, setAnimedIn] = useState<Set<string>>(new Set())
  const [sidebarWidth, setSidebarWidth] = useState(300)

  const startTimeRef = useRef<number>(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const evtRef = useRef<EventSource | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const draggingRef = useRef(false)

  const stopTimers = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current)
    if (pollRef.current) clearInterval(pollRef.current)
    if (evtRef.current) evtRef.current.close()
  }, [])

  const fetchResults = useCallback(async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/find/${id}/results`)
      if (!res.ok) return
      const data: FindResult = await res.json()
      setFindResult(data)
      if (data.grants) {
        setAnimedIn(prev => {
          const n = new Set(prev)
          data.grants.forEach(g => n.add(g.grant_id))
          return n
        })
      }
    } catch { /* ignore */ }
  }, [])

  const startFind = useCallback(async () => {
    const url = orgUrl.trim()
    if (!url || !url.startsWith('http')) {
      setUrlError('Enter a valid URL starting with http:// or https://')
      return
    }
    setUrlError('')
    stopTimers()
    setPhase('running')
    setStats({ totalCost: 0, totalTokens: 0, calls: 0, elapsedMs: 0, modelUsage: {}, toolUsage: {} })
    setFindResult(null)
    setAnimedIn(new Set())
    startTimeRef.current = Date.now()

    timerRef.current = setInterval(() => {
      setStats(s => ({ ...s, elapsedMs: Date.now() - startTimeRef.current }))
    }, 250)

    try {
      const res = await fetch(`${API_BASE}/find`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ org_url: url }),
      })
      const accepted = await res.json()
      pollRef.current = setInterval(() => fetchResults(accepted.run_id), 2500)
      const es = new EventSource(`${API_BASE}${accepted.stream_url}`)
      evtRef.current = es
      es.addEventListener('telemetry', (e) => {
        const ev: TelemetryEvent = JSON.parse(e.data)
        const eventTokens = ev.tokens_in + ev.tokens_out
        const toolLabel = toolLabelForStage(ev.stage)
        setStats(s => ({
          ...s,
          totalCost: s.totalCost + ev.cost_usd,
          totalTokens: s.totalTokens + eventTokens,
          calls: s.calls + 1,
          modelUsage: ev.model
            ? {
                ...s.modelUsage,
                [ev.model]: {
                  attempts: (s.modelUsage[ev.model]?.attempts ?? 0) + 1,
                  cost: (s.modelUsage[ev.model]?.cost ?? 0) + ev.cost_usd,
                  tokens: (s.modelUsage[ev.model]?.tokens ?? 0) + eventTokens,
                },
              }
            : s.modelUsage,
          toolUsage: toolLabel
            ? {
                ...s.toolUsage,
                [toolLabel]: {
                  attempts: (s.toolUsage[toolLabel]?.attempts ?? 0) + 1,
                  cost: (s.toolUsage[toolLabel]?.cost ?? 0) + ev.cost_usd,
                  tokens: (s.toolUsage[toolLabel]?.tokens ?? 0) + eventTokens,
                },
              }
            : s.toolUsage,
        }))
        if (ev.stage === 'find_done') { setPhase('done'); stopTimers(); fetchResults(accepted.run_id) }
      })
      es.onerror = () => { setPhase('done'); stopTimers(); fetchResults(accepted.run_id) }
    } catch (err) {
      console.error(err); setPhase('idle'); stopTimers()
    }
  }, [orgUrl, stopTimers, fetchResults])

  const startDrag = useCallback((e: RMouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    const onMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return
      setSidebarWidth(w => Math.max(240, Math.min(480, w + ev.movementX)))
    }
    const onUp = () => { draggingRef.current = false; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [])

  useEffect(() => () => stopTimers(), [stopTimers])

  const grants = findResult?.grants ?? []
  const profile = findResult?.profile ?? null
  const strongCount = grants.filter(g => g.match_verdict === 'STRONG_MATCH').length
  const partialCount = grants.filter(g => g.match_verdict === 'PARTIAL_MATCH').length

  return (
    <>
      {phase !== 'idle' && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 20,
          padding: '24px 36px',
          display: 'flex', alignItems: 'center', gap: 12,
          pointerEvents: 'none',
        }}>
          <div className="reveal-fade d-0" style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text)' }}>
            <span className="headline-fade" style={{ fontFamily: 'var(--font-sans)', fontSize: 20, fontWeight: 700, letterSpacing: '-0.035em', lineHeight: 1 }}>
              Grant Finder
            </span>
          </div>
          <span className="reveal-fade d-1" style={{ color: 'var(--muted)', fontSize: 12, fontFamily: 'var(--font-sans)' }}>
            <RotatingText phrases={['Finding your best grant matches.', 'Scanning federal databases.', 'Scoring eligibility live.']} />
          </span>
        </div>
      )}

      {phase === 'idle' ? (
        /* ── LANDING ── */
        <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '64px 24px 80px' }}>
          <div className="aurora-bg" />
          <div style={{ maxWidth: 600, width: '100%', textAlign: 'center' }}>
            <h1 className="reveal-up d-1 headline-fade" style={{
              fontFamily: 'var(--font-sans)', fontSize: 'clamp(56px, 8vw, 108px)',
              fontWeight: 700, letterSpacing: '-0.055em', lineHeight: 1,
              margin: 0, marginBottom: 16,
            }}>
              Grant Finder
            </h1>

            <div className="reveal-up d-2" style={{
              color: 'var(--text-2)', fontSize: 'clamp(17px, 2vw, 22px)',
              fontFamily: 'var(--font-sans)', fontWeight: 400,
              lineHeight: 1.4, letterSpacing: '-0.01em', marginBottom: 44,
            }}>
              <div>Federal grants matched to your org.</div>
              <div>
                <RotatingText phrases={['In under a minute.', 'Ranked by eligibility.', 'With next steps.']} />
              </div>
            </div>

            <div className="reveal-up d-3" style={{ marginBottom: 16 }}>
              <input
                type="url"
                value={orgUrl}
                onChange={e => { setOrgUrl(e.target.value); setUrlError('') }}
                onKeyDown={e => { if (e.key === 'Enter') startFind() }}
                placeholder="https://your-nonprofit.org"
                style={{
                  width: '100%', boxSizing: 'border-box',
                  background: 'rgba(255,255,255,0.65)',
                  border: `1px solid ${urlError ? 'var(--verdict-bad)' : 'var(--border)'}`,
                  borderRadius: 18, color: 'var(--text)', fontSize: 15,
                  fontFamily: 'var(--font-mono)', padding: '18px 22px',
                  outline: 'none', lineHeight: 1.5,
                  boxShadow: '0 10px 40px rgba(15,23,42,0.06), inset 0 1px 0 rgba(255,255,255,0.9)',
                  backdropFilter: 'blur(20px) saturate(180%)',
                  WebkitBackdropFilter: 'blur(20px) saturate(180%)',
                  transition: 'border-color 0.2s',
                }}
              />
              {urlError && <div style={{ color: 'var(--verdict-bad)', fontSize: 12, marginTop: 8, textAlign: 'left' }}>{urlError}</div>}
            </div>

            <div className="reveal-up d-4" style={{ marginBottom: 14 }}>
              <button
                onClick={startFind}
                disabled={!orgUrl.trim()}
                className="pill pill-primary"
                style={{ height: 52, fontSize: 14.5, padding: '0 36px', minWidth: 200 }}
              >
                Find Grants →
              </button>
            </div>

            <div className="reveal-up d-5" style={{ fontSize: 12, color: 'var(--muted)', display: 'flex', justifyContent: 'center', gap: 14, flexWrap: 'wrap' }}>
              <span>Paste your org URL above or</span>
              <button
                onClick={() => { setOrgUrl(EXAMPLE_URL); setUrlError('') }}
                style={{ background: 'none', border: 'none', color: 'var(--accent)', fontSize: 12, cursor: 'pointer', fontFamily: 'var(--font-sans)', textDecoration: 'underline', textUnderlineOffset: 3, padding: 0 }}>
                try Khan Academy
              </button>
            </div>
          </div>
        </div>
      ) : (
        /* ── ACTIVE LAYOUT ── */
        <div className="reveal-fade" style={{ position: 'fixed', inset: 0, display: 'flex', flexDirection: 'column', paddingTop: 72, overflow: 'hidden' }}>
          <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>

            {/* Left sidebar */}
            <aside className="reveal-left d-1" style={{
              width: sidebarWidth, flexShrink: 0,
              background: 'rgba(255,255,255,0.35)',
              backdropFilter: 'blur(20px) saturate(180%)',
              WebkitBackdropFilter: 'blur(20px) saturate(180%)',
              padding: '28px 20px',
              display: 'flex', flexDirection: 'column', gap: 20, overflowY: 'auto',
              borderRight: '1px solid var(--border)',
            }}>

              {/* URL input */}
              <div>
                <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 17, color: 'var(--text)', marginBottom: 4 }}>
                  Organization URL
                </div>
                <input
                  type="url"
                  value={orgUrl}
                  onChange={e => { setOrgUrl(e.target.value); setUrlError('') }}
                  disabled={phase === 'running'}
                  placeholder="https://your-nonprofit.org"
                  style={{
                    marginTop: 8, width: '100%', boxSizing: 'border-box',
                    background: 'rgba(255,255,255,0.6)',
                    border: `1px solid ${urlError ? 'var(--verdict-bad)' : 'var(--border)'}`,
                    borderRadius: 10, color: 'var(--text)', fontSize: 13,
                    fontFamily: 'var(--font-mono)', padding: '10px 12px', outline: 'none',
                    transition: 'border-color 0.15s',
                  }}
                />
                {urlError && <div style={{ color: 'var(--verdict-bad)', fontSize: 11, marginTop: 4 }}>{urlError}</div>}
              </div>

              {/* Run button */}
              <div>
                <button onClick={startFind} disabled={phase === 'running'}
                  className="pill pill-primary"
                  style={{ width: '100%', height: 44, fontSize: 13.5, fontWeight: 500 }}>
                  {phase === 'running' ? 'Searching…' : 'Find Grants Again'}
                </button>
                <Tip text="Reads the page, profiles the org, scans federal databases, scores each grant." />
              </div>

              {/* Org profile — appears after profile stage completes */}
              {profile && <OrgProfileCard p={profile} />}

              {/* Legend */}
              <div>
                <div style={{ fontFamily: 'var(--font-sans)', fontSize: 10, fontWeight: 600, color: 'var(--muted)', letterSpacing: '0.12em', marginBottom: 10, textTransform: 'uppercase' }}>
                  Match Verdicts
                </div>
                {Object.entries(VERDICT_META).map(([, m]) => (
                  <div key={m.label} style={{ marginBottom: 10 }}>
                    <span style={{
                      display: 'inline-block', background: m.bg, color: m.color,
                      fontWeight: 600, fontSize: 11, padding: '2px 10px', borderRadius: 9999,
                    }}>
                      {m.label}
                    </span>
                    <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4, lineHeight: 1.5 }}>{m.description}</div>
                  </div>
                ))}
              </div>
            </aside>

            {/* Drag handle */}
            <div
              onMouseDown={startDrag}
              style={{ width: 1, flexShrink: 0, background: 'var(--border)', cursor: 'col-resize', transition: 'background 0.15s, width 0.15s' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--text-2)'; e.currentTarget.style.width = '2px' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'var(--border)'; e.currentTarget.style.width = '1px' }}
            />

            {/* Right: stats + results */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

              {/* Stats bar */}
              <div className="reveal-down d-2" style={{
                display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)',
                background: 'rgba(255,255,255,0.45)',
                backdropFilter: 'blur(20px) saturate(180%)',
                WebkitBackdropFilter: 'blur(20px) saturate(180%)',
                borderBottom: '1px solid var(--border)',
                flexShrink: 0,
              }}>
                <div className="reveal-up d-3"><CostStatBox stats={stats} /></div>
                {[
                  { label: 'Elapsed', value: fmtMs(stats.elapsedMs), tip: 'Wall-clock time since search started', mono: false },
                  { label: 'API calls', value: String(stats.calls), tip: 'Scrape, search, and model calls', mono: false },
                  { label: 'Grants found', value: grants.length > 0 ? String(grants.length) : '—', tip: 'Total opportunities scored so far', mono: false },
                  { label: 'Strong matches', value: grants.length > 0 ? String(strongCount) : '—', tip: 'Grants with match score ≥ 75%', mono: false },
                ].map(({ label, value, tip, mono }, i) => (
                  <div key={label} className={`reveal-up d-${3 + i}`} style={{ borderLeft: '1px solid var(--border)' }}>
                    <StatBox label={label} value={value} tip={tip} mono={mono} />
                  </div>
                ))}
              </div>

              {/* Results */}
              <main className="reveal-fade d-3" style={{ flex: 1, overflowY: 'auto', padding: '32px 36px' }}>

                {/* Spinner */}
                {phase === 'running' && grants.length === 0 && (
                  <div style={{ textAlign: 'center', marginTop: 100, color: 'var(--muted)' }}>
                    <div style={{
                      width: 32, height: 32, border: '2px solid var(--border)',
                      borderTop: '2px solid var(--text)', borderRadius: '50%',
                      margin: '0 auto 18px', animation: 'spin 0.9s linear infinite',
                    }} />
                    <div style={{ fontSize: 14, color: 'var(--text)', fontFamily: 'var(--font-serif)', fontWeight: 500 }}>
                      {profile ? 'Scoring grant opportunities…' : 'Reading organization website…'}
                    </div>
                    <div style={{ fontSize: 12, marginTop: 6 }}>
                      {profile ? `Found ${profile.keywords.length} keywords · scanning federal databases` : 'Extracting org profile'}
                    </div>
                  </div>
                )}

                {/* Summary banner */}
                {phase === 'done' && findResult && (
                  <div className="glass" style={{
                    marginBottom: 28, padding: '20px 24px', borderRadius: 18,
                    display: 'flex', alignItems: 'baseline', gap: 36, flexWrap: 'wrap',
                  }}>
                    {profile && (
                      <div>
                        <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4, textTransform: 'uppercase', fontWeight: 600, letterSpacing: '0.12em' }}>Org</div>
                        <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 20, letterSpacing: '-0.015em' }}>{profile.org_name}</div>
                        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{profile.org_type} · {profile.location}</div>
                      </div>
                    )}
                    <div>
                      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4, textTransform: 'uppercase', fontWeight: 600, letterSpacing: '0.12em' }}>Grants Found</div>
                      <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 20, letterSpacing: '-0.015em' }}>{findResult.total_grants_found}</div>
                    </div>
                    <div title="Grants with match score ≥ 75%">
                      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4, textTransform: 'uppercase', fontWeight: 600, letterSpacing: '0.12em' }}>Strong Matches</div>
                      <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 20, color: 'var(--verdict-good)', letterSpacing: '-0.015em' }}>{findResult.strong_matches}</div>
                    </div>
                    {findResult.total_funding_available && findResult.total_funding_available !== 'varies' && (
                      <div title="Sum of funding from strong and partial matches">
                        <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4, textTransform: 'uppercase', fontWeight: 600, letterSpacing: '0.12em' }}>Funding Available</div>
                        <div style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, fontSize: 20, letterSpacing: '-0.015em' }}>{findResult.total_funding_available}</div>
                      </div>
                    )}
                    {(strongCount + partialCount) > 0 && (
                      <div style={{ marginLeft: 'auto', fontSize: 12, color: 'var(--verdict-good)', fontWeight: 500 }}>
                        {strongCount} strong · {partialCount} partial
                      </div>
                    )}
                  </div>
                )}

                {/* Grant cards */}
                {grants.length > 0 && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 18 }}>
                    {grants.map(g => (
                      <GrantCard key={g.grant_id} g={g} animIn={animedIn.has(g.grant_id)} />
                    ))}
                  </div>
                )}
              </main>
            </div>
          </div>
        </div>
      )}

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </>
  )
}
