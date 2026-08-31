import os
import logging
from datetime import datetime, timedelta, timezone

import requests

from journal import get_all_trades

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


# ---------- Position size calculator ----------
# Deliberately conservative: only gold and standard 6-letter forex pairs get
# a "lots" conversion, because those contract sizes really are standard
# almost everywhere. Everything else (stocks, indices, silver, crypto...)
# just gets raw units — guessing a specific lot size for those would be a
# confidently wrong number on a real risk calculation, which is worse than
# no number at all.

def classify_instrument(symbol):
    symbol = symbol.upper()
    if symbol in ("XAUUSD", "GOLD"):
        return "gold", 100  # 1 standard lot = 100 oz
    if len(symbol) == 6 and symbol.isalpha():
        return "forex", 100000  # 1 standard lot = 100,000 units
    return "other", None


def calculate_position_size(capital, risk_percent, entry, sl):
    risk_amount = capital * (risk_percent / 100)
    distance = abs(entry - sl)
    if distance <= 0:
        return None
    return {"risk_amount": risk_amount, "distance": distance, "units": risk_amount / distance}


def format_position_size_message(symbol, capital, risk_percent, entry, sl):
    result = calculate_position_size(capital, risk_percent, entry, sl)
    if not result:
        return "❌ ราคา Entry กับ SL ต้องไม่เท่ากันครับ"

    kind, lot_size = classify_instrument(symbol)
    units = result["units"]

    lines = [
        f"📐 <b>คำนวณขนาดโพซิชัน — {symbol.upper()}</b>",
        f"เสี่ยง: {result['risk_amount']:,.2f} (ทุน {capital:,.2f} x {risk_percent}%)",
        f"ระยะ Entry-SL: {result['distance']:,.4f}",
    ]

    if lot_size:
        lots = units / lot_size
        lines.append(f"ขนาดที่เหมาะสม: {units:,.2f} หน่วย (≈ {lots:.3f} lot มาตรฐาน)")
        lines.append(
            f"<i>อ้างอิง contract size ทั่วไปของ {kind} ({lot_size:,} หน่วย/lot) "
            "— เช็คกับโบรกเกอร์จริงอีกครั้งก่อนเข้าไม้ เพราะบางที่กำหนดไม่เท่ากัน</i>"
        )
    else:
        lines.append(f"ขนาดที่เหมาะสม: {units:,.4f} หน่วย")
        lines.append("<i>ถ้าเทรดเป็น lot/contract ให้เช็คขนาดของโบรกเกอร์แล้วหารเอาเองครับ (ไม่ขอเดาให้ เพราะแตกต่างกันมากในแต่ละที่)</i>")

    return "\n".join(lines)


# ---------- Settings (loss limits) ----------

def get_settings():
    result = _call_sheet({"action": "get_settings"})
    if not result or not result.get("ok"):
        return {}
    return result.get("settings", {})


def set_setting(key, value):
    result = _call_sheet({"action": "set_setting", "key": key, "value": value})
    return bool(result and result.get("ok"))


def set_daily_limit(limit_r):
    ok = set_setting("daily_loss_limit_r", str(limit_r))
    return f"✅ ตั้ง limit ขาดทุนต่อวันไว้ที่ {limit_r}R แล้วครับ" if ok else "❌ ตั้งค่าไม่สำเร็จครับ"


def set_weekly_limit(limit_r):
    ok = set_setting("weekly_loss_limit_r", str(limit_r))
    return f"✅ ตั้ง limit ขาดทุนต่อสัปดาห์ไว้ที่ {limit_r}R แล้วครับ" if ok else "❌ ตั้งค่าไม่สำเร็จครับ"


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _realized_r_since(cutoff):
    trades = get_all_trades()
    if trades is None:
        return None
    total = 0.0
    for t in trades:
        if t.get("status") != "CLOSED":
            continue
        exit_time = _parse_iso(t.get("exit_time"))
        r = t.get("r_multiple")
        if exit_time is None or not isinstance(r, (int, float)):
            continue
        if exit_time >= cutoff:
            total += r
    return total


def check_trading_allowed():
    """Returns (allowed, message). If Sheet data can't be checked right
    now, fails OPEN (allowed=True) rather than locking the user out over
    a temporary hiccup — a lockout should only ever trigger on a real,
    confirmed loss limit breach."""
    settings = get_settings()
    daily_raw = settings.get("daily_loss_limit_r")
    weekly_raw = settings.get("weekly_loss_limit_r")
    if not daily_raw and not weekly_raw:
        return True, ""

    now = datetime.now(timezone.utc)

    if daily_raw:
        try:
            daily_limit = abs(float(daily_raw))
        except ValueError:
            daily_limit = None
        if daily_limit:
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_r = _realized_r_since(today_start)
            if today_r is not None and today_r <= -daily_limit:
                return False, (
                    f"🛑 วันนี้ขาดทุนรวม {today_r:+.2f}R แล้ว เกิน limit ที่ตั้งไว้ (-{daily_limit:.2f}R)\n"
                    "ระบบล็อกไม่ให้เปิดไม้ใหม่ — พักก่อนครับ พรุ่งนี้เริ่มใหม่ได้"
                )

    if weekly_raw:
        try:
            weekly_limit = abs(float(weekly_raw))
        except ValueError:
            weekly_limit = None
        if weekly_limit:
            week_start = now - timedelta(days=7)
            week_r = _realized_r_since(week_start)
            if week_r is not None and week_r <= -weekly_limit:
                return False, (
                    f"🛑 7 วันล่าสุดขาดทุนรวม {week_r:+.2f}R แล้ว เกิน limit ที่ตั้งไว้ (-{weekly_limit:.2f}R)\n"
                    "ระบบล็อกไม่ให้เปิดไม้ใหม่ในช่วงนี้ครับ — พักสักหน่อยดีกว่า"
                )

    return True, ""


def build_limits_status():
    settings = get_settings()
    daily_limit = settings.get("daily_loss_limit_r")
    weekly_limit = settings.get("weekly_loss_limit_r")

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    today_r = _realized_r_since(today_start)
    week_r = _realized_r_since(week_start)

    lines = ["🛡️ <b>Risk Limits</b>"]
    if today_r is not None:
        suffix = f" (limit -{daily_limit}R)" if daily_limit else " (ยังไม่ตั้ง limit)"
        lines.append(f"วันนี้: {today_r:+.2f}R{suffix}")
    else:
        lines.append("วันนี้: ดึงข้อมูลไม่ได้")

    if week_r is not None:
        suffix = f" (limit -{weekly_limit}R)" if weekly_limit else " (ยังไม่ตั้ง limit)"
        lines.append(f"7 วันล่าสุด: {week_r:+.2f}R{suffix}")
    else:
        lines.append("7 วันล่าสุด: ดึงข้อมูลไม่ได้")

    lines.append("\nตั้งค่าใหม่: /setlimit daily 3  หรือ  /setlimit weekly 6")
    return "\n".join(lines)
