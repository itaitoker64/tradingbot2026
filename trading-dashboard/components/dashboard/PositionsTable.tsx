'use client'
import { cn, formatPrice } from '@/lib/utils'
import type { AlpacaPosition } from '@/lib/alpaca'

interface Props { positions: AlpacaPosition[] }

export function PositionsTable({ positions }: Props) {
  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-primary">Open Positions</h2>
        <span className="badge bg-brand-cyan/10 border-brand-cyan/20 text-brand-cyan">
          {positions.length} open
        </span>
      </div>

      {positions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-center">
          <p className="text-sm text-muted">No open positions</p>
          <p className="text-xs text-muted/60 mt-1">Execute a trade to see it here</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border">
                {['Symbol','Side','Qty','Avg Entry','Current','P&L','P&L %'].map(h => (
                  <th key={h} className="px-3 py-2 text-left font-medium text-muted">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map(p => {
                const pnl    = parseFloat(p.unrealized_pl)
                const pnlPct = parseFloat(p.unrealized_plpc) * 100
                const isLong = p.side === 'long'
                return (
                  <tr key={p.symbol} className="border-b border-bg-border/50 hover:bg-bg-hover transition-colors">
                    <td className="px-3 py-2.5 font-mono font-semibold text-primary">{p.symbol}</td>
                    <td className="px-3 py-2.5">
                      <span className={cn(
                        'rounded px-1.5 py-0.5 text-[10px] font-semibold',
                        isLong ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear',
                      )}>
                        {p.side.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 font-mono text-subtle">{p.qty}</td>
                    <td className="px-3 py-2.5 font-mono text-subtle">{formatPrice(parseFloat(p.avg_entry_price))}</td>
                    <td className="px-3 py-2.5 font-mono text-primary">{formatPrice(parseFloat(p.current_price))}</td>
                    <td className={cn('px-3 py-2.5 font-mono font-semibold', pnl >= 0 ? 'text-bull' : 'text-bear')}>
                      {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                    </td>
                    <td className={cn('px-3 py-2.5 font-mono font-semibold', pnlPct >= 0 ? 'text-bull' : 'text-bear')}>
                      {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
