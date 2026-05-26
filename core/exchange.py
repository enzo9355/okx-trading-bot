from __future__ import annotations

import ccxt

from core.config import Settings


def create_okx_exchange(settings: Settings, default_type: str) -> ccxt.okx:
    if default_type not in {"spot", "swap"}:
        raise ValueError("default_type must be either 'spot' or 'swap'.")

    exchange = ccxt.okx(
        {
            "apiKey": settings.api_key,
            "secret": settings.secret_key,
            "password": settings.passphrase,
            "enableRateLimit": True,
            "timeout": 30_000,
            "options": {
                "defaultType": default_type,
                "adjustForTimeDifference": True,
            },
        }
    )
    exchange.set_sandbox_mode(settings.sandbox_mode)
    return exchange

