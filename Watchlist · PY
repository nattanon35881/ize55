import os
import logging

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

SHEET_WEBAPP_URL = os.environ.get("SHEET_WEBAPP_URL", "")


def _call_sheet(payload):
    if not SHEET_WEBAPP_URL:
        return None
    try:
        resp = requests.post(SHEET_WEBAPP_URL, json=payload, timeout=15)
        return resp.json()
    except Exception:
        logger.exception("Sheet webapp call failed: %s", payload)
        return None


def get_price(symbol):
    """Current price + % change for a stock symbol (tries the symbol as
    typed, then with a .BK suffix for Thai/SET stocks — same approach as
    the main stock lookup)."""
    candidates = [symbol]
    if "." not in symbol:
        candidates.append(f"{symbol}.BK")

    for candidate in candidates:
        try:
            info = yf.Ticker(candidate).info
        except Exception:
            continue
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price:
            prev_close = info.get("previousClose")
            change_pct = ((price - prev_close) / prev_close) if prev_close else None
            return {
                "symbol": candidate,
                "price": price,
                "change_pct": change_pct,
                "currency": info.get("currency", ""),
            }
    return None


# ---------- Watchlist ----------

def add_to_watchlist(symbol):
    result = _call_sheet({"action": "watch_add", "symbol": symbol})
    if not result or not result.get("ok"):
        return f"❌ เพิ่ม {symbol} เข้า watchlist ไม่สำเร็จครับ"
    if result.get("already_exists"):
        return f"{symbol} อยู่ใน watchlist อยู่แล้วครับ"
    return f"✅ เพิ่ม {symbol} เข้า watchlist แล้วครับ"


def remove_from_watchlist(symbol):
    result = _call_sheet({"action": "watch_remove", "symbol": symbol})
    if not result or not result.get("ok"):
        return f"❌ ลบ {symbol} ไม่สำเร็จครับ"
    if not result.get("removed"):
        return f"ไม่พบ {symbol} ใน watchlist ครับ"
    return f"🗑️ ลบ {symbol} ออกจาก watchlist แล้วครับ"


def get_watchlist_symbols():
    result = _call_sheet({"action": "watch_list"})
    if not result or not result.get("ok"):
        return None
    return result.get("symbols", [])


def build_watchlist_summary():
    symbols = get_watchlist_symbols()
    if symbols is None:
        return "❌ ดึง watchlist ไม่ได้ตอนนี้ครับ (เช็ค SHEET_WEBAPP_URL)"
    if not symbols:
        return 'ยังไม่มีหุ้นใน watchlist ครับ เพิ่มด้วย /watch SYMBOL เช่น /watch PTT'

    lines = ["📋 <b>สรุป Watchlist ประจำวัน</b>"]
    for sym in symbols:
        data = get_price(sym)
        if not data:
            lines.append(f"{sym}: ดึงราคาไม่ได้")
            continue
        change = data["change_pct"]
        arrow = "🟢" if (change or 0) >= 0 else "🔴"
        pct = f"{change * 100:+.2f}%" if change is not None else "-"
        lines.append(f"{sym}: {data['price']:,.2f} {data['currency']} {arrow} {pct}")
    return "\n".join(lines)


# ---------- Price alerts ----------

def add_alert(symbol, target, direction):
    result = _call_sheet({"action": "alert_add", "symbol": symbol, "target": target, "direction": direction})
    if not result or not result.get("ok"):
        return "❌ ตั้งแจ้งเตือนไม่สำเร็จครับ"
    arrow_text = "ขึ้นถึง" if direction == "UP" else "ลงถึง"
    return f"🔔 ตั้งแจ้งเตือน #{result['id']} แล้วครับ — {symbol} {arrow_text} {target}"


def remove_alert(alert_id):
    result = _call_sheet({"action": "alert_remove", "id": alert_id})
    if not result or not result.get("ok"):
        return f"❌ ลบแจ้งเตือน #{alert_id} ไม่สำเร็จครับ"
    if not result.get("removed"):
        return f"ไม่พบแจ้งเตือน #{alert_id} ครับ"
    return f"🗑️ ลบแจ้งเตือน #{alert_id} แล้วครับ"


def list_alerts():
    alerts = get_active_alerts_raw()
    if alerts is None:
        return "❌ ดึงรายการแจ้งเตือนไม่ได้ตอนนี้ครับ"
    if not alerts:
        return "ไม่มีแจ้งเตือนที่ตั้งไว้ตอนนี้ครับ"
    lines = ["🔔 <b>แจ้งเตือนที่ตั้งไว้:</b>"]
    for a in alerts:
        arrow = "⬆️" if a["direction"] == "UP" else "⬇️"
        lines.append(f"#{a['id']} {a['symbol']} {arrow} {a['target']}")
    return "\n".join(lines)


def get_active_alerts_raw():
    result = _call_sheet({"action": "alert_list"})
    if not result or not result.get("ok"):
        return None
    return result.get("alerts", [])


def mark_alert_triggered(alert_id):
    _call_sheet({"action": "alert_trigger", "id": alert_id})


def check_and_trigger_alerts():
    """Check every active alert against the live price; fire (and mark
    triggered, so it won't repeat) any that have been reached. Returns the
    list of notification messages to send."""
    alerts = get_active_alerts_raw()
    if not alerts:
        return []

    messages = []
    for a in alerts:
        data = get_price(a["symbol"])
        if not data:
            continue
        price = data["price"]
        try:
            target = float(a["target"])
        except (TypeError, ValueError):
            continue
        hit = price >= target if a["direction"] == "UP" else price <= target
        if hit:
            mark_alert_triggered(a["id"])
            arrow = "⬆️" if a["direction"] == "UP" else "⬇️"
            messages.append(
                f"🔔 แจ้งเตือน #{a['id']}: {a['symbol']} {arrow} ถึง {target} แล้ว "
                f"(ราคาปัจจุบัน {price:,.2f})"
            )
    return messages
