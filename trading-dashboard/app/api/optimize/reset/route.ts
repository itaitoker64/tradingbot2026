import { NextResponse } from 'next/server'
import { auth } from '@/auth'
import { botPost } from '@/lib/bot-api'

export async function POST() {
  const session = await auth()
  if (!session?.user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  try {
    const data = await botPost('/api/optimize/reset', {})
    return NextResponse.json(data)
  } catch {
    return NextResponse.json({ error: 'Failed to reset strategy weights' }, { status: 502 })
  }
}
