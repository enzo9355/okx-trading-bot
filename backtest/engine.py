from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.strategy import filtered_ma_cross_signal

# Default taker fee per side. OKX spot taker is 0.10% at the base tier; adjust
# via the CLI flag if your tier differs.
DEFAULT_FEE_RATE = 0.001


@dataclass
class BacktestTrade:
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    exit_reason: str  # "signal" | "stop_loss" | "end_of_data"

    @property
    def gross_return_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price


@dataclass
class BacktestResult:
    n_bars: int = 0
    trades: list[BacktestTrade] = field(default_factory=list)
    total_return_pct: float = 0.0
    buy_hold_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    total_fees_pct: float = 0.0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.gross_return_pct > 0)
        return wins / len(self.trades)

    def summary(self) -> str:
        lines = [
            f"bars={self.n_bars} trades={self.n_trades} win_rate={self.win_rate:.1%}",
            f"strategy_return={self.total_return_pct:+.2%}  buy_hold={self.buy_hold_return_pct:+.2%}",
            f"max_drawdown={self.max_drawdown_pct:.2%}  fees_paid={self.total_fees_pct:.2%} of equity",
        ]
        stop_outs = sum(1 for t in self.trades if t.exit_reason == "stop_loss")
        if stop_outs:
            lines.append(f"stop_loss_exits={stop_outs}")
        return "\n".join(lines)


def run_backtest(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    window: int = 50,
    fee_rate: float = DEFAULT_FEE_RATE,
    stop_loss_pct: float = 0.0,
    **signal_params: Any,
) -> BacktestResult:
    """Replay the LIVE signal function bar-by-bar over historical candles.

    Deliberate design choices, so the result answers "does this signal have
    edge after fees", not "what exactly would my account have done":

    - Uses the same `filtered_ma_cross_signal` the live bot calls, fed a rolling
      `window`-bar slice — identical to the live OHLCV_LIMIT view of the market.
    - Long-only, one position at a time, full allocation per trade. The live bot
      stacks 5% entries; modelling that precisely entangles sizing with signal
      quality. Here every signal is expressed at equal weight so win rate and
      per-trade return measure the SIGNAL.
    - Fills at the bar close where the signal fired (the live bot also acts on
      the just-closed candle), taker fee charged on both sides.
    - Optional intrabar stop-loss: if a bar's low touches the stop, the fill is
      AT the stop price — slightly optimistic in gaps, fine at crypto liquidity.
    - Buy & hold over the same span is reported as the honest benchmark.
    """
    n = len(closes)
    if len(highs) != n or len(lows) != n:
        raise ValueError("High, low, and close series must have the same length.")
    if fee_rate < 0 or fee_rate >= 1:
        raise ValueError("fee_rate must be at least 0 and less than 1.")
    if stop_loss_pct < 0 or stop_loss_pct >= 1:
        raise ValueError("stop_loss_pct must be at least 0 and less than 1.")
    result = BacktestResult(n_bars=n)
    if n <= window:
        return result
    if n > 1 and closes[0] > 0:
        result.buy_hold_return_pct = (closes[-1] - closes[0]) / closes[0]

    equity = 1.0
    peak = 1.0
    fees = 0.0
    entry_price: float | None = None
    entry_index = 0

    def close_trade(index: int, exit_price: float, reason: str) -> None:
        nonlocal equity, peak, fees, entry_price
        assert entry_price is not None
        gross = exit_price / entry_price
        net = gross * (1 - fee_rate) ** 2  # fee on entry and exit
        # Entry fee is recorded when the position opens. The exit fee is based
        # on the marked exit value after the entry fee reduced the position.
        fees += equity * (1 - fee_rate) * gross * fee_rate
        equity *= net
        peak = max(peak, equity)
        result.trades.append(
            BacktestTrade(
                entry_index=entry_index,
                exit_index=index,
                entry_price=entry_price,
                exit_price=exit_price,
                exit_reason=reason,
            )
        )
        entry_price = None

    for i in range(window, n):
        lo = i - window
        # Stop-loss check first (intrabar), before this bar's signal.
        if entry_price is not None and stop_loss_pct > 0:
            stop_price = entry_price * (1 - stop_loss_pct)
            if lows[i] <= stop_price:
                close_trade(i, stop_price, "stop_loss")
                result.max_drawdown_pct = max(result.max_drawdown_pct, (peak - equity) / peak)
                continue

        signal = filtered_ma_cross_signal(
            highs[lo : i + 1],
            lows[lo : i + 1],
            closes[lo : i + 1],
            **signal_params,
        )

        if signal.signal == "buy" and entry_price is None:
            entry_price = closes[i]
            entry_index = i
            fees += equity * fee_rate
        elif signal.signal == "sell" and entry_price is not None:
            close_trade(i, closes[i], "signal")

        if entry_price is not None:
            # Mark the value after the already-paid entry fee. Update the peak
            # while a trade is open; otherwise a rise followed by a pullback
            # would be omitted from maximum drawdown.
            mark = equity * (1 - fee_rate) * (closes[i] / entry_price)
            peak = max(peak, mark)
            result.max_drawdown_pct = max(result.max_drawdown_pct, (peak - mark) / peak)
        else:
            result.max_drawdown_pct = max(result.max_drawdown_pct, (peak - equity) / peak)

    if entry_price is not None:
        close_trade(n - 1, closes[-1], "end_of_data")
        result.max_drawdown_pct = max(result.max_drawdown_pct, (peak - equity) / peak)

    result.total_return_pct = equity - 1.0
    result.total_fees_pct = fees
    return result
