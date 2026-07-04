// trading-dashboard/lib/session.ts
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { decrypt } from '@/lib/crypto'
import type { AlpacaCreds } from '@/lib/alpaca'

/**
 * Returns the signed-in user's Alpaca credentials, or null if unauthenticated.
 *
 * Credentials are fetched and decrypted from the DB per request rather than
 * carried in the session JWT — broker secrets must not live in a browser
 * cookie, and DB reads also mean a key update takes effect immediately
 * instead of after the next token refresh.
 */
export async function getAlpacaCreds(): Promise<AlpacaCreds | null> {
  const session = await auth()
  const userId = session?.user?.id
  if (!userId) return null

  const user = await prisma.user.findUnique({
    where:  { id: userId },
    select: { alpacaKeyId: true, alpacaSecret: true, alpacaPaper: true },
  })
  if (!user?.alpacaKeyId || !user?.alpacaSecret) return null

  try {
    return {
      keyId:  decrypt(user.alpacaKeyId),
      secret: decrypt(user.alpacaSecret),
      paper:  user.alpacaPaper,
    }
  } catch {
    return null
  }
}
