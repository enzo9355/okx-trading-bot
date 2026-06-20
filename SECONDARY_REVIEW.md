# Codex Secondary Review — OKX Automated Trading Bot

Reviewed GitHub commit: `8f31c7d2a94573f1be823652cfa53d7ed45c7f9f`

Baseline: 30 tests passed. After the fixes in this review: 45 tests pass.

## Release decision

**Not approved for real-money trading yet.** The code fixes below materially improve safety, but the exchange-side futures stop-loss remains an operational blocker until `scripts/verify_sl_attachment.py` passes against the exact ccxt version and OKX account mode deployed on the VM.

## File-by-file findings

### `spot/trader.py`

#### CRITICAL — A sell signal could sell assets the bot did not buy — FIXED

- Location: `run_once`, now lines 31–102.
- Problem: the original sell path used `min(free_balance, risk_sized_amount)` without requiring a registered bot position. A death cross could therefore sell a student's pre-existing wallet holdings even when the bot had never bought that symbol.
- Fix: the sell path now requires a `spot:symbol` registry entry and caps the order to `min(free_balance, registered_amount)`. Entry-only risk checks no longer block this risk-reducing exit. Tests cover both an unregistered wallet balance and a registered bot position.

#### CRITICAL — Spot stop-loss could be bypassed by a buy or sell signal — FIXED

- Location: `_check_stop_loss`, now lines 104–158.
- Problem: C2 is genuine. The original code called `_check_stop_loss` only inside the `hold` branch. A breached stop was skipped whenever the same cycle produced `buy` or `sell`.
- Fix: stop-loss handling now runs before every strategy branch and returns a handled flag. A breached stop ends the cycle, preventing a same-cycle re-entry. Partial fills reduce the registry by the confirmed fill instead of blindly deleting the position.

#### MAJOR — Requested amount was stored as if it were filled — FIXED

- Location: `_execution_details`, now lines 160–181.
- Problem: exchange precision, partial fills, and average execution price could differ from the requested amount and ticker price. Overstating a spot position can later consume part of a manual holding when the bot exits.
- Fix: registry and trade log updates prefer `order.filled` and `order.average`, with a documented fallback for dry-run or sparse exchange responses.

### `core/risk.py`

#### CRITICAL — Daily loss used the USDT currency balance instead of account equity — FIXED

- Location: `extract_equity`, now lines 112–143.
- Problem: ccxt maps OKX `details[].eq` into each currency's `balance["total"]`. The original function returned `total["USDT"]` before inspecting OKX `totalEq`. Buying spot with USDT reduces the USDT balance even when account equity is unchanged, so normal asset allocation could be misclassified as a daily loss and trip the circuit breaker.
- Fix: prefer account-wide OKX `totalEq`, then `adjEq`, and use the quote-currency balance only as a compatibility fallback. Regression tests reproduce the USDT-versus-total-equity case.

#### CRITICAL — Daily loss halt stopped protective monitoring — FIXED

- Location: `EntryHalted` at line 17 and `main.py` line 73.
- Problem: the original `RiskLimitError` handler set the global shutdown event. Reaching the daily loss limit stopped new entries, but also stopped spot stop-loss checks, futures margin checks, and stop-fill reconciliation for positions already open.
- Fix: daily loss now raises `EntryHalted`. Workers remain alive to manage exits while every new entry continues to be rejected from the persisted daily state. Unreadable risk or position state still raises the fatal `RiskLimitError` and stops all workers.

#### CRITICAL — Corrupt risk state reset the daily circuit breaker — FIXED

- Location: `_load_state` / `_save_state`, now lines 195–232.
- Problem: malformed or unreadable JSON returned `{}`, resetting the daily baseline after a crash. Writes were in-place, so a process or VM failure could create exactly that corruption.
- Fix: reads fail closed; writes use flush, `fsync`, and atomic replacement in the same directory. Persistence failure is fatal because the bot can no longer prove the circuit breaker state.

#### MAJOR — `MAX_ORDER_NOTIONAL_USDT` rejected orders instead of sizing to the cap — FIXED

- Location: `max_order_notional`, now line 81.
- Problem: traders sized from the percentage cap and only later checked the hard cap. If the hard cap was lower, every entry was rejected rather than reduced to the configured amount.
- Fix: sizing now uses the lower of the percentage cap and hard USDT cap; the strict validation remains in `assert_order_notional`.

#### MAJOR — Missing futures margin ratio is fail-open — OPEN

- Location: `margin_ratio_breached`, lines 105–109.
- Problem: `ratio is None` returns safe. OKX legitimately leaves the ratio blank with no exposure, but a parsing or account-mode change could also make it blank while a futures position is open, silently disabling the margin guard.
- Concrete fix: make the result tri-state. If the ratio is unavailable, query open positions; return safe only when none exist, otherwise raise `EntryHalted` for new exposure and emit a high-severity alert until the ratio is readable. Do not liquidate solely because telemetry is missing.

### `core/position_registry.py`

#### CRITICAL — Dry-run positions could become live sell instructions — FIXED

- Location: `for_mode`, now line 41.
- Problem: dry-run and executable modes shared `positions.json`. A simulated spot buy could survive a restart; after switching `DRY_RUN=false`, its simulated stop-loss could sell real wallet assets.
- Fix: dry-run state is isolated in `positions.dry-run.json`. Live and simulated position lifecycles cannot cross.
- Deployment note: this cannot identify historical contamination. Before deploying, stop the service and compare the existing `data/positions.json` with actual OKX positions/fills; archive and rebuild it if dry-run was ever used with that file.

#### MAJOR — Registry corruption was fail-open and writes were non-atomic — FIXED

- Location: `_load` / `_save`, now lines 171–205.
- Problem: corrupt JSON was logged and treated as an empty registry. That disabled bot-only ownership checks, the global position cap, and stop tracking. In-place writes could create partial JSON during a crash.
- Fix: unreadable state raises fatal `RiskLimitError`; writes use `fsync` plus atomic replacement. Tests verify corrupt state refuses to trade.

#### MAJOR — Global position cap had a check-then-act race — FIXED

- Location: `entry_slot`, now line 54.
- Problem: workers independently called `open_count`, placed an order, then recorded it. Correlated symbols could all observe the same free slot and exceed `MAX_OPEN_POSITIONS`.
- Fix: a process-wide re-entrant lock now serializes capacity check, order placement, and registry update. This intentionally serializes only rare entry operations, not normal market polling.

#### MAJOR — Crash after exchange acceptance but before registry write remains possible — OPEN

- Location: the order/registry boundary in both traders.
- Problem: no local JSON transaction can be atomic with an external exchange order. A process kill in that narrow window leaves a real position without a local record.
- Concrete fix: add a bot-specific `clOrdId` prefix to every entry, then rebuild missing registry entries at startup from OKX open orders/positions and recent fills before enabling new entries. For spot, use those tagged fills rather than wallet balance so manual holdings remain distinguishable.

### `futures/trader.py`

#### MAJOR — Signal-flip exit logged the new entry size — FIXED

- Location: `ClosedPosition` at line 26 and `close_positions_by_direction` at line 397.
- Problem: M2 is confirmed. Flip-close rows used the newly calculated entry `contracts`, not the contracts actually closed; only the last close order was logged.
- Fix: each close returns a typed `ClosedPosition` containing its actual contract count and order. Every closed position is logged with that count. Exit happens before daily entry checks, so risk controls cannot block exposure reduction.

#### M3 — Reconciliation API-load concern — NOT AN ISSUE

- Location: `_reconcile_stop_outs`, now lines 280–314.
- Finding: the registry lookup occurs first and returns at once when no position is registered. `has_open_position` and `fetch_last_price` are not called in that case.

#### MAJOR — Live signals may use the still-open candle — FIXED

- Location: `fetch_signal`, lines 345–367; the same issue exists in `spot/trader.py` lines 203–225.
- Problem: OKX/ccxt commonly includes the current, incomplete OHLCV bar. The bot polls every 60 seconds on a 15-minute timeframe and does not remove that bar. The backtest uses completed historical bars, so the claim that it replays the exact live decision is not reliable; intrabar crosses can appear and disappear before candle close.
- Fix: `core/market_data.py` now fetches one extra candle, compares the latest candle end with OKX server time, drops it while it is still open, and fails closed unless `OHLCV_LIMIT` completed candles remain. Spot and futures use the same helper. Boundary and insufficient-history tests cover the behavior.

### `scripts/verify_sl_attachment.py`

#### CRITICAL — Futures exchange-side stop remains unverified — OPEN / RELEASE BLOCKER

- Location: `main`, lines 28–65.
- Problem: source inspection cannot prove that the exact ccxt version, OKX account mode, and sandbox API attach the conditional stop. The script changes external account state and was not run in this review because no demo credentials were provided.
- Required action: on the VM, with `SANDBOX_MODE=true`, run `.venv/bin/python scripts/verify_sl_attachment.py`; retain the PASS log, verify the stop in the OKX UI/API, and manually confirm the cleanup left no position. Do not set `SANDBOX_MODE=false` before this passes.

#### MAJOR — Verification can match an old stop and cleanup can close pre-existing demo positions — OPEN

- Location: pending-algo check at line 46 and cleanup at line 62.
- Problem: the script accepts any pending stop for the instrument and immediately queries after entry. It can false-pass on an old order or false-fail because of API propagation delay. `close_all_positions` can close positions that existed before the test.
- Concrete fix: refuse to start unless the symbol has no position or algo orders; capture the created order time and expected trigger; poll for a bounded timeout; require matching `instId`, side, and trigger price; close only the contracts opened by the script; cancel the matched algo in `finally`.

### `backtest/engine.py`

#### MAJOR — Maximum drawdown ignored open-position peaks — FIXED

- Location: mark-to-market block, now line 154.
- Problem: `peak` only changed when a trade closed. A position could double and fall back to entry while the reported drawdown remained zero.
- Fix: update peak from fee-adjusted mark-to-market equity on every bar. Final liquidation is also included in drawdown.

#### MINOR — Fee total used an incorrect decomposition — FIXED

- Location: fee accounting, now lines 111 and 145.
- Problem: total return was internally consistent, but the displayed fee total valued the entry fee at the exit price.
- Fix: record entry fee at entry equity and exit fee at fee-adjusted exit notional. Tests assert the exact amount.

#### MINOR — Stop fill at trigger is optimistic in a gap — ACCEPTED CAVEAT

- Location: lines 129–131.
- Finding: the engine fills at the stop price whenever the low crosses it. Without open prices or tick data, a gap through the stop will understate loss. Keep the caveat in every report; a concrete future fix is to include candle opens and fill at `min(open, stop)` for long positions before applying optional slippage.

#### MINOR — Break-even is counted as non-winning — CONFIRMED

- Location: `win_rate`, line 43.
- Finding: M3/minor note is correct by convention. No code change is needed; add a separate break-even count only if reports need it.

### `backtest/run.py`

#### M4 — GitHub f-string is fixed; VM parity not independently verifiable

- Location: line 98.
- Finding: commit `8f31c7d` contains a valid f-string. This review has no SSH access to compare the VM file.
- Concrete fix: deploy only from a clean Git checkout, record `git rev-parse HEAD` in deployment logs, and replace any manual VM patch by pulling the reviewed commit.

### `core/config.py`, `.env.example`, `README.md`

#### MINOR — Tuned slope default diverged from production — FIXED

- Location: `core/config.py` line 144, `.env.example` line 45, `README.md` line 127.
- Fix: default and documentation now use `0.0003`, with an `.env.example` comment that it is a 15-minute backtest starting point rather than a universal optimum.

### `core/strategy.py`

#### MINOR — Flat-market RSI returns 100 instead of neutral 50 — FIXED

- Location: `calculate_rsi`, lines 29–53.
- Problem: when both average gain and average loss are zero, the `avg_loss == 0` branch returns 100. This is mathematically misleading, although a completely flat series does not create a crossover.
- Fix: return `50.0` when average gain and loss are both zero, while retaining `100.0` for gain-only periods and `0.0` for loss-only periods. Tests cover all three cases.

## Phase 3 follow-up — completed

The shared **closed-candle filter** described under `futures/trader.py` is implemented for both spot and futures signal paths. Live decisions and backtests now use completed candles rather than transient intrabar values.

## Verification performed

- `python -m unittest discover -s tests -v`: **51 passed**.
- `git diff --check`: passed.
- No OKX order was placed and no VM state was changed during this review.
