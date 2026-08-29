import os
import logging

from flask import Flask, request, jsonify
import requests
import yfinance as yf

from forex import build_forex_alert_message, build_symbol_report, resolve_forex_symbol

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
FOREX_CHAT_ID = os.environ.get("FOREX_CHAT_ID", "")
FOREX_CRON_SECRET = os.environ.get("FOREX_CRON_SECRET", "")


def send_message(chat_id, text):
    """Send a message back to a Telegram chat."""
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
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
    """Try the symbol as typed, then with a .BK suffix (SET/Thai stocks).
    Returns (yfinance.Ticker, resolved_symbol, info_dict) or (None, None, None)
    if nothing usable was found.
    """
    candidates = [raw_symbol]
    if "." not in raw_symbol:
        candidates.append(f"{raw_symbol}.BK")

    for candidate in candidates:
        try:
            t = yf.Ticker(candidate)
            info = t.info
        except Exception:
            continue
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price:
            return t, candidate, info
    return None, None, None


def get_news(ticker, limit=3):
    """Handle both old and new yfinance .news response shapes defensively."""
    try:
        raw_items = ticker.news or []
    except Exception:
        return []

    items = []
    for raw in raw_items[:limit]:
        content = raw.get("content", raw)
        title = content.get("title") or raw.get("title")
        link = None
        click = content.get("clickThroughUrl") or content.get("canonicalUrl")
        if isinstance(click, dict):
            link = click.get("url")
        link = link or raw.get("link")
        if title and link:
            items.append((title, link))
    return items


def _get_row(df, names):
    """Return the first matching row (as a Series) from a yfinance financial
    statement DataFrame, trying several possible label spellings."""
    for name in names:
        if name in df.index:
            return df.loc[name]
    return None


def get_fcf_series(ticker):
    """Return [(year_label, free_cash_flow), ...] oldest-to-newest using
    real historical data from yfinance's annual cash flow statement."""
    try:
        cf = ticker.cashflow
    except Exception:
        return []
    if cf is None or cf.empty:
        return []

    fcf_row = _get_row(cf, ["Free Cash Flow"])
    if fcf_row is None:
        ocf_row = _get_row(cf, ["Operating Cash Flow", "Total Cash From Operating Activities"])
        capex_row = _get_row(cf, ["Capital Expenditure", "Capital Expenditures"])
        if ocf_row is None or capex_row is None:
            return []
        # CapEx is reported as a negative (cash outflow), so adding it
        # subtracts it from operating cash flow correctly.
        fcf_row = ocf_row.add(capex_row, fill_value=0)

    pairs = [(col, val) for col, val in fcf_row.items() if val == val]  # drop NaN
    pairs.sort(key=lambda p: p[0])  # oldest -> newest
    return [(str(getattr(d, "year", d)), float(v)) for d, v in pairs]


def estimate_growth_rate(fcf_series, default=0.05, min_rate=0.0, max_rate=0.15):
    """CAGR of historical FCF, capped to a sane range so it can never run away."""
    positive = [v for _, v in fcf_series if v and v > 0]
    if len(positive) < 2:
        return default
    years = len(positive) - 1
    if years <= 0 or positive[0] <= 0:
        return default
    try:
        cagr = (positive[-1] / positive[0]) ** (1 / years) - 1
    except Exception:
        return default
    return max(min_rate, min(max_rate, cagr))


def estimate_discount_rate(beta, risk_free=0.04, erp=0.05, min_rate=0.06, max_rate=0.15):
    """CAPM-based discount rate from the stock's actual beta, capped to a sane range."""
    b = beta if beta else 1.0
    r = risk_free + b * erp
    return max(min_rate, min(max_rate, r))


def compute_dcf_value(ticker, info, years=5, terminal_growth=0.025):
    """2-stage DCF using real FCF history and a CAPM discount rate.
    Growth and terminal-growth rates are always kept below the discount
    rate so the terminal-value formula can never blow up or go negative
    (this was the bug in the original spreadsheet).
    Returns (intrinsic_value_per_share, r, g1, g_term) or None.
    """
    fcf_series = get_fcf_series(ticker)
    if not fcf_series:
        return None
    latest_fcf = fcf_series[-1][1]
    if not latest_fcf or latest_fcf <= 0:
        return None

    shares = info.get("sharesOutstanding")
    if not shares:
        return None

    r = estimate_discount_rate(info.get("beta"))
    g1 = min(estimate_growth_rate(fcf_series), r - 0.01)
    g_term = min(terminal_growth, r - 0.01)

    pv_sum = 0.0
    fcf_t = latest_fcf
    for t in range(1, years + 1):
        fcf_t *= (1 + g1)
        pv_sum += fcf_t / ((1 + r) ** t)

    terminal_value = fcf_t * (1 + g_term) / (r - g_term)
    pv_terminal = terminal_value / ((1 + r) ** years)

    intrinsic_per_share = (pv_sum + pv_terminal) / shares
    return intrinsic_per_share, r, g1, g_term


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
    prev_close = info.get("previousClose")
    change_pct = None
    if price and prev_close:
        change_pct = (price - prev_close) / prev_close

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

    dcf_result = None
    try:
        dcf_result = compute_dcf_value(ticker, info)
    except Exception:
        logger.exception("DCF calculation failed for %s", resolved)

    if dcf_result:
        intrinsic, r, g1, g_term = dcf_result
        dcf_line = f"\n💰 มูลค่าที่แท้จริงโดยประมาณ (DCF 5 ปี): {fmt_num(intrinsic)} {currency}"
        if price:
            diff_pct = (intrinsic - price) / price
            verdict = "ต่ำกว่าราคาตลาด" if diff_pct > 0 else "สูงกว่าราคาตลาด"
            dcf_line += f"\n   ต่างจากราคาปัจจุบัน {fmt_pct(diff_pct)} ({verdict})"
        dcf_line += (
            f"\n   <i>สมมติฐาน: growth {fmt_pct(g1)}/ปี, "
            f"discount rate {fmt_pct(r)}, terminal growth {fmt_pct(g_term)}</i>"
        )
        lines.append(dcf_line)
    else:
        lines.append("\n💰 คำนวณ DCF ไม่ได้ — ข้อมูลกระแสเงินสด/จำนวนหุ้นไม่พอสำหรับหุ้นตัวนี้")

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
    # Render (and similar hosts) ping this so the service is marked healthy.
    return "OK", 200


@app.route(f"/forex-check/{FOREX_CRON_SECRET}", methods=["GET", "POST"])
def forex_check():
    # Triggered by an external free cron service at the user's 5 scheduled
    # check times (see README) — this route itself does not run a
    # background scheduler, since Render's free tier can't keep one alive.
    if not FOREX_CHAT_ID:
        return jsonify(ok=False, error="FOREX_CHAT_ID is not set"), 400
    message = build_forex_alert_message()
    send_message(FOREX_CHAT_ID, message)
    return jsonify(ok=True)


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
            "สวัสดีครับ 👋\n"
            "พิมพ์ชื่อย่อหุ้น เช่น PTT, ADVANC, AAPL, GOOGL "
            "แล้วบอทจะตอบราคา ข่าวล่าสุด และพื้นฐานบริษัทให้ครับ\n\n"
            "หรือพิมพ์สัญลักษณ์ทอง/ดัชนี/คู่เงิน เช่น XAUUSD, US100, EURUSD "
            "เพื่อเช็คราคาสดและ Bias จาก Fibo 4H ตามระบบ ize\n\n"
            f"Chat ID ของคุณ (เอาไปตั้งค่าแจ้งเตือน Forex): <code>{chat_id}</code>",
        )
        return jsonify(ok=True)

    if resolve_forex_symbol(text):
        report = build_symbol_report(text)
    else:
        report = build_report(text)

    if report:
        send_message(chat_id, report)
    return jsonify(ok=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
