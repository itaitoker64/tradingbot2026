/**
 * Server-side bot API client — proxies to the running FastAPI server.
 * Use these only in Next.js API routes or server components.
 */

const BOT_URL = process.env.TRADING_BOT_API_URL ?? 'http://localhost:8000'

export async function botGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BOT_URL}${path}`, {
    next: { revalidate: 15 },
  })
  if (!res.ok) throw new Error(`Bot API ${path} → ${res.status}`)
  return res.json()
}

export async function botPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BOT_URL}${path}`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
    cache:   'no-store',
  })
  if (!res.ok) throw new Error(`Bot API POST ${path} → ${res.status}`)
  return res.json()
}
