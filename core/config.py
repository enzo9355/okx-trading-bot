from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _path_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw) if raw else default
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


@dataclass(frozen=True)
class Settings:
    api_key: str
    secret_key: str
    passphrase: str
    sandbox_mode: bool
    dry_run: bool
    spot_symbol: str
    futures_symbol: str
    quote_currency: str
    timeframe: str
    ohlcv_limit: int
    trade_interval_seconds: int
    max_position_pct: float
    daily_max_loss_pct: float
    margin_ratio_threshold: float
    futures_margin_mode: str
    futures_leverage: int
    futures_position_mode: str
    futures_stop_loss_pct: float
    futures_take_profit_pct: float
    state_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            api_key=os.getenv("API_KEY", "").strip(),
            secret_key=os.getenv("SECRET_KEY", "").strip(),
            passphrase=os.getenv("PASSPHRASE", "").strip(),
            sandbox_mode=_bool_env("SANDBOX_MODE", True),
            dry_run=_bool_env("DRY_RUN", False),
            spot_symbol=os.getenv("SPOT_SYMBOL", "BTC/USDT").strip(),
            futures_symbol=os.getenv("FUTURES_SYMBOL", "BTC/USDT:USDT").strip(),
            quote_currency=os.getenv("QUOTE_CURRENCY", "USDT").strip(),
            timeframe=os.getenv("TIMEFRAME", "1m").strip(),
            ohlcv_limit=_int_env("OHLCV_LIMIT", 50),
            trade_interval_seconds=_int_env("TRADE_INTERVAL_SECONDS", 60),
            max_position_pct=_float_env("RISK_MAX_POSITION_PCT", 0.05),
            daily_max_loss_pct=_float_env("RISK_DAILY_MAX_LOSS_PCT", 0.10),
            margin_ratio_threshold=_float_env("RISK_MARGIN_RATIO_THRESHOLD", 0.20),
            futures_margin_mode=os.getenv("FUTURES_MARGIN_MODE", "isolated").strip().lower(),
            futures_leverage=_int_env("FUTURES_LEVERAGE", 3),
            futures_position_mode=os.getenv("FUTURES_POSITION_MODE", "net").strip().lower(),
            futures_stop_loss_pct=_float_env("FUTURES_STOP_LOSS_PCT", 0.0075),
            futures_take_profit_pct=_float_env("FUTURES_TAKE_PROFIT_PCT", 0.015),
            state_file=_path_env("RISK_STATE_FILE", ROOT_DIR / "data" / "risk_state.json"),
        )
        settings.validate()
        return settings

    def with_overrides(
        self,
        *,
        dry_run: bool | None = None,
        trade_interval_seconds: int | None = None,
    ) -> "Settings":
        updates = {}
        if dry_run is not None:
            updates["dry_run"] = dry_run
        if trade_interval_seconds is not None:
            updates["trade_interval_seconds"] = trade_interval_seconds
        return replace(self, **updates)

    def require_credentials(self) -> None:
        missing = [
            name
            for name, value in {
                "API_KEY": self.api_key,
                "SECRET_KEY": self.secret_key,
                "PASSPHRASE": self.passphrase,
            }.items()
            if not value
        ]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing required OKX credentials in .env: {joined}")

    def validate(self) -> None:
        if not 0 < self.max_position_pct <= 1:
            raise ValueError("RISK_MAX_POSITION_PCT must be between 0 and 1.")
        if not 0 < self.daily_max_loss_pct <= 1:
            raise ValueError("RISK_DAILY_MAX_LOSS_PCT must be between 0 and 1.")
        if self.margin_ratio_threshold <= 0:
            raise ValueError("RISK_MARGIN_RATIO_THRESHOLD must be greater than 0.")
        if self.futures_margin_mode != "isolated":
            raise ValueError("This bot is configured to support isolated futures margin only.")
        if self.futures_leverage != 3:
            raise ValueError("FUTURES_LEVERAGE must remain fixed at 3.")
        if self.futures_position_mode not in {"net", "long_short"}:
            raise ValueError("FUTURES_POSITION_MODE must be either net or long_short.")
        if self.futures_stop_loss_pct <= 0:
            raise ValueError("FUTURES_STOP_LOSS_PCT must be greater than 0.")
        if self.futures_take_profit_pct <= 0:
            raise ValueError("FUTURES_TAKE_PROFIT_PCT must be greater than 0.")
        if self.ohlcv_limit < 21:
            raise ValueError("OHLCV_LIMIT must be at least 21 for MA5/MA20 crossover.")

