// trading-dashboard/app/api/auth/forgot-password/route.ts
//
// Issues a TEMPORARY password stored alongside the real hash. The real
// password keeps working — an unauthenticated request must never be able to
// lock the account owner out (the old flow overwrote passwordHash directly,
// which let anyone who knew an email DoS that account repeatedly).
// The temp password is single-use (consumed at login) and expires.
import { NextResponse } from 'next/server'
import bcrypt from 'bcryptjs'
import { prisma } from '@/lib/prisma'
import { generateTemporaryPassword } from '@/lib/generatePassword'
import { sendTemporaryPasswordEmail } from '@/lib/brevo'

// Naive per-instance rate limit: 3 requests per email per 15 minutes.
// Serverless instances don't share this map, but it still blunts scripted
// abuse from a single connection.
const _recent = new Map<string, number[]>()
const WINDOW_MS = 15 * 60 * 1000
const MAX_PER_WINDOW = 3

function rateLimited(email: string): boolean {
  const now = Date.now()
  const hits = (_recent.get(email) ?? []).filter(t => now - t < WINDOW_MS)
  if (hits.length >= MAX_PER_WINDOW) { _recent.set(email, hits); return true }
  hits.push(now)
  _recent.set(email, hits)
  return false
}

export async function POST(req: Request) {
  const body = await req.json().catch(() => null) as { email?: string } | null
  const email = body?.email?.trim().toLowerCase()

  if (email && !rateLimited(email)) {
    const user = await prisma.user.findFirst({ where: { email: { equals: email, mode: 'insensitive' } } })
    if (user) {
      const tempPassword = generateTemporaryPassword()
      const tempPasswordHash = await bcrypt.hash(tempPassword, 10)
      await prisma.user.update({
        where: { id: user.id },
        data: { tempPasswordHash, tempPasswordAt: new Date() },
      })

      try {
        await sendTemporaryPasswordEmail(email, tempPassword)
      } catch (err) {
        console.error('Failed to send password reset email', err)
      }
    }
  }

  // Always return success — don't reveal whether the email is registered.
  return NextResponse.json({ success: true })
}
