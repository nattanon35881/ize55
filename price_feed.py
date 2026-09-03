import os
import logging

import requests

logger = logging.getLogger(__name__)

TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")
TWELVEDATA_BASE = "https://api.twelvedata.com"


def get_realtime_price(symbol):
    """Real-time quote via Twelve Data (symbol format e.g. 'XAU/USD',
    'EUR/USD'). Returns a float, or None if no API key is configured or
    the call fails — callers should fall back to their existing
    (potentially delayed) source in that case, never hard-fail on this."""
    if not TWELVEDATA_API_KEY:
        return None
    try:
        resp = requests.get(
            f"{TWELVEDATA_BASE}/price",
            params={"symbol": symbol, "apikey": TWELVEDATA_API_KEY},
            timeout=10,
        )
        data = resp.json()
        return float(data["price"])
    except Exception:
        logger.exception("Twelve Data price fetch failed for %s", symbol)
        return None


def get_realtime_1m_series(symbol, outputsize=300):
    """Real-time 1-minute OHLC candles via Twelve Data, oldest first, as a
    list of dicts with keys: datetime (UTC string 'YYYY-MM-DD HH:MM:SS'),
    open, high, low, close. Returns None if unavailable/failed."""
    if not TWELVEDATA_API_KEY:
        return None
    try:
        resp = requests.get(
            f"{TWELVEDATA_BASE}/time_series",
            params={
                "symbol": symbol,
                "interval": "1min",
                "outputsize": outputsize,
                "timezone": "UTC",
                "apikey": TWELVEDATA_API_KEY,
            },
            timeout=15,
        )
        data = resp.json()
        values = data.get("values")
        if not values:
            return None
        candles = [
            {
                "datetime": v["datetime"],
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
            }
            for v in values
        ]
        candles.reverse()  # Twelve Data returns newest first
        return candles
    except Exception:
        logger.exception("Twelve Data time_series fetch failed for %s", symbol)
        return None
