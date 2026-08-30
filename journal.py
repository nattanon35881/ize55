import os
import logging

import requests

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


def log_trade(side, symbol, entry, sl, tp, note=""):
    result = _call_sheet({
        "action": "log",
        "side": side,
        "symbol": symbol,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "note": note,
    })
    if not result or not result.get("ok"):
        return "❌ บันทึกไม่สำเร็จ เช็คว่าตั้งค่า SHEET_WEBAPP_URL ถูกต้องหรือยังครับ"

    trade_id = result["id"]
    return (
        f"✅ บันทึกไม้ #{trade_id} แล้วครับ\n"
        f"{side} {symbol} @ {entry}\n"
        f"SL: {sl} | TP: {tp}\n\n"
        f"ปิดไม้นี้ทีหลังด้วย: /close {trade_id} ราคาที่ปิด"
    )


def close_trade(trade_id, exit_price):
    result = _call_sheet({
        "action": "close",
        "id": trade_id,
        "exit_price": exit_price,
    })
    if not result or not result.get("ok"):
        error = (result or {}).get("error", "ไม่ทราบสาเหตุ — เช็ค SHEET_WEBAPP_URL ด้วยครับ")
        return f"❌ ปิดไม้ #{trade_id} ไม่สำเร็จ ({error})"

    r_multiple = result.get("r_multiple")
    r_text = f"{r_multiple:.2f}R" if isinstance(r_multiple, (int, float)) else "-"
    return f"✅ ปิดไม้ #{trade_id} ที่ {exit_price} แล้วครับ (ผลลัพธ์ {r_text})"


def list_open_trades():
    result = _call_sheet({"action": "list_open"})
    if not result or not result.get("ok"):
        return "❌ ดึงรายการไม้เปิดไม่ได้ตอนนี้ครับ"

    trades = result.get("trades", [])
    if not trades:
        return "ตอนนี้ไม่มีไม้ที่เปิดอยู่ครับ"

    lines = ["📋 <b>ไม้ที่เปิดอยู่:</b>"]
    for t in trades:
        lines.append(
            f"#{t['id']} {t['side']} {t['symbol']} @ {t['entry']} "
            f"(SL {t['sl']} / TP {t['tp']})"
        )
    return "\n".join(lines)


def get_open_trades_raw():
    """Same data as list_open_trades() but as a list of dicts, for
    building inline-keyboard pickers instead of plain text."""
    result = _call_sheet({"action": "list_open"})
    if not result or not result.get("ok"):
        return None
    return result.get("trades", [])


def get_stats():
    result = _call_sheet({"action": "stats"})
    if not result or not result.get("ok"):
        return "❌ ดึงสถิติไม่ได้ตอนนี้ครับ"

    closed = result.get("closed", 0)
    wins = result.get("wins", 0)
    losses = result.get("losses", 0)
    total_r = result.get("total_r", 0)
    win_rate = (wins / closed * 100) if closed else 0

    return (
        "📊 <b>สรุปผลการเทรด</b>\n"
        f"ปิดแล้ว: {closed} ไม้ (ชนะ {wins} / แพ้ {losses})\n"
        f"Win rate: {win_rate:.1f}%\n"
        f"รวม: {total_r:+.2f}R"
    )
