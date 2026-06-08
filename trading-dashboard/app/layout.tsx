import type { Metadata } from 'next'
import './globals.css'
import { Sidebar } from '@/components/layout/Sidebar'
import { Toaster } from 'sonner'

export const metadata: Metadata = {
  title: 'Trading Bot Dashboard',
  description: 'AI-powered multi-agent trading intelligence',
  icons: {
    icon: '/favicon.svg',
    shortcut: '/favicon.svg',
  },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="flex h-dvh overflow-hidden bg-bg-base text-primary">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          {children}
        </main>
        <Toaster
          theme="dark"
          toastOptions={{
            style: {
              background: '#0F172A',
              border: '1px solid #1E293B',
              color: '#F1F5F9',
            },
          }}
        />
      </body>
    </html>
  )
}
