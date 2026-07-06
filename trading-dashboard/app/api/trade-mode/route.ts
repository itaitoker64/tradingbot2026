/**
 * GET  /api/trade-mode  → current execution mode { auto_execute }
 * POST /api/trade-mode  → toggle auto-execute { auto_execute: boolean }
 *
 * The DB is the source of truth for the user's preference — it survives bot
 * restarts on Railway. On GET we return the DB value and, if the bot is
 * online but disagrees (e.g. it just restarted and lost trade_mode.json),
 * we silently re-sync it so the bot catches up without a user action.
 */
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { botGet, botPost } from '@/lib/bot-api'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const user = await prisma.user.findUnique({
    where:  { id: session.user.id },
    select: { autoExecute: true },
  })
  const autoExecute = user?.autoExecute ?? false

  // Re-sync the bot if it has drifted (e.g. after a Railway restart).
  botGet<{ auto_execute?: boolean }>('/api/trade-mode')
    .then(data => {
      if (!!data.auto_execute !== autoExecute) {
        botPost('/api/trade-mode', { auto_execute: autoExecute }).catch(() => {})
      }
    })
    .catch(() => {})

  return NextResponse.json({ auto_execute: autoExecute })
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  let body: { auto_execute?: boolean }
  try { body = await req.json() } catch {
    return NextResponse.json({ error: 'Invalid body' }, { status: 400 })
  }

  const autoExecute = !!body.auto_execute

  // Remember the user's preference (display fallback when the bot is offline).
  await prisma.user.update({
    where: { id: session.user.id },
    data:  { autoExecute },
  })

  // Push the explicit change to the bot.
  botPost('/api/trade-mode', { auto_execute: autoExecute }).catch(() => {})

  return NextResponse.json({ status: 'ok', auto_execute: autoExecute })
}
