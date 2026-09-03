import logging

import yfinance as yf

logger = logging.getLogger(__name__)

# Known symbol aliases -> yfinance ticker candidates to try, in order.
SYMBOL_ALIASES = {
    "XAUUSD": ["XAUUSD=X", "GC=F"],
    "GOLD": ["XAUUSD=X", "GC=F"],
    "XAGUSD": ["SI=F", "XAGUSD=X"],
    "SILVER": ["SI=F", "XAGUSD=X"],
    "US100": ["^NDX", "NQ=F"],
    "NAS100": ["^NDX", "NQ=F"],
    "US30": ["^DJI", "YM=F"],
    "DJ30": ["^DJI", "YM=F"],
    "US500": ["^GSPC", "ES=F"],
    "SPX500": ["^GSPC", "ES=F"],
    "SPX": ["^GSPC", "ES=F"],
    "BTCUSD": ["BTC-USD"],
    "ETHUSD": ["ETH-USD"],
    "USOIL": ["CL=F"],
    "WTI": ["CL=F"],
    "UKOIL": ["BZ=F"],
    "BRENT": ["BZ=F"],
}

# Major currency codes, so a typed pair like "EURUSD" or "GBPJPY" can be
# turned into the yfinance format "EURUSD=X" automatically.
CURRENCY_CODES = {
    "USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "CNH", "SGD", "HKD", "THB",
}


def resolve_forex_symbol(raw_symbol):
    """Return a list of yfinance ticker candidates for a symbol the user
    typed (e.g. 'XAUUSD', 'US100', 'EURUSD'), or [] if it isn't a
    recognized forex/index/commodity symbol."""
    symbol = raw_symbol.strip().upper()
    if symbol in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[symbol]
    if len(symbol) == 6 and symbol[:3] in CURRENCY_CODES and symbol[3:] in CURRENCY_CODES:
        return [f"{symbol}=X"]
    return []


def _get_4h_history(ticker_candidates, period="1mo"):
    """Try each candidate ticker. Yahoo Finance has no native 4H interval,
    so real 4H candles are built here by fetching 1H candles and
    resampling them (Open=first, High=max, Low=min, Close=last).
    Note: bins align to UTC clock hours, which may sit slightly off from
    your broker/MT4's own 4H candle boundaries — close enough for the
    bias reading, but worth knowing if you're eyeballing it against a chart.
    """
    for symbol in ticker_candidates:
        try:
            hourly = yf.Ticker(symbol).history(period=period, interval="1h")
        except Exception:
            continue
        if hourly is None or hourly.empty:
            continue
        try:
            four_hour = (
                hourly.resample("4h")
                .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
                .dropna()
            )
        except Exception:
            continue
        if four_hour.empty:
            continue
        return symbol, four_hour
    return None, None


def compute_4h_bias(ticker_candidates, lookback_candles=30):
    """Take the most recent swing (lowest low to highest high) on the 4H
    chart, draw a 0/50/100 Fibonacci range across it, and read the bias
    the same way the user's system does: upper half = 'expensive' (SELL
    bias), lower half = 'cheap' (BUY bias).
    """
    symbol, hist = _get_4h_history(ticker_candidates)
    if hist is None:
        return None

    window = hist.tail(lookback_candles)
    if window.empty:
        return None

    swing_low = float(window["Low"].min())
    swing_high = float(window["High"].max())
    if swing_high <= swing_low:
        return None

    current_price = float(hist["Close"].iloc[-1])
    fib_50 = (swing_low + swing_high) / 2
    bias = "SELL (โซนแพง)" if current_price > fib_50 else "BUY (โซนถูก)"

    return {
        "symbol": symbol,
        "price": current_price,
        "swing_low": swing_low,
        "swing_high": swing_high,
        "fib_50": fib_50,
        "bias": bias,
    }


CHECKLIST = (
    "✅ <b>เช็คลิสต์ที่เหลือ (ดูด้วยตาตามระบบ):</b>\n"
    "1️⃣ 1H หาแนวรับ/แนวต้าน (Resistance, Support, RBS, SBR, OCL, QM, OB)\n"
    "2️⃣ กรองโซนใหญ่→เล็ก 1H &gt; M15 &gt; M5\n"
    "3️⃣ เช็คว่ามี IDM ก่อนถึงโซนหรือยัง\n"
    "4️⃣ ครบทุกเงื่อนไข = setup A+\n"
    "5️⃣ TP 3 จุด (800-1000 จุด / swing BSL-SSL / price movement TF ใหญ่สุด) — RR ≥ 1:6, SL 300 จุด\n"
    "6️⃣ Confirm เข้าจริงเมื่อ M1/M5 เกิดสัญญาณ Turtle Soup (แท่งต่อแท่งเท่านั้น)"
)


def _twelvedata_symbol(display_symbol):
    """Map our bot's display symbol to Twelve Data's format ('XAU/USD',
    'EUR/USD', ...). Returns None for symbols we don't have a mapping
    for (caller keeps using the yfinance price in that case)."""
    s = display_symbol.strip().upper()
    if s in ("XAUUSD", "GOLD"):
        return "XAU/USD"
    if s in ("XAGUSD", "SILVER"):
        return "XAG/USD"
    if len(s) == 6 and s.isalpha():
        return f"{s[:3]}/{s[3:]}"
    return None


def _build_report(display_symbol, title):
    candidates = resolve_forex_symbol(display_symbol)
    if not candidates:
        return None  # not a recognized forex/index/commodity symbol

    try:
        data = compute_4h_bias(candidates)
    except Exception:
        logger.exception("compute_4h_bias failed for %s", display_symbol)
        data = None

    if not data:
        return (
            f"⚠️ ดึงข้อมูลราคา {display_symbol} ไม่ได้ตอนนี้ "
            "ลองเปิดชาร์ตเช็คด้วยตัวเองก่อนนะครับ"
        )

    # The swing/Fibonacci structure stays on yfinance (delay doesn't matter
    # for that). Only the live price — and the bias comparison against it
    # — gets swapped for a real-time Twelve Data quote when configured,
    # since that's what "matches TradingView" actually needs.
    td_symbol = _twelvedata_symbol(display_symbol)
    if td_symbol:
        try:
            from price_feed import get_realtime_price
            fresh_price = get_realtime_price(td_symbol)
        except Exception:
            fresh_price = None
        if fresh_price:
            data["price"] = fresh_price
            data["bias"] = "SELL (โซนแพง)" if fresh_price > data["fib_50"] else "BUY (โซนถูก)"

    lines = [
        f"<b>{title} — {display_symbol}</b>",
        f"ราคาปัจจุบัน: {data['price']:,.2f}",
        "",
        f"📊 <b>Bias จาก Fibo 4H:</b> {data['bias']}",
        f"   0% (swing low): {data['swing_low']:,.2f}",
        f"   50%: {data['fib_50']:,.2f}",
        f"   100% (swing high): {data['swing_high']:,.2f}",
        "",
        CHECKLIST,
    ]
    return "\n".join(lines)


def build_forex_alert_message(display_symbol="XAUUSD"):
    """Scheduled alert (used by the /forex-check cron route)."""
    report = _build_report(display_symbol, "🔔 เช็คระบบ ize (ตามเวลา)") or (
        f"⚠️ ดึงข้อมูลราคา {display_symbol} ไม่ได้ตอนนี้"
    )
    if display_symbol.strip().upper() in ("XAUUSD", "GOLD"):
        try:
            from qt import build_qt_report
            report = f"{report}\n\n{build_qt_report()}"
        except Exception:
            logger.exception("build_qt_report failed in scheduled alert")
    return report


def build_symbol_report(raw_symbol):
    """On-demand report when the user types a symbol like XAUUSD or US100
    directly into the chat. Returns None if the symbol isn't recognized
    as forex/index/commodity, so the caller can fall back to stock lookup."""
    return _build_report(raw_symbol.strip().upper(), "📊 เช็คราคาสด")
