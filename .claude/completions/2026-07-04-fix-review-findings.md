# Fix all findings from the 2026-07-04 end-to-end review

Companion to `.claude/completions/2026-07-04-app-end-to-end-review.md`.
Every Critical/High and all actionable Medium/Low findings are fixed.
Suites after changes: **417 pytest passed / 1 skipped**, **61 vitest passed**,
`tsc --noEmit` clean.

## Deployment steps required (one-time)

1. **Prisma migration** — new `tempPasswordHash` / `tempPasswordAt` columns:
   `npx prisma migrate deploy` (migration `20260704000000_add_temp_password`).
2. **Set `SIGNUP_INVITE_CODE`** in Vercel — sign-ups are now invite-only and
   DISABLED while it is empty (fail closed).
3. `BOT_API_SECRET` should already be set on Railway + Vercel; it is now also
   documented in `trading-dashboard/.env.example`.

## Security (dashboard)

- **C1** `/api/optimize/patch` + `/api/optimize/reset`: now require a session
  (both were fully unauthenticated proxies to the bot with the secret
  attached server-side).
- **C2** Signup is invite-only (`SIGNUP_INVITE_CODE`, timing-safe compare,
  fail closed when unset). Also fixed: the API demanded `phone` which the
  signup form never sent — phone is now optional (matches schema).
- **C3** `/api/trade-mode` GET is read-only (reports the bot's state; falls
  back to the user's stored preference only when the bot is offline). The old
  GET pushed the *requesting user's* personal `autoExecute` onto the shared
  bot on every page load — any account could silently arm/disarm the bot.
- **C4** Forgot-password no longer overwrites `passwordHash` (unauthenticated
  account-lockout DoS). A temporary password is stored in separate columns,
  expires after 1h, is single-use (promoted to the real hash at login, which
  then forces a change via the existing `mustChangePassword` flow), and the
  endpoint is rate-limited (3/email/15min). Real password keeps working until
  the temp one is used. `auth.ts` implements the temp-login path; reset- and
  change-password clear the temp columns.
- **M6** Alpaca key/secret no longer ride in the session JWT.
  `lib/session.getAlpacaCreds()` fetches + decrypts from the DB per request
  (also fixes staleness after a key update in Settings).

## Money-path (Python)

- **H1** `_trailing_stop_loop` now only manages simulated trades
  (`PAPER-*`/empty `order_id`). It could previously mark REAL bracket trades
  closed in trades.json while the position stayed open at Alpaca, feeding
  phantom exits into the breaker and the learning loop.
- **H2** Breakeven lock rewritten: uses broker-reported `avg_entry_price`
  (the old cost-basis derivation went negative for shorts → shorts never
  locked), and moves the bracket's stop leg via **in-place replace**
  (`PATCH /v2/orders/{id}`, new `AlpacaBroker.replace_order_stop`) instead of
  cancel+resubmit, which raced the TP leg's share hold and could leave the
  position with no stop. On replace failure the original stop is kept.
  Bonus fix: `get_open_orders` now returns `type` and includes bracket child
  legs (`nested=true`) — the old mapping stripped `type`, so the lock's stop-
  leg filter could never match anything.
- **H3** `/api/reset-circuit-breaker` persists an acknowledgement cutoff
  (`circuit_breaker_reset.json`); `_consecutive_losses` / `_daily_pnl_pct`
  ignore trades closed before it. Previously reset was a no-op (breaker
  recomputed from history and re-halted; deadlock when flat).
- **H4** `_daily_pnl_pct` denominator is now verified equity: cached broker
  reading (≤30min) → last EOD snapshot → conservative $1,000 floor. The old
  `|total_pnl|*3+1000` estimate violated the fail-closed rule and inflated
  with history until real 2% losses couldn't trip the halt.
- **H5** Fixed the `NameError: wait` in `_background_loop`'s scan error
  handler (could kill the whole background loop) and added real exponential
  backoff (30s → 5min).
- **H6** Single path resolver `core.paths.data_dir()` (volume-backed when
  attached): PortfolioManager, RiskAgent, DecisionAgent, WeightTuner,
  live_runner, bootstrap broker-mode, and api_server's learning aliases all
  read/write the SAME `strategy_weights.json` / runtime files. Previously the
  optimizer Apply wrote to the Railway volume while the agents read the
  repo-relative copy (tuned params were dead letters) and tuner output was
  wiped every deploy.
- **H7** New entries are wall-clock gated (`PortfolioManager._entry_window_open`):
  Mon–Fri, 09:30 ET → (16:00 − `EOD_FLATTEN_MIN_BEFORE`). A rescan/breakout
  between the 15:55 flatten and 16:10 could previously open an unmanaged
  overnight position. Also reconciled: `eod_flatten_loop` stands down when
  `ALLOW_OVERNIGHT=true` so it can't liquidate positions the EOD review chose
  to hold.
- **H8** `RegimeSnapshot.vix_is_proxy` flag: VIX>30/40 position scaling (and
  the LLM prompt label) now skip VIXY *price* readings — a failed ^VIX fetch
  previously halved every position on a calm day whenever VIXY traded >$40.

## Medium

- **M1/M3** Idempotent `client_order_id` everywhere orders are placed:
  `tbot-<uuid>` (PC broker), `autoexec-<rec_id>` (Railway auto-executor),
  `dash-<recommendation_id>` (dashboard) — Alpaca rejects duplicates
  server-side, closing the cross-lambda double-click hole.
- **M2** Dashboard execute flow: bot entry guards are consulted BEFORE the
  order via new bot endpoint `POST /api/entry-check` (409 blocks the order;
  bot-offline proceeds), and an Alpaca rejection now fails the request loudly
  instead of fabricating a `PAPER-` id that mixed simulated trades into the
  real history.
- **M4** PortfolioManager kill-switch state (baseline / peak / halted) is
  persisted per ET day (`kill_switch.json`) and restored on restart — a
  mid-day restart no longer re-baselines away losses. Opt-in
  (`persist_state=True` from `bootstrap.build_manager`) so tests/backtests
  stay hermetic.
- **M5** live_runner's `strategy_refresh_loop` honors `live_tuning_active`,
  matching `RiskAgent._effective_atr_multiples`.
- **M7** `_entry_guard_reason` blocks a second open trade in the same symbol
  in EITHER direction (opposite-side brackets conflict with held shares).
- **M8** `_close_position_via_alpaca` cancels the symbol's open orders before
  liquidating (Alpaca rejects closes while bracket legs hold the shares — the
  EOD review's "close for safety" previously failed on bracket trades).
- **M9** `requirements.txt`: pandas-ta made an optional manual install (its
  PyPI pin broke fresh installs; the TechnicalAgent already degrades cleanly).
- **M10** `_kelly_qty` caps composite-derived win probability at 0.65 and
  anchors to the measured `win_rate_30d` once ≥15 tuner updates exist.

## Low

- ADX ±DM tie handling now zeroes both (decided from original values).

## Deliberately NOT changed

- Log rotation for decisions.jsonl / rejections / learning history (growth is
  slow; revisit if disk becomes a concern).
- Static `_CORRELATION_GROUPS` (data-derived graph already takes precedence).
- Public `/api/health` (needed by the keep-awake pinger; exposes booleans only).

## New tests

- PM: entry-window gate (mid-session / flatten margin / pre-open / weekend),
  kill-switch restart persistence, VIX-proxy scaling skip (2 decide-level).
- api_server: circuit-breaker reset acknowledgement (2), duplicate-symbol
  guard both directions (2).
- Dashboard: execute guard-409 block, bot-offline proceed, loud Alpaca
  failure, idempotent client_order_id; forgot-password non-destructive update
  + rate limiting.
