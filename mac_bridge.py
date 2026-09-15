import os
import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)

MAC_BRIDGE_URL = os.environ.get("MAC_BRIDGE_URL", "")
MAC_BRIDGE_SECRET = os.environ.get("MAC_BRIDGE_SECRET", "")


class MacCashflowProxy:
    """Looks enough like a yfinance.Ticker for the existing DCF code
    (which only ever touches .cashflow) to keep working unchanged, but is
    backed by data fetched from the Mac bridge instead of a live
    yfinance call from Render (which Yahoo blocks)."""

    def __init__(self, cashflow_dict):
        self._cashflow_dict = cashflow_dict or {}

    @property
    def cashflow(self):
        if not self._cashflow_dict:
            return pd.DataFrame()
        df = pd.DataFrame(self._cashflow_dict).T  # rows=line items, columns=dates
        try:
            df.columns = pd.to_datetime(df.columns)
        except Exception:
            pass  # keep columns as plain date strings — get_fcf_series handles both
        return df


def get_fundamentals_from_mac(symbol):
    """Ask the Mac bridge (running yfinance from a home IP) for
    fundamentals data. Returns (info_dict, cashflow_dict, resolved_symbol)
    or (None, None, None) if the bridge isn't configured or unreachable —
    callers should just skip the fundamentals section gracefully, same
    as when yfinance itself has no data.
    """
    if not MAC_BRIDGE_URL or not MAC_BRIDGE_SECRET:
        return None, None, None
    try:
        resp = requests.get(
            f"{MAC_BRIDGE_URL.rstrip('/')}/fundamentals/{MAC_BRIDGE_SECRET}",
            params={"symbol": symbol},
            timeout=20,
        )
        data = resp.json()
    except Exception:
        logger.exception("Mac bridge call failed for %s", symbol)
        return None, None, None

    if not data.get("ok"):
        return None, None, None
    return data.get("info", {}), data.get("cashflow", {}), data.get("symbol")
