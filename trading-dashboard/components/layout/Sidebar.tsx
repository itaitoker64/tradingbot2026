'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import {
  LayoutDashboard, TrendingUp, History, BarChart2,
  Zap, Settings, ExternalLink, FlaskConical,
} from 'lucide-react'
import { cn } from '@/lib/utils'

const nav = [
  { href: '/',          icon: LayoutDashboard, label: 'Dashboard'  },
  { href: '/trades',    icon: TrendingUp,      label: 'Trades'     },
  { href: '/history',   icon: History,         label: 'History'    },
  { href: '/pnl',       icon: BarChart2,       label: 'P&L'        },
  { href: '/backtest',  icon: FlaskConical,    label: 'Backtest'   },
]

export function Sidebar() {
  const path = usePathname()
  return (
    <aside className="flex w-[220px] flex-col border-r border-bg-border bg-bg-card shrink-0">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-bg-border">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-cyan/10 border border-brand-cyan/30">
          <Zap className="h-4 w-4 text-brand-cyan" />
        </div>
        <div>
          <p className="text-sm font-semibold text-primary leading-tight">TradingBot</p>
          <p className="text-[10px] text-muted leading-tight">AI Intelligence</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-widest text-muted/60">
          Navigation
        </p>
        {nav.map(({ href, icon: Icon, label }) => {
          const active = href === '/' ? path === '/' : path.startsWith(href)
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all duration-150',
                active
                  ? 'bg-brand-cyan/10 text-brand-cyan border border-brand-cyan/20'
                  : 'text-subtle hover:bg-bg-hover hover:text-primary'
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {label}
              {active && (
                <div className="ml-auto h-1.5 w-1.5 rounded-full bg-brand-cyan" />
              )}
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="border-t border-bg-border px-3 py-3 space-y-0.5">
        <Link
          href="/settings"
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
        >
          <Settings className="h-4 w-4" />
          Settings
        </Link>
        <a
          href="https://github.com/itaitoker64/tradingbot2026"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-subtle hover:bg-bg-hover hover:text-primary transition-colors"
        >
          <ExternalLink className="h-4 w-4" />
          GitHub
        </a>
      </div>
    </aside>
  )
}
