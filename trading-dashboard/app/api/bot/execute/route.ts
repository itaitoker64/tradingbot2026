import { NextResponse } from 'next/server'
import { botPost } from '@/lib/bot-api'
import type { ExecuteRequest } from '@/types/trading'

export async function POST(req: Request) {
  try {
    const body: ExecuteRequest = await req.json()
    const result = await botPost('/api/execute', body)
    return NextResponse.json(result)
  } catch (err: any) {
    return NextResponse.json({ success: false, order_id: '', message: err.message }, { status: 502 })
  }
}
