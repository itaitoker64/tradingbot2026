/**
 * GET  /api/trade-mode  → this user's execution preference { auto_execute }
 * POST /api/trade-mode  → save preference { auto_execute: boolean }
 *
 * Per-user setting stored in Neon DB. AUTO means the dashboard executes
 * trades automatically on this user's behalf; MANUAL means they click each
 * one themselves. The bot's own server-side loop is a separate mechanism
 * (AUTO_EXECUTE_ON_RAILWAY) and is not touched here.
 */
import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'

export const dynamic = 'force-dynamic'

export async function GET() {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const user = await prisma.user.findUnique({
    where:  { id: session.user.id },
    select: { autoExecute: true },
  })
  return NextResponse.json({ auto_execute: user?.autoExecute ?? false })
}

export async function POST(req: Request) {
  const session = await auth()
  if (!session?.user?.id) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  let body: { auto_execute?: boolean }
  try { body = await req.json() } catch {
    return NextResponse.json({ error: 'Invalid body' }, { status: 400 })
  }

  const autoExecute = !!body.auto_execute
  await prisma.user.update({
    where: { id: session.user.id },
    data:  { autoExecute },
  })
  return NextResponse.json({ status: 'ok', auto_execute: autoExecute })
}
