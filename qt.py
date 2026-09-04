import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")
BKK_TZ = ZoneInfo("Asia/Bangkok")

GOLD_TICKER_CANDIDATES = ["XAUUSD=X", "GC=F"]

BOX_MINUTES_BEFORE_TDO = 90  # the fixed pre-TDO box duration (9:30-11:00 style)

# The boundaries used to build each day's box sequence: 4 fixed Thai
# clock times, plus TDO (dynamic — see get_tdo_bias). Chronological
# order within a day: 8:00, TDO(~11:00/12:00), 12:30, 14:00, 18:30, 20:00.
FIXED_TIMES_BKK = [("0800", 8, 0), ("1230", 12, 30), ("1400", 14, 0), ("1830", 18, 30), ("2000", 20, 0)]


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


def _tdo_bkk_for(ny_date):
    """TDO (NY midnight) for a given NY calendar date, as a Bangkok-aware
    datetime. Anchored to real NY time so it keeps working across US
    daylight saving changes."""
    tdo_ny = datetime(ny_date.year, ny_date.month, ny_date.day, 0, 0, tzinfo=NY_TZ)
    return tdo_ny.astimezone(BKK_TZ)


def get_tdo_bias():
    """Bias = BUY if the current price is below today's TDO (True Day
    Open, = NY midnight open), SELL if above. Independent of the
    alert-interval box/model below."""
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


def _day_boundaries_bkk(ny_date):
    """(label, datetime_bkk) pairs for one NY calendar date's boundaries,
    chronological: 8:00, TDO, 12:30, 14:00, 18:30, 20:00 (Bangkok time)."""
    tdo_bkk = _tdo_bkk_for(ny_date)
    bkk_date = tdo_bkk.date()

    def t(h, m):
        return datetime(bkk_date.year, bkk_date.month, bkk_date.day, h, m, tzinfo=BKK_TZ)

    points = [("0800", t(8, 0)), ("TDO", tdo_bkk)]
    points += [(label, t(h, m)) for label, h, m in FIXED_TIMES_BKK if label != "0800"]
    points.sort(key=lambda p: p[1])
    return points


def _all_boundaries_bkk(now_bkk):
    now_ny = now_bkk.astimezone(NY_TZ)
    points = []
    for offset in (-2, -1, 0, 1, 2):
        points.extend(_day_boundaries_bkk((now_ny.date() + timedelta(days=offset))))
    return sorted(set(points), key=lambda p: p[1])


def get_alert_box_model(hist):
    """The box for each check is the interval that had ALREADY completed
    one step before the current check — not the interval ending at the
    check itself (comparing a candle to the box it's part of is
    meaningless). E.g. checking at 14:00 compares against the TDO-12:30
    box, not the 12:30-14:00 box. The one exception is the very first
    check after TDO (at 12:30), which uses the fixed 90-minute pre-TDO
    window instead of the (much wider) 8:00-TDO gap, matching the chart.
    AMDX/XAMD is read off the latest candle's position relative to that
    reference box.
    """
    now_bkk = datetime.now(BKK_TZ)
    boundaries = _all_boundaries_bkk(now_bkk)

    idx = None
    for i, (_, t) in enumerate(boundaries):
        if t <= now_bkk:
            idx = i
    if idx is None or idx < 2:
        return None

    box_start_label, box_start = boundaries[idx - 2]
    box_end_label, box_end = boundaries[idx - 1]

    if box_end_label == "TDO":
        box_start = box_end - timedelta(minutes=BOX_MINUTES_BEFORE_TDO)

    hist_bkk = hist.tz_convert(BKK_TZ)
    box_window = hist_bkk[(hist_bkk.index >= box_start) & (hist_bkk.index < box_end)]
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
        "box_start_bkk": box_start,
        "box_end_bkk": box_end,
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
            f"\nกรอบอ้างอิง ({start_str}-{end_str} น.): "
            f"{box_data['box_low']:,.2f} - {box_data['box_high']:,.2f}"
        )
        if box_data["model"] == "AMDX":
            lines.append("🟦 โมเดล: <b>AMDX</b> — ตอนนี้ราคายังอยู่ในกรอบ")
        else:
            lines.append(f"🟥 โมเดล: <b>XAMD</b> — ตอนนี้ราคาทะลุกรอบไปทาง{box_data['breakout_direction']}แล้ว")
    else:
        lines.append("\nกรอบอ้างอิง: ดึงข้อมูลไม่พอสำหรับช่วงเวลานี้")

    return "\n".join(lines)
