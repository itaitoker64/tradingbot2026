import type { DefaultSession } from 'next-auth'

declare module 'next-auth' {
  interface Session {
    user: {
      id: string
      mustChangePassword: boolean
    } & DefaultSession['user']
  }

  interface User {
    alpacaPaper:        boolean
    mustChangePassword: boolean
  }
}

// next-auth/jwt is just a re-export of @auth/core/jwt — augment the real module so the JWT type merge applies
declare module '@auth/core/jwt' {
  interface JWT {
    userId: string
    mustChangePassword: boolean
  }
}
