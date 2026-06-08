'use client'
import { ArrowUpRight, ArrowDownLeft, Flame, Zap } from 'lucide-react'
import { cn, formatPrice, colorForPnl, bgColorForScore } from '@/lib/utils'
import type { TradeRecommendation } from '@/types/trading'

interface Props {
  trade:     TradeRecommendation
  onExecute: (trade: TradeRecommendation) => void
}

const AGENT_COLORS: Record<string, string> = {
  technical:   'bg-brand-cyan',
  fundamental: 'bg-purple-400',
  vision:      'bg-indigo-400',
  risk:        'bg-caution',
  social:      'bg-pink-400',
  liquid:      'bg-teal-400',
}

export function TradeCard({ trade, onExecute }: Props) {
  const isLong   = trade.direction === 'LONG'
  const dirColor = isLong ? 'text-bull' : 'text-bear'
  const dirBg    = isLong ? 'bg-bull/10 border-bull/25' : 'bg-bear/10 border-bear/25'

  return (
    <div className={cn(
      'card p-5 animate-slide-up transition-all duration-200 hover:border-bg-hover',
      isLong ? 'shadow-glow-bull' : 'shadow-glow-bear',
    )}>
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={cn('flex h-10 w-10 items-center justify-center rounded-xl border', dirBg)}>
            {isLong
              ? <ArrowUpRight className={cn('h-5 w-5', dirColor)} />
              : <ArrowDownLeft className={cn('h-5 w-5', dirColor)} />
            }
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className={cn('ticker-mono text-xl', dirColor)}>{trade.ticker}</span>
              {trade.hot_sector && (
                <span className="flex items-center gap-0.5 rounded-full bg-caution/15 border border-caution/25 px-1.5 py-0.5 text-[10px] font-semibold text-caution">
                  <Flame className="h-2.5 w-2.5" /> HOT
                </span>
              )}
            </div>
            <p className="text-xs text-muted">{trade.sector}</p>
          </div>
        </div>

        <div className={cn('badge', bgColorForScore(trade.composite_score))}>
          <Zap className="h-3 w-3" />
          {trade.composite_score.toFixed(0)}
        </div>
      </div>

      {/* Price plan */}
      <div className="grid grid-cols-3 gap-2 mb-4">
        {[
          { label: 'Entry',   value: formatPrice(trade.risk.entry),       color: 'text-primary' },
          { label: 'Stop',    value: formatPrice(trade.risk.stop_loss),    color: 'text-bear'    },
          { label: 'Target',  value: formatPrice(trade.risk.take_profit),  color: 'text-bull'    },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-lg bg-bg-base px-3 py-2 text-center">
            <p className="text-[10px] text-muted mb-0.5">{label}</p>
            <p className={cn('font-mono text-sm font-semibold', color)}>{value}</p>
          </div>
        ))}
      </div>

      {/* Meta row */}
      <div className="flex items-center gap-4 mb-4 text-xs text-muted">
        <span>Qty <span className="text-subtle font-mono">{trade.risk.qty}</span></span>
        <span>R/R <span className="text-brand-cyan font-mono font-semibold">{trade.risk.risk_reward.toFixed(2)}x</span></span>
        <span>Risk <span className="text-bear font-mono">${trade.risk.dollar_risk.toFixed(0)}</span></span>
      </div>

      {/* Agent scores */}
      <div className="space-y-1.5 mb-4">
        {trade.evaluations.map(ev => (
          <div key={ev.role} className="flex items-center gap-2">
            <span className="w-20 text-[10px] text-muted capitalize">{ev.role}</span>
            <div className="flex-1 score-bar-track">
              <div
                className={cn('h-full rounded-full transition-all duration-700', AGENT_COLORS[ev.role] ?? 'bg-subtle')}
                style={{ width: `${ev.score}%`, opacity: 0.7 + ev.confidence * 0.3 }}
              />
            </div>
            <span className="w-7 text-right text-[10px] font-mono text-subtle">{ev.score}</span>
          </div>
        ))}
      </div>

      {/* CTA */}
      <button
        onClick={() => onExecute(trade)}
        className={cn(
          'w-full rounded-lg py-2.5 text-sm font-semibold transition-all duration-200',
          isLong
            ? 'bg-bull/15 border border-bull/30 text-bull hover:bg-bull/25'
            : 'bg-bear/15 border border-bear/30 text-bear hover:bg-bear/25',
        )}
      >
        {isLong ? '↑ Execute Long' : '↓ Execute Short'} · {trade.ticker}
      </button>
    </div>
  )
}
