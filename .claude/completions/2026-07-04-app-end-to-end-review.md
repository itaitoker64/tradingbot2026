# End-to-End Application Review — 2026-07-04

Scope: full pass over `trading_bot/` (engine, agents, brokers, api_server) and
`trading-dashboard/` (auth, API routes, proxy layer), plus CI, deps, and repo
hygiene. Both test suites were run: **407 passed / 1 skipped (pytest)**,
**56 passed (vitest)**, `tsc --noEmit` clean. No secrets are committed.

Findings are ordered by severity. File references use `path:line`.

---

## CRITICAL — security (internet-facing)

### C1. `/api/optimize/patch` and `/api/optimize/reset` are unauthenticated
`trading-dashboard/app/api/optimize/patch/route.ts` and `optimize/reset/route.ts`
do no `auth()` check (the middleware matcher deliberately excludes `/api/*`,
`middleware.ts:37`). The routes proxy to the bot with `BOT_API_SECRET` attached
server-side (`lib/bot-api.ts:18-22`). **Anyone on the internet can PATCH or
reset the live strategy weights** (ATR multiples, thresholds) of the production
bot via the Vercel URL, no login required. Every other `/api/bot/*` route
checks the session — these two were missed.

### C2. Open self-signup grants control over the shared bot
`/api/auth/signup` is public and only requires *any* valid Alpaca key pair —
free paper keys qualify. Once signed in, a stranger can reach shared-bot
controls: flip the auto-execute toggle, switch brokers (which flattens open
positions — `live_runner.py:512-523`), trigger scans/backtests/optimizer runs,
reset the circuit breaker, and record trades into the shared `trades.json`.
The dashboard is multi-user in appearance but the bot state behind it is a
single shared instance; per-user auth ≠ authorization for shared controls.

### C3. Cross-user clobbering of the auto-execute toggle
`app/api/trade-mode/route.ts` GET syncs **the requesting user's** DB
`autoExecute` value to the shared bot file on every page load
(fire-and-forget `botPost`). Any user loading the dashboard overwrites the
bot's shared toggle with their personal setting — the owner's "auto-execute
ON" can be silently switched off by another account's page view (or ON, arming
the Railway auto-executor, by a self-registered stranger — see C2).

### C4. Forgot-password is an unauthenticated account-DoS
`app/api/auth/forgot-password/route.ts` immediately **replaces** the user's
password hash with a temp password on every request, with no rate limit and no
confirmation step. Anyone who knows/guesses an email can lock the real user
out repeatedly (and if Brevo email fails, the user is stranded — the error is
swallowed). Standard fix: send a time-limited reset link; never mutate the
credential on an unauthenticated request.

---

## HIGH — money-path correctness

### H1. Trailing-stop loop "closes" real Alpaca positions on paper only
`api_server.py:_trailing_stop_loop` (1921-2017) ratchets `stop_loss` and, when
price crosses it, calls `_close_simulated_trade()` on **any** open trade — the
docstring says "for PAPER- simulated orders" but there is no `order_id`
filter (contrast `_do_exit_position:2091`, which does distinguish). For a real
bracket trade this: (a) never moves the real Alpaca stop order, and (b) marks
the trade closed in `trades.json` while the real position stays open at Alpaca
with its original bracket. Consequences: position tracking diverges from the
broker; `_check_and_close_trades` stops watching it (status != open); phantom
exits feed the circuit breaker, win-rate learning, agent attribution, and
Telegram with fictitious results.

### H2. Breakeven lock can strip a position of stop protection
`live_runner.py:breakeven_lock_loop` (288-375):
- It **cancels the bracket's stop leg first**, then submits a standalone stop.
  Alpaca holds the position's shares against the remaining TP limit leg, so
  the new stop order is typically rejected ("insufficient qty available") —
  leaving the position with **no stop at all**. The right primitive is order
  *replace* on the stop leg (PATCH /v2/orders/{id}), not cancel+new.
- Short positions can never lock: `cost = market_value - unreal` is negative
  for shorts, so `entry <= 0 → continue` (line 342-344).
- Lines 348-351 are duplicated long/short conditions (identical), harmless
  but a sign the short path was never exercised.

### H3. Manual circuit-breaker reset is a no-op (and can deadlock flat)
`api_server.py:reset_circuit_breaker` (3086) clears `_circuit_breaker`, but
`_check_circuit_breaker()` (1103) **recomputes** consecutive losses from trade
history on every entry attempt and immediately re-halts. After
`MAX_CONSECUTIVE_LOSSES` losing closes with no open positions, no new entry
can ever occur (entries blocked → no new trade can break the streak) until
someone hand-edits `trades.json`. The reset endpoint needs to persist an
acknowledgement (e.g. "ignore losses before timestamp T").

### H4. Daily-loss circuit breaker uses fabricated equity
`api_server.py:_daily_pnl_pct` (1062-1081): the denominator is
`max(abs(all_pnl)*3 + 1000, 1000)` — an invented equity figure. This directly
violates the project's own fail-closed rule (COMMON_MISTAKES #2: never
fabricate equity). The 2% `DAILY_LOSS_LIMIT_PCT` is therefore applied against
an arbitrary base: with little history the floor is $1,000, so a $20 loss
halts the day; with a long profitable history the base inflates and real 2%
account losses won't halt. Fetch real equity (fail closed on error) or use
the day's snapshot.

### H5. `NameError: wait` can kill the entire background loop
`api_server.py:2415` — `logger.error("Scanner error #%d (backoff=%ds)...",
consecutive_errors, wait, exc)`: `wait` is never defined. If
`_run_market_scan()` ever raises (most of the inner body is try-wrapped, but
not all — e.g. code before the main `try:` at ~1344), the except handler
itself raises NameError, which propagates out of `_background_loop` and
permanently stops scans, trade-close detection, and rec revalidation until
redeploy. Also: the intended error backoff (`consecutive_errors`) does
nothing — there is no sleep scaling.

### H6. Railway volume split-brain: two `strategy_weights.json`, cross-wired
With a Railway volume attached, `api_server` writes weights/trade-mode/broker-
mode to `$VOLUME/data/` (`api_server.py:245-256`), but the agent pipeline
reads/writes the **repo-relative** `trading_bot/data/strategy_weights.json`:
`PortfolioManager._WEIGHTS_FILE` (portfolio_manager.py:47), `RiskAgent`
(risk_agent.py:25), `DecisionAgent` (decision_agent.py:23), `WeightTuner`
(weight_tuner.py:41), `live_runner` (live_runner.py:59). So on Railway:
- Optimizer "Apply Optimal Params" (`_save_weights` → volume file) **never
  reaches** the RiskAgent/PM in the same process — tuned params are dead
  letters (`live_tuning_active` is set in a file nobody reads).
- WeightTuner output lands on the ephemeral repo path and is wiped each
  deploy — the exact problem the volume was added to solve.
The comment at api_server.py:260-264 acknowledges the split but the Apply
path (its primary consumer) writes to the other file. All readers/writers
should resolve through one function (e.g. `core/paths.py`).

### H7. Bot can re-enter positions *after* the EOD flatten
`eod_flatten_loop` fires once in [15:55, 16:00) (`bootstrap.py:195`), but
`_is_market_hours()` allows evaluations until **16:10** (`live_runner.py:97-103`)
and the rescan/breakout loops keep running. A rescan at 15:57 can open a new
bracket after the flatten already fired → overnight position in a
day-trade-only bot (with a `time_in_force=day` bracket whose protective legs
expire at the close). There is no "no new entries after T" gate; `execute()`
checks `_halted` but not time-of-day.
Related inconsistency: `_eod_position_review_loop` can decide to HOLD a
position overnight (`ALLOW_OVERNIGHT`), but the PC bot's flatten loop will
still liquidate it at 15:55.

### H8. VIXY proxy price is fed into real-VIX position-scaling cutoffs
`detect_regime` falls back to the **VIXY ETF price** as `vix_level`
(regime_agent.py:233-235) — correctly using separate thresholds for regime
classification — but `RegimeSnapshot` carries no flag saying which scale it
is. `PortfolioManager.decide` then applies `vix > 30 → 70% size, vix > 40 →
50% size` (portfolio_manager.py:241-248) against that value. VIXY trades in
the $30-$50 range for long stretches (reverse splits), so whenever the Yahoo
^VIX fetch fails, every position is silently cut 30-50% on a normal-vol day.
The `VIX={level}` string also goes into the DecisionAgent prompt.

---

## MEDIUM

### M1. No idempotency on order submission
`AlpacaBroker.submit_bracket` (alpaca_broker.py:254) sends no
`client_order_id`. Writes are deliberately not retried (good), but a timeout
after the POST reached Alpaca leaves a live order the bot believes failed —
no `_track_fill`, no memory record; only the duplicate-position guard on the
next cycle papers over it. Supplying a deterministic `client_order_id` makes
the submit safely retryable and reconcilable.

### M2. Dashboard execute: real order placed, record best-effort
`app/api/bot/execute/route.ts`: the Alpaca bracket is submitted first, then
the bot is notified fire-and-forget (`botPost(...).catch(() => {})`). If the
bot 409s (circuit breaker, max positions, sector cap) or is offline, the
**real order stands** but is never tracked — and none of the bot-side entry
guards actually prevented it. The response is also `success: true` even when
Alpaca rejected the order (falls back to a `PAPER-` id), silently mixing
simulated trades into the same history as real ones.

### M3. Execute idempotency map is per-lambda
The 30s dedup `Map` (execute/route.ts:22) is module state on Vercel
serverless — concurrent double-clicks can land on different instances and
both execute. Low probability, real money. A DB unique constraint on
`recommendation_id` would be robust.

### M4. Kill-switch baseline resets on every restart
`PortfolioManager._day_start_equity` is in-memory (portfolio_manager.py:110).
A mid-day restart (crash, broker toggle → session rebuild) re-baselines at
current equity, so the daily-loss and intraday-drawdown halts forget losses
already taken. Persist `{date, day_start_equity, halted}` to disk.

### M5. `strategy_refresh_loop` bypasses the `live_tuning_active` gate
`live_runner.py:251-285` applies `atr_stop_multiple` / `atr_target_multiple`
from `strategy_weights.json` to `pm.risk.cfg` **without** checking
`live_tuning_active`, while `RiskAgent._effective_atr_multiples` deliberately
honors that flag. The self-tuner writes ATR values without activating the
flag (api_server.py:876-880 comment), so the live loop applies numbers the
risk agent's own policy says should be inactive — and it permanently mutates
the cfg baseline in the process.

### M6. Alpaca API secrets ride in the session JWT
`auth.config.ts:23-27` stores the decrypted Alpaca key/secret in the NextAuth
JWT (encrypted JWE cookie). It works, but the broker secret now leaves the
server on every request and lives in browser cookie storage; a leaked
`AUTH_SECRET` decrypts every user's brokerage credentials. Prefer keeping
only `userId` in the token and decrypting from the DB per request.

### M7. Auto-executor / manual-execute duplicate-position gaps
`_entry_guard_reason` (api_server.py:2788) checks circuit breaker, max
positions, sector, and beta — but not "already open in this symbol".
`_run_auto_executor` adds a same-ticker+same-direction check (3010-3014),
so the same ticker in the *opposite* direction passes, and manual
`/api/execute` records duplicates freely beyond the dashboard's 30s window.
On Alpaca, an opposite-side bracket on an existing position will conflict
with the held shares/legs.

### M8. Exit monitor closes will fail for bracket-held positions
`_close_position_via_alpaca` (api_server.py:2024) DELETEs
`/v2/positions/{ticker}` without cancelling the trade's open bracket legs
first; Alpaca rejects liquidation when shares are held by open orders. The
EOD review path then leaves the trade open (ok=False), so the "close for
safety" decision doesn't actually close anything on brackets placed by the
auto-executor.

### M9. `requirements.txt` is not installable as pinned
`pandas-ta>=0.3.14b` has no matching distribution on PyPI for this
environment (only yanked/renamed releases; newer requires Python ≥3.12) — a
fresh `pip install -r requirements.txt` fails before pytest can run. The code
already treats pandas-ta as optional (technical_agent.py:30-34); make the
requirement optional/extras or pin an installable source.

### M10. Composite-score-as-probability Kelly
`api_server.py:_kelly_qty` (400-426) treats `composite_score/100` as the win
probability. The composite is an uncalibrated blend (~50-75 in practice), so
p is systematically optimistic vs the measured ~40-60% win rates the
RiskAgent's own Kelly uses. The two Kelly implementations (api_server rec
sizing vs risk_agent live sizing) can disagree substantially for the same
signal.

---

## LOW / hygiene

- **PortfolioManager conviction boost** (portfolio_manager.py:253-267) sizes
  up to +20% *after* the RiskAgent capped qty by `max_position_pct`; it
  re-caps against equity, fine — but uses `ctx.account` equity which can be
  one cycle stale relative to the plan's sizing equity.
- **`decisions.jsonl` / logs unbounded growth** — `_AUDIT_FILE`, `REJECT_LOG`,
  learning history all append forever; no rotation.
- **`_observe_positions` counts EOD flatten exits as "stops"** when UPL<0 —
  can trip the loss-streak guard at 15:56 for the next morning? No: halt is
  time-boxed (60 min), so it expires overnight. Just noise in memory/lessons.
- **ADX tie-zeroing** (technical_agent.py:880-883): when +DM == -DM both
  should zero; current order keeps -DM. Cosmetic signal noise.
- **`/api/health` is unauthenticated by design** and reveals config booleans
  (which keys are set). Acceptable, but be aware it's public recon surface.
- **CORS** on FastAPI defaults to localhost — fine because Vercel proxies
  server-side; just don't set `CORS_ALLOW_ORIGINS=*` together with a weak
  `BOT_API_SECRET`.
- **DecisionAgent prompt injection surface**: news headlines and squeeze/insider
  rationales are interpolated into the LLM prompt. The code-level risk veto
  still binds, but direction/composite can be steered by a crafted headline.
  Treat headline text as untrusted (it already is in effect — just noting).
- **Static correlation groups** (portfolio_manager.py:55-61) go stale; the
  data-derived graph mitigates this when it covers the symbol.
- **keep-awake workflow**: fine; last ping 20:50 UTC — acceptable margins in
  both DST states.

---

## What's genuinely good (keep it)

- Fail-closed conventions are mostly consistently applied: `get_account() → {}`,
  RiskAgent refusing to size without equity, entry guard failing closed on
  portfolio-state errors, freshness veto on stale bars.
- Single composition point (`bootstrap.build_manager`) is respected by
  main/live/api_server/backtests — the drift class of bug is designed out.
- `_merge_trade_changes` correctly solves concurrent trades.json writers.
- Read-vs-write retry asymmetry in AlpacaBroker (retry GETs, never POSTs) is
  the right call.
- Backtest look-ahead hygiene (point-in-time agents neutralized, Kelly
  disabled in backtests, bar-timestamp-based time-of-day phase) is unusually
  careful.
- Auth fundamentals: bcrypt with dummy-hash timing equalization, AES-256-GCM
  for stored keys, Telegram notify endpoint fails closed without its secret.
- Test discipline is real: 407 Python tests + 56 dashboard tests, all green.

---

## Recommended priority order

1. Lock down the dashboard: auth on `optimize/patch|reset`, gate signup
   (invite code/allowlist), make shared-bot controls owner-only, fix
   trade-mode cross-user sync (C1-C3).
2. Fix forgot-password to a reset-link flow (C4).
3. Restrict the trailing-stop loop to `PAPER-` trades (or make it replace the
   real stop order) (H1).
4. Breakeven lock: use Alpaca order-replace; fix the short-side entry calc (H2).
5. Make circuit-breaker reset persistent, and daily P&L use real equity (H3, H4).
6. Define `wait` / add real backoff in `_background_loop` (H5) — one-line fix.
7. Unify strategy_weights/trade_mode path resolution through `core/paths.py` (H6).
8. Add a "no new entries after 15:5x" gate; reconcile EOD review vs flatten (H7).
9. Tag `RegimeSnapshot` with the VIX source; only apply VIX sizing cutoffs to
   the real index (H8).
10. Add `client_order_id` idempotency to both bracket submitters (M1-M3).
