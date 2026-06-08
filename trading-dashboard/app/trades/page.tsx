'use client'
import { useState, useEffect, useCallback } from 'react'
import { TradeCard }       from '@/components/trades/TradeCard'
import { ConfirmModal }    from '@/components/trades/ConfirmModal'
import { RegimeIndicator } from '@/components/dashboard/RegimeIndicator'
import { demoRecommendations, demoRegime, api } from '@/lib/api'
import type { TradeRecommendation, RegimeInfo } from '@/types/trading'
import { RefreshCw, Filter, Wifi, WifiOff } from 'lucide-react'
import { cn } from '@/lib/utils'

const REFRESH_MS = 30_000   // auto-refresh every 30s

export default function TradesPage() {
  const [selected,  setSelected]  = useState<TradeRecommendation | null>(null)
  const [filter,    setFilter]    = useState<'all' | 'LONG' | 'SHORT'>('all')
  const [trades,    setTrades]    = useState<TradeRecommendation[]>(demoRecommendations())
  const [regime,    setRegime]    = useState<RegimeInfo>(demoRegime())
  const [loading,   setLoading]   = useState(false)
  const [live,      setLive]      = useState(false)
  const [lastFetch, setLastFetch] = useState<Date | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const [recs, reg] = await Promise.allSettled([api.recommendations(), api.regime()])
      if (recs.status === 'fulfilled' && recs.value.length > 0) {
        setTrades(recs.value)
        setLive(true)
      } else {
        setTrades(demoRecommendations())
        setLive(false)
      }
      if (reg.status === 'fulfilled') setRegime(reg.value)
    } catch {
      setLive(false)
    } finally {
      setLoading(false)
      setLastFetch(new Date())
    }
  }, [])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchData])

  const displayed = filter === 'all' ? trades : trades.filter(t => t.direction === filter)

  return (
    <div className="px-6 py-6 space-y-6 max-w-[1400px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-primary">Trade Recommendations</h1>
          <p className="text-xs text-muted mt-0.5">
            {displayed.length} signals · AI multi-agent analysis
            {lastFetch && ` · Updated ${lastFetch.toLocaleTimeString()}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Live indicator */}
          {live
            ? <span className="flex items-center gap-1.5 text-xs text-bull"><Wifi className="h-3 w-3" /> Live</span>
            : <span className="flex items-center gap-1.5 text-xs text-caution"><WifiOff className="h-3 w-3" /> Demo</span>
          }

          {/* Direction filter */}
          <div className="flex items-center gap-1 rounded-lg border border-bg-border p-0.5">
            {(['all', 'LONG', 'SHORT'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={cn(
                  'rounded-md px-3 py-1 text-xs font-medium transition-all',
                  filter === f
                    ? f === 'LONG'  ? 'bg-bull/15 text-bull'
                    : f === 'SHORT' ? 'bg-bear/15 text-bear'
                    : 'bg-brand-cyan/10 text-brand-cyan'
                    : 'text-muted hover:text-subtle',
                )}
              >
                {f === 'all' ? 'All' : f}
              </button>
            ))}
          </div>

          <button onClick={fetchData} className="btn-ghost text-xs" disabled={loading}>
            <RefreshCw className={cn('h-3.5 w-3.5', loading && 'animate-spin')} />
            Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_220px]">
        {/* Trade cards */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 content-start">
          {displayed.length === 0 ? (
            <div className="col-span-full card flex flex-col items-center justify-center py-16 text-center">
              <Filter className="h-8 w-8 text-muted mb-3" />
              <p className="text-sm text-muted">No signals match the current filter.</p>
            </div>
          ) : (
            displayed.map(t => (
              <TradeCard key={t.id} trade={t} onExecute={setSelected} />
            ))
          )}
        </div>

        {/* Regime sidebar */}
        <RegimeIndicator regime={regime} />
      </div>

      {/* Confirmation modal */}
      <ConfirmModal
        trade={selected}
        onClose={() => setSelected(null)}
        onDone={() => { setSelected(null); fetchData() }}
      />
    </div>
  )
}
