import logging
import os

import requests
import yfinance as yf
from flask import Flask, jsonify, request


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


def send_message(chat_id, text):
    """Send a message back to a Telegram chat."""
    try:
        response = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("send_message failed")


def fmt_num(value, digits=2):
    if value is None:
        return "-"
    try:
        return f"{value:,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_pct(value, digits=2):
    if value is None:
        return "-"
    try:
        return f"{value * 100:,.{digits}f}%"
    except (TypeError, ValueError):
        return str(value)


def resolve_ticker(raw_symbol):
    """Try the symbol as typed, then with a .BK suffix for Thai stocks."""
    candidates = [raw_symbol]
    if "." not in raw_symbol:
        candidates.append(f"{raw_symbol}.BK")

    for candidate in candidates:
        try:
            ticker = yf.Ticker(candidate)
            info = ticker.info
        except Exception:
            continue
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price:
            return ticker, candidate, info
    return None, None, None


def get_news(ticker, limit=3):
    """Handle both old and new yfinance news response shapes."""
    try:
        raw_items = ticker.news or []
    except Exception:
        return []

    items = []
    for raw in raw_items[:limit]:
        content = raw.get("content", raw)
        title = content.get("title") or raw.get("title")
        click = content.get("clickThroughUrl") or content.get("canonicalUrl")
        link = click.get("url") if isinstance(click, dict) else click
        link = link or raw.get("link")
        if title and link:
            items.append((title, link))
    return items


def build_report(raw_symbol):
    symbol = raw_symbol.strip().upper()
    if not symbol:
        return None

    ticker, resolved, info = resolve_ticker(symbol)
    if ticker is None:
        return (
            f'❌ ไม่พบข้อมูลหุ้น "{symbol}"\n'
            "ลองพิมพ์ชื่อย่อหุ้นให้ตรง เช่น PTT, ADVANC, KBANK (หุ้นไทย) "
            "หรือ AAPL, GOOGL, MSFT (หุ้นต่างประเทศ)"
        )

    name = info.get("longName") or info.get("shortName") or resolved
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    currency = info.get("currency", "")
    previous_close = info.get("previousClose")
    change_pct = None
    if price is not None and previous_close:
        change_pct = (price - previous_close) / previous_close

    lines = [f"<b>{name} ({resolved})</b>"]
    price_line = f"ราคา: {fmt_num(price)} {currency}".strip()
    if change_pct is not None:
        arrow = "🟢" if change_pct >= 0 else "🔴"
        price_line += f"  {arrow} {fmt_pct(change_pct)}"
    lines.append(price_line)
    lines.append(
        "P/E: {} | P/B: {} | EPS: {}".format(
            fmt_num(info.get("trailingPE")),
            fmt_num(info.get("priceToBook")),
            fmt_num(info.get("trailingEps")),
        )
    )
    lines.append(
        "ROE: {} | ROA: {}".format(
            fmt_pct(info.get("returnOnEquity")),
            fmt_pct(info.get("returnOnAssets")),
        )
    )
    lines.append(
        "Net Margin: {} | Gross Margin: {}".format(
            fmt_pct(info.get("profitMargins")),
            fmt_pct(info.get("grossMargins")),
        )
    )
    market_cap = info.get("marketCap")
    if market_cap:
        lines.append(f"Market Cap: {fmt_num(market_cap, digits=0)}")

    news_items = get_news(ticker)
    if news_items:
        lines.append("\n📰 ข่าวล่าสุด:")
        for title, link in news_items:
            lines.append(f'• <a href="{link}">{title}</a>')
    else:
        lines.append("\n📰 ไม่พบข่าวล่าสุดในระบบตอนนี้")
    return "\n".join(lines)


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify(ok=True)

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if not text:
        return jsonify(ok=True)

    if text.startswith("/start") or text.startswith("/help"):
        send_message(
            chat_id,
            "สวัสดีครับ 👋\nพิมพ์ชื่อย่อหุ้น เช่น PTT, ADVANC, AAPL, GOOGL "
            "แล้วบอทจะตอบราคา ข่าวล่าสุด และพื้นฐานบริษัทให้ครับ",
        )
        return jsonify(ok=True)

    report = build_report(text)
    if report:
        send_message(chat_id, report)
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)