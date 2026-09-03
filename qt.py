import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")
BKK_TZ = ZoneInfo("Asia/Bangkok")

GOLD_TICKER_CANDIDATES = ["XAUUSD=X", "GC=F"]

BOX_MINUTES_BEFORE_TDO = 90  # the "blue box" = 90 min immediately before TDO


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


def get_tdo_and_box():
    """TDO (True Day Open) = the open price at New York midnight, computed
    from actual NY time (not a fixed Thai clock hour) so it keeps working
    across US daylight saving changes — it lands on 11:00 Thai time
    during EDT (roughly Mar-Nov) and 12:00 during EST.

    The 'box' the user reads on the chart is the high/low of the 90
    minutes immediately before TDO (22:30-00:00 NY time). AMDX/XAMD is
    read off where the LATEST candle sits relative to that box at the
    moment of checking — not whether price ever poked outside it earlier
    in the session. A mid-session breakout that has since reverted back
    inside the box does not count as XAMD; only the current position does.
    This is meant to be read right at each session-boundary check time
    (the same 8:00/12:30/14:00/18:30/20:00 alert times), which is exactly
    when this function gets called.
    """
    symbol, hist = _get_gold_m1()
    if hist is None:
        return None

    now_ny = datetime.now(NY_TZ)
    tdo_time = now_ny.replace(hour=0, minute=0, second=0, microsecond=0)
    box_start = tdo_time - timedelta(minutes=BOX_MINUTES_BEFORE_TDO)

    box_window = hist[(hist.index >= box_start) & (hist.index < tdo_time)]
    session_window = hist[hist.index >= tdo_time]

    if box_window.empty:
        return None

    box_high = float(box_window["High"].max())
    box_low = float(box_window["Low"].min())

    tdo_price = float(session_window["Open"].iloc[0]) if not session_window.empty else None
    current_price = float(hist["Close"].iloc[-1])

    model = None
    breakout_direction = None
    if not session_window.empty:
        if current_price > box_high:
            model = "XAMD"
            breakout_direction = "ขึ้น"
        elif current_price < box_low:
            model = "XAMD"
            breakout_direction = "ลง"
        else:
            model = "AMDX"

    bias = None
    if tdo_price is not None:
        bias = "BUY (ราคาต่ำกว่า TDO)" if current_price < tdo_price else "SELL (ราคาสูงกว่า TDO)"

    return {
        "symbol": symbol,
        "current_price": current_price,
        "tdo_price": tdo_price,
        "tdo_time_ny": tdo_time,
        "box_high": box_high,
        "box_low": box_low,
        "bias": bias,
        "model": model,
        "breakout_direction": breakout_direction,
    }


def build_qt_report():
    try:
        data = get_tdo_and_box()
    except Exception:
        logger.exception("get_tdo_and_box failed")
        data = None

    if not data:
        return (
            "⚠️ ดึงข้อมูล TDO/กรอบราคาไม่ได้ตอนนี้ครับ "
            "(อาจยังไม่ถึงช่วงที่มีข้อมูลของกรอบก่อนหน้า หรือ Yahoo มีปัญหาชั่วคราว)"
        )

    tdo_time_bkk = data["tdo_time_ny"].astimezone(BKK_TZ)
    lines = [
        "🕐 <b>Quarterly Theory — XAUUSD</b>",
        f"ราคาปัจจุบัน: {data['current_price']:,.2f}",
    ]
    if data["tdo_price"] is not None:
        lines.append(f"TDO ({tdo_time_bkk.strftime('%H:%M')} น. ไทย): {data['tdo_price']:,.2f}")
    else:
        lines.append("TDO: ยังไม่เริ่ม session ใหม่วันนี้")

    if data["bias"]:
        lines.append(f"📊 Bias วันนี้: {data['bias']}")

    lines.append(f"กรอบก่อนหน้า (box): {data['box_low']:,.2f} - {data['box_high']:,.2f}")

    if data["model"] == "AMDX":
        lines.append("🟦 โมเดล: <b>AMDX</b> — ตอนนี้ราคายังอยู่ในกรอบก่อนหน้า")
    elif data["model"] == "XAMD":
        lines.append(f"🟥 โมเดล: <b>XAMD</b> — ตอนนี้ราคาทะลุกรอบไปทาง{data['breakout_direction']}แล้ว")
    else:
        lines.append("โมเดล: รอถึง TDO ก่อนถึงจะจำแนกได้")

    return "\n".join(lines)
