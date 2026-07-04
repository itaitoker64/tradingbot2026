-- Forgot-password: temporary credential stored alongside the real hash so an
-- unauthenticated reset request can no longer overwrite (DoS) the account
-- owner's password.
ALTER TABLE "User" ADD COLUMN "tempPasswordHash" TEXT;
ALTER TABLE "User" ADD COLUMN "tempPasswordAt" TIMESTAMP(3);
