/**
 * POST /api/bot/execute
 *
 * Executes a trade recommendation. Strategy (in order):
 *  1. Ask the bot's entry guards (circuit breaker / position / sector / beta
 *     caps) BEFORE placing anything — a 409 blocks the order itself. If the
 *     bot is unreachable the check is skipped (execute stays independent of
 *     localhost:8000 being up).
 *  2. Submit the bracket order to the signed-in user's Alpaca account. A
 *     rejected order FAILS the request — no phantom "paper" fallback mixing
 *     simulated trades into the real history.
 *  3. Record the fill with the bot server (best-effort).
 */
import { NextResponse }       from 'next/server'
import { revalidatePath }      from 'next/cache'
import { submitBracketOrder, getAccount } from '@/lib/alpaca'
import { getAlpacaCreds }     from '@/lib/session'
import { botPost, botTryPost } from '@/lib/bot-api'
import { sizePosition }       from '@/lib/risk'
import type { ExecuteRequest, ExecuteResponse } from '@/types/trading'

// ── Idempotency guard ──────────────────────────────────────────────────────
// Reject the same recommendation_id if it arrives again within 30 seconds.
// Prevents double-executions from rapid clicks or network retries.
const _recentIds  = new Map<string, number>() // rec_id -> timestamp ms
const _DEDUP_MS   = 30_000

function _isDuplicate(recId: string | undefined): boolean {
  if (!recId) return false
  const now = Date.now()
  for (const [id, ts] of _recentIds) {
    if (now - ts > _DEDUP_MS) _recentIds.delete(id)
  }
  if (_recentIds.has(recId)) return true
  _recentIds.set(recId, now)
  return false
}

export async function POST(req: Request) {
  const creds = await getAlpacaCreds()
  if (!creds) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Unauthorized' },
      { status: 401 },
    )
  }

  let body: ExecuteRequest
  try {
    body = await req.json()
  } catch {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Invalid request body' },
      { status: 400 },
    )
  }

  // Idempotency check — 409 if same rec_id arrives within 30s
  if (_isDuplicate(body.recommendation_id)) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: `Duplicate: '${body.recommendation_id}' already executed within 30s` },
      { status: 409 },
    )
  }

  const { ticker, direction, entry, stop_loss, take_profit } = body

  // Runtime validation — TypeScript types don't protect us at the boundary.
  if (!ticker || !/^[A-Z0-9.]{1,10}$/.test(ticker)) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Invalid ticker symbol' },
      { status: 400 },
    )
  }
  if (direction !== 'LONG' && direction !== 'SHORT') {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'direction must be LONG or SHORT' },
      { status: 400 },
    )
  }
  if (!(entry > 0) || !(stop_loss > 0) || !(take_profit > 0)) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'entry, stop_loss, and take_profit must be positive' },
      { status: 400 },
    )
  }
  // Bracket sanity: LONG needs SL < entry < TP; SHORT needs TP < entry < SL
  const bracketValid = direction === 'LONG'
    ? (stop_loss < entry && entry < take_profit)
    : (take_profit < entry && entry < stop_loss)
  if (!bracketValid) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Bracket legs are inverted for this direction' },
      { status: 400 },
    )
  }

  // -- 0. Size the position to the EXECUTING USER's own account equity
  let equity: number
  try {
    const account = await getAccount(creds)
    equity = parseFloat(account.equity)
  } catch (err: any) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: `Could not fetch your Alpaca account: ${err.message}` },
      { status: 401 },
    )
  }

  const qty = sizePosition(equity, entry, stop_loss)
  if (qty <= 0) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: 'Your account balance is too small to size this trade within risk limits' },
      { status: 422 },
    )
  }

  // -- 1. Bot entry guards BEFORE placing the order. A 409 means a risk gate
  //       (circuit breaker, max positions, sector/beta cap, duplicate) says
  //       no — block the real order, not just the bookkeeping afterwards.
  //       null = bot unreachable → proceed (execute must not depend on it).
  const guard = await botTryPost('/api/entry-check', {
    ticker,
    direction,
    composite_score: body.composite_score ?? null,
    beta:            (body as { beta?: number }).beta ?? null,
  })
  if (guard && guard.status === 409) {
    const reason = guard.data?.detail ?? 'blocked by bot risk guards'
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: `Trade blocked: ${reason}` },
      { status: 409 },
    )
  }

  // -- 2. Submit to the signed-in user's Alpaca account. Failure is failure:
  //       no fake PAPER order id — a rejected real-money order must be loud.
  let orderId: string
  try {
    const alpacaOrder = await submitBracketOrder(creds, {
      symbol:      ticker,
      side:        direction === 'LONG' ? 'buy' : 'sell',
      qty,
      stop_loss,
      take_profit,
      client_order_id: body.recommendation_id ? `dash-${body.recommendation_id}` : undefined,
    })
    orderId = alpacaOrder.id
  } catch (err: any) {
    return NextResponse.json(
      { success: false, order_id: '', qty: 0, message: `Alpaca rejected the order: ${err.message}` },
      { status: 502 },
    )
  }
  const message = `${direction} ${qty}x ${ticker} submitted to Alpaca ${creds.paper ? 'Paper' : 'Live'} (order ${orderId})`

  // -- 3. Record with the bot server (best-effort — the order stands either way)
  botPost('/api/execute', {
    ...body,
    qty,
    order_id: orderId,
    score:    body.composite_score ?? null,
  }).catch(() => {})

  // -- 4. Invalidate dashboard cache so positions refresh on next load
  revalidatePath('/')
  revalidatePath('/history')
  revalidatePath('/pnl')

  return NextResponse.json({
    success:  true,
    order_id: orderId,
    qty,
    message,
    alpaca:   true,
  } as ExecuteResponse & { alpaca: boolean })
}
