import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")
BKK_TZ = ZoneInfo("Asia/Bangkok")

GOLD_TICKER_CANDIDATES = ["XAUUSD=X", "GC=F"]

# The 5 daily check times (Thai wall-clock, fixed — same as the existing
# forex-check cron schedule). The box for a given check = the price range
# from the PREVIOUS one of these times up to this one; AMDX/XAMD is read
# off wherever the latest candle sits relative to that box at check time.
ALERT_TIMES_BKK = [(8, 0), (12, 30), (14, 0), (18, 30), (20, 0)]


def _get_gold_m1():
    """Fetch recent 1-minute gold candles — real-time via Twelve Data when
    an API key is configured (matches what you see on TradingView),
    falling back to Yahoo Finance (which can run a few minutes behind)
    otherwise. AMDX/XAMD hinges on exactly where the latest candle sits,
    so this is the one place real-time data matters most.
    """
    try:
        from price_feed import get_realtime_1m_series
        candles = get_realtime_1m_series("XAU/USD")
    except Exception:
        candles = None

    if candles:
        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize("UTC")
        df = df.set_index("datetime").rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"}
        )
        return "XAU/USD (Twelve Data)", df.tz_convert(NY_TZ)

    for symbol in GOLD_TICKER_CANDIDATES:
        try:
            hist = yf.Ticker(symbol).history(period="2d", interval="1m")
        except Exception:
            continue
        if hist is not None and not hist.empty:
            if hist.index.tz is None:
                hist.index = hist.index.tz_localize("UTC")
            return symbol, hist.tz_convert(NY_TZ)
    return None, None


def get_tdo_bias():
    """TDO (True Day Open) = the open price at New York midnight, computed
    from actual NY time (not a fixed Thai clock hour) so it keeps working
    across US daylight saving changes — it lands on 11:00 Thai time
    during EDT (roughly Mar-Nov) and 12:00 during EST. Bias = BUY if the
    current price is below TDO, SELL if above. This is independent of
    the alert-interval box/model below.
    """
    symbol, hist = _get_gold_m1()
    if hist is None:
        return None

    now_ny = datetime.now(NY_TZ)
    tdo_time = now_ny.replace(hour=0, minute=0, second=0, microsecond=0)
    today_session = hist[hist.index >= tdo_time]

    if today_session.empty:
        return {"symbol": symbol, "current_price": float(hist["Close"].iloc[-1]),
                "tdo_price": None, "tdo_time_ny": tdo_time, "bias": None}

    tdo_price = float(today_session["Open"].iloc[0])
    current_price = float(hist["Close"].iloc[-1])
    bias = "BUY (ราคาต่ำกว่า TDO)" if current_price < tdo_price else "SELL (ราคาสูงกว่า TDO)"

    return {
        "symbol": symbol,
        "current_price": current_price,
        "tdo_price": tdo_price,
        "tdo_time_ny": tdo_time,
        "bias": bias,
    }


def _alert_boundaries_bkk(now_bkk):
    """All 5-daily-alert-time instants spanning the day before/after `now`,
    sorted chronologically, as tz-aware Bangkok datetimes."""
    candidates = []
    for day_offset in (-1, 0, 1):
        day = (now_bkk + timedelta(days=day_offset)).date()
        for h, m in ALERT_TIMES_BKK:
            candidates.append(datetime(day.year, day.month, day.day, h, m, tzinfo=BKK_TZ))
    candidates.sort()
    return candidates


def get_alert_box_model(hist):
    """The box resets fresh at every one of the 5 daily alert times: box =
    high/low of the interval from the PREVIOUS alert time to the CURRENT
    one. AMDX/XAMD is read off the latest available candle's position
    relative to that box, checked right at (or just before) each alert
    time — not any breakout earlier in the interval that may have since
    reverted.
    """
    now_bkk = datetime.now(BKK_TZ)
    boundaries = _alert_boundaries_bkk(now_bkk)

    current_boundary = None
    previous_boundary = None
    for i, b in enumerate(boundaries):
        if b <= now_bkk:
            current_boundary = b
            previous_boundary = boundaries[i - 1] if i > 0 else None

    if current_boundary is None or previous_boundary is None:
        return None

    hist_bkk = hist.tz_convert(BKK_TZ)
    box_window = hist_bkk[(hist_bkk.index >= previous_boundary) & (hist_bkk.index < current_boundary)]
    if box_window.empty:
        return None

    box_high = float(box_window["High"].max())
    box_low = float(box_window["Low"].min())
    current_price = float(hist_bkk["Close"].iloc[-1])

    if current_price > box_high:
        model, direction = "XAMD", "ขึ้น"
    elif current_price < box_low:
        model, direction = "XAMD", "ลง"
    else:
        model, direction = "AMDX", None

    return {
        "box_high": box_high,
        "box_low": box_low,
        "box_start_bkk": previous_boundary,
        "box_end_bkk": current_boundary,
        "model": model,
        "breakout_direction": direction,
    }


def build_qt_report():
    try:
        symbol, hist = _get_gold_m1()
    except Exception:
        logger.exception("_get_gold_m1 failed")
        symbol, hist = None, None

    if hist is None:
        return "⚠️ ดึงข้อมูลราคาทองไม่ได้ตอนนี้ครับ (Twelve Data/Yahoo อาจมีปัญหาชั่วคราว)"

    try:
        tdo_data = get_tdo_bias()
    except Exception:
        logger.exception("get_tdo_bias failed")
        tdo_data = None

    try:
        box_data = get_alert_box_model(hist)
    except Exception:
        logger.exception("get_alert_box_model failed")
        box_data = None

    current_price = float(hist["Close"].iloc[-1])
    lines = [
        "🕐 <b>Quarterly Theory — XAUUSD</b>",
        f"ราคาปัจจุบัน: {current_price:,.2f}",
    ]

    if tdo_data and tdo_data.get("tdo_price") is not None:
        tdo_time_bkk = tdo_data["tdo_time_ny"].astimezone(BKK_TZ)
        lines.append(f"TDO ({tdo_time_bkk.strftime('%H:%M')} น. ไทย): {tdo_data['tdo_price']:,.2f}")
        lines.append(f"📊 Bias วันนี้: {tdo_data['bias']}")
    else:
        lines.append("TDO: ยังไม่เริ่ม session ใหม่วันนี้")

    if box_data:
        start_str = box_data["box_start_bkk"].strftime("%H:%M")
        end_str = box_data["box_end_bkk"].strftime("%H:%M")
        lines.append(
            f"\nกรอบรอบนี้ ({start_str}-{end_str} น.): "
            f"{box_data['box_low']:,.2f} - {box_data['box_high']:,.2f}"
        )
        if box_data["model"] == "AMDX":
            lines.append("🟦 โมเดล: <b>AMDX</b> — ตอนนี้ราคายังอยู่ในกรอบรอบนี้")
        else:
            lines.append(f"🟥 โมเดล: <b>XAMD</b> — ตอนนี้ราคาทะลุกรอบไปทาง{box_data['breakout_direction']}แล้ว")
    else:
        lines.append("\nกรอบรอบนี้: ดึงข้อมูลไม่พอสำหรับช่วงเวลานี้")

    return "\n".join(lines)
