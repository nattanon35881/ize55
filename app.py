import os
import logging

from flask import Flask, request, jsonify
import requests
import yfinance as yf

from cot import build_cot_report, build_cot_weekly_digest
from dashboard import build_dashboard_html
from forex import build_forex_alert_message, build_symbol_report, resolve_forex_symbol
from journal import close_trade, get_all_trades, get_open_trades_raw, get_stats, list_open_trades, log_trade
from macro import build_macro_dashboard_html, build_macro_report
from qt import build_qt_report
from risk import (
    build_limits_status,
    check_trading_allowed,
    format_position_size_message,
    set_daily_limit,
    set_weekly_limit,
)
from watchlist import (
    add_alert,
    add_to_watchlist,
    build_watchlist_summary,
    check_and_trigger_alerts,
    get_price,
    list_alerts,
    remove_alert,
    remove_from_watchlist,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
FOREX_CHAT_ID = os.environ.get("FOREX_CHAT_ID", "")
FOREX_CRON_SECRET = os.environ.get("FOREX_CRON_SECRET", "")
DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET", "")


def send_message(chat_id, text, reply_markup=None):
    """Send a message back to a Telegram chat. reply_markup can carry an
    inline keyboard (see _keyboard())."""
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)
    except Exception:
        logger.exception("send_message failed")


def answer_callback(callback_query_id):
    """Acknowledge a button tap so Telegram stops showing the loading spinner."""
    try:
        requests.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id},
            timeout=10,
        )
    except Exception:
        logger.exception("answer_callback failed")


def _keyboard(rows):
    return {"inline_keyboard": rows}


# In-memory per-chat state for the guided /log and /close flows (button
# taps + follow-up text answers). This resets if the process restarts —
# e.g. Render's free tier sleeping after 15 min idle — but a form is
# normally filled within a minute or two of active back-and-forth, which
# itself keeps the service awake, so in practice this is rarely an issue.
_log_state = {}
_close_state = {}


def start_log_flow(chat_id):
    allowed, reason = check_trading_allowed()
    if not allowed:
        send_message(chat_id, reason)
        return
    _log_state[chat_id] = {"step": "side"}
    send_message(
        chat_id,
        "บันทึกไม้ใหม่ — ฝั่งไหนครับ?",
        _keyboard([[
            {"text": "🟢 BUY", "callback_data": "logside:BUY"},
            {"text": "🔴 SELL", "callback_data": "logside:SELL"},
        ]]),
    )


def start_close_flow(chat_id):
    trades = get_open_trades_raw()
    if trades is None:
        send_message(chat_id, "❌ ดึงรายการไม้เปิดไม่ได้ตอนนี้ครับ")
        return
    if not trades:
        send_message(chat_id, "ตอนนี้ไม่มีไม้ที่เปิดอยู่ครับ")
        return
    rows = [
        [{
            "text": f"#{t['id']} {t['side']} {t['symbol']} @ {t['entry']}",
            "callback_data": f"closepick:{t['id']}",
        }]
        for t in trades
    ]
    send_message(chat_id, "จะปิดไม้ไหนครับ?", _keyboard(rows))


def handle_callback(callback):
    """Handle an inline-button tap (Telegram 'callback_query' update)."""
    callback_id = callback["id"]
    chat_id = callback["message"]["chat"]["id"]
    data = callback.get("data", "")
    answer_callback(callback_id)

    if data.startswith("logside:"):
        side = data.split(":", 1)[1]
        _log_state[chat_id] = {"step": "symbol", "side": side}
        send_message(chat_id, "พิมพ์สัญลักษณ์ที่เทรด (เช่น XAUUSD)")
        return

    if data.startswith("logidm:"):
        state = _log_state.get(chat_id)
        if not state:
            send_message(chat_id, "เซสชันหมดอายุแล้วครับ ลองพิมพ์ /log ใหม่")
            return
        state["idm_ok"] = data.split(":", 1)[1] == "yes"
        state["step"] = "turtle"
        send_message(
            chat_id,
            "Confirm ด้วยสัญญาณ Turtle Soup บน M1/M5 จริงมั้ย?",
            _keyboard([[
                {"text": "✅ ใช่", "callback_data": "logturtle:yes"},
                {"text": "❌ ไม่ใช่", "callback_data": "logturtle:no"},
            ]]),
        )
        return

    if data.startswith("logturtle:"):
        state = _log_state.get(chat_id)
        if not state:
            send_message(chat_id, "เซสชันหมดอายุแล้วครับ ลองพิมพ์ /log ใหม่")
            return
        state["turtle_ok"] = data.split(":", 1)[1] == "yes"
        summary = (
            "ยืนยันบันทึกไม้นี้มั้ย?\n\n"
            f"{state['side']} {state['symbol']} @ {state['entry']}\n"
            f"SL: {state['sl']} | TP: {state['tp']}\n"
            f"หมายเหตุ: {state['note'] or '-'}\n"
            f"IDM: {'✅' if state['idm_ok'] else '❌'} | Turtle Soup: {'✅' if state['turtle_ok'] else '❌'}"
        )
        send_message(
            chat_id,
            summary,
            _keyboard([[
                {"text": "✅ บันทึก", "callback_data": "logconfirm:yes"},
                {"text": "❌ ยกเลิก", "callback_data": "logconfirm:no"},
            ]]),
        )
        return

    if data.startswith("logconfirm:"):
        choice = data.split(":", 1)[1]
        state = _log_state.pop(chat_id, None)
        if choice == "yes" and state:
            send_message(
                chat_id,
                log_trade(
                    state["side"], state["symbol"], state["entry"],
                    state["sl"], state["tp"], state.get("note", ""),
                    idm_ok=state.get("idm_ok"), turtle_ok=state.get("turtle_ok"),
                ),
            )
        else:
            send_message(chat_id, "ยกเลิกแล้วครับ")
        return

    if data.startswith("closepick:"):
        trade_id = data.split(":", 1)[1]
        trades = get_open_trades_raw() or []
        trade = next((t for t in trades if str(t["id"]) == trade_id), None)
        if not trade:
            send_message(chat_id, "ไม่พบไม้นี้แล้วครับ (อาจปิดไปแล้ว)")
            return
        _close_state[chat_id] = {"trade_id": trade_id, "tp": trade["tp"], "sl": trade["sl"]}
        send_message(
            chat_id,
            f"ไม้ #{trade_id} จะปิดยังไง?",
            _keyboard([
                [
                    {"text": f"🎯 TP ({trade['tp']})", "callback_data": "closeat:tp"},
                    {"text": f"🛑 SL ({trade['sl']})", "callback_data": "closeat:sl"},
                ],
                [{"text": "✏️ พิมพ์ราคาเอง", "callback_data": "closeat:custom"}],
            ]),
        )
        return

    if data.startswith("closeat:"):
        choice = data.split(":", 1)[1]
        state = _close_state.get(chat_id)
        if not state:
            send_message(chat_id, "เซสชันหมดอายุแล้วครับ ลองพิมพ์ /close ใหม่")
            return
        if choice == "custom":
            state["step"] = "await_price"
            send_message(chat_id, "พิมพ์ราคาที่ปิดจริงครับ")
            return
        exit_price = state["tp"] if choice == "tp" else state["sl"]
        _close_state.pop(chat_id, None)
        send_message(chat_id, close_trade(state["trade_id"], exit_price))
        return


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


def _find_latest_swing(window, pivot_window=1):
    """Find the most recent confirmed swing leg (a low-to-high or
    high-to-low move), using simple local-extremum pivot detection,
    instead of the absolute min/max of the whole lookback window. A
    long-ago extreme that price never revisits makes a stale, useless
    anchor for a trade decision — the latest actual turning point is
    what matters.
    Returns (older_price, newer_price) or None if no swing pair is found.
    """
    highs = window["High"].tolist()
    lows = window["Low"].tolist()
    n = len(window)

    pivots = []  # (index, price, kind)
    for i in range(pivot_window, n - pivot_window):
        seg_high = highs[i - pivot_window : i + pivot_window + 1]
        if highs[i] == max(seg_high):
            pivots.append((i, highs[i], "high"))
        seg_low = lows[i - pivot_window : i + pivot_window + 1]
        if lows[i] == min(seg_low):
            pivots.append((i, lows[i], "low"))

    pivots.sort(key=lambda p: p[0])
    if len(pivots) < 2:
        return None

    latest = pivots[-1]
    for earlier in reversed(pivots[:-1]):
        if earlier[2] != latest[2]:
            return earlier[1], latest[1]
    return None


def compute_monthly_bias(ticker, lookback_months=24, pivot_window=1):
    """Same cheap/expensive Fibonacci bias as the ize forex system (swing
    low/high -> 0/50/100 range -> above 50% = แพง/SELL zone, below = ถูก/
    BUY zone), but read from monthly candles instead of 4H — stocks move
    far slower than gold/forex, so a monthly view suits the same idea
    much better than an intraday one. The swing itself is the most
    recent confirmed pivot-to-pivot leg, not just the window's raw
    highest-high / lowest-low, so it stays relevant to a current
    decision rather than anchored on an old extreme.
    """
    try:
        hist = ticker.history(period="5y", interval="1mo")
    except Exception:
        return None
    if hist is None or hist.empty:
        return None

    window = hist.tail(lookback_months)
    if len(window) < (pivot_window * 2 + 3):
        return None

    swing = _find_latest_swing(window, pivot_window=pivot_window)
    if not swing:
        return None
    swing_low, swing_high = min(swing), max(swing)
    if swing_high <= swing_low:
        return None

    current_price = float(hist["Close"].iloc[-1])
    fib_50 = (swing_low + swing_high) / 2
    bias = "แพง — โซน SELL" if current_price > fib_50 else "ถูก — โซน BUY"

    return {
        "price": current_price,
        "swing_low": swing_low,
        "swing_high": swing_high,
        "fib_50": fib_50,
        "bias": bias,
    }


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

    monthly_bias = None
    try:
        monthly_bias = compute_monthly_bias(ticker)
    except Exception:
        logger.exception("compute_monthly_bias failed for %s", resolved)

    if monthly_bias:
        lines.append(
            f"\n📊 <b>Bias จาก Fibo รายเดือน ({monthly_bias['bias']})</b>\n"
            f"   0% (swing low, 24 เดือนล่าสุด): {fmt_num(monthly_bias['swing_low'])}\n"
            f"   50%: {fmt_num(monthly_bias['fib_50'])}\n"
            f"   100% (swing high): {fmt_num(monthly_bias['swing_high'])}"
        )

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


@app.route(f"/dashboard/{DASHBOARD_SECRET}", methods=["GET"])
def dashboard():
    # Secret is part of the path (like /webhook/<token>) so random visitors
    # can't view your trade history just by guessing the base URL.
    trades = get_all_trades()
    if trades is None:
        return "ดึงข้อมูลจาก Google Sheet ไม่ได้ตอนนี้ — เช็ค SHEET_WEBAPP_URL ครับ", 502
    return build_dashboard_html(trades)


@app.route(f"/macro-dashboard/{DASHBOARD_SECRET}", methods=["GET"])
def macro_dashboard():
    return build_macro_dashboard_html()


@app.route(f"/watchlist-check/{FOREX_CRON_SECRET}", methods=["GET", "POST"])
def watchlist_check():
    # Same shared secret as /forex-check — set up a daily cron job (e.g.
    # 9:00 AM) at cron-job.org pointing here, same as the forex alerts.
    if not FOREX_CHAT_ID:
        return jsonify(ok=False, error="FOREX_CHAT_ID is not set"), 400
    send_message(FOREX_CHAT_ID, build_watchlist_summary())
    return jsonify(ok=True)


@app.route(f"/alert-check/{FOREX_CRON_SECRET}", methods=["GET", "POST"])
def alert_check():
    # Point a cron job here every 15-30 min during market hours to check
    # active price alerts and notify when one is hit.
    if not FOREX_CHAT_ID:
        return jsonify(ok=False, error="FOREX_CHAT_ID is not set"), 400
    messages = check_and_trigger_alerts()
    for message in messages:
        send_message(FOREX_CHAT_ID, message)
    return jsonify(ok=True, triggered=len(messages))


@app.route(f"/cot-check/{FOREX_CRON_SECRET}", methods=["GET", "POST"])
def cot_check():
    # CFTC publishes new COT data every Friday ~3:30pm ET — point a
    # weekly cron job here (e.g. Saturday morning) to get the digest
    # without asking for it.
    if not FOREX_CHAT_ID:
        return jsonify(ok=False, error="FOREX_CHAT_ID is not set"), 400
    send_message(FOREX_CHAT_ID, build_cot_weekly_digest())
    return jsonify(ok=True)


@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}

    callback = update.get("callback_query")
    if callback:
        handle_callback(callback)
        return jsonify(ok=True)

    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify(ok=True)

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if not text:
        return jsonify(ok=True)

    if text == "/cancel":
        had_state = _log_state.pop(chat_id, None) or _close_state.pop(chat_id, None)
        send_message(chat_id, "ยกเลิกแล้วครับ" if had_state else "ไม่มีอะไรให้ยกเลิกตอนนี้ครับ")
        return jsonify(ok=True)

    # --- guided /log flow: waiting for the next typed answer ---
    if chat_id in _log_state:
        state = _log_state[chat_id]
        step = state["step"]
        if step == "symbol":
            state["symbol"] = text.upper()
            state["step"] = "entry"
            send_message(chat_id, "ราคาเข้า (Entry)?")
            return jsonify(ok=True)
        if step == "entry":
            try:
                state["entry"] = float(text)
            except ValueError:
                send_message(chat_id, "พิมพ์เป็นตัวเลขครับ เช่น 2650")
                return jsonify(ok=True)
            state["step"] = "sl"
            send_message(chat_id, "Stop Loss (SL)?")
            return jsonify(ok=True)
        if step == "sl":
            try:
                state["sl"] = float(text)
            except ValueError:
                send_message(chat_id, "พิมพ์เป็นตัวเลขครับ")
                return jsonify(ok=True)
            state["step"] = "tp"
            send_message(chat_id, "Take Profit (TP)?")
            return jsonify(ok=True)
        if step == "tp":
            try:
                state["tp"] = float(text)
            except ValueError:
                send_message(chat_id, "พิมพ์เป็นตัวเลขครับ")
                return jsonify(ok=True)
            state["step"] = "note"
            send_message(chat_id, "หมายเหตุ (ถ้าไม่มีพิมพ์ -)")
            return jsonify(ok=True)
        if step == "note":
            state["note"] = "" if text == "-" else text
            state["step"] = "idm"
            send_message(
                chat_id,
                "รอ IDM (inducement) ก่อนเข้าไม้จริงมั้ย?",
                _keyboard([[
                    {"text": "✅ ใช่", "callback_data": "logidm:yes"},
                    {"text": "❌ ไม่ใช่", "callback_data": "logidm:no"},
                ]]),
            )
            return jsonify(ok=True)

    # --- guided /close flow: waiting for a custom exit price ---
    if chat_id in _close_state and _close_state[chat_id].get("step") == "await_price":
        state = _close_state.pop(chat_id)
        try:
            exit_price = float(text)
        except ValueError:
            send_message(chat_id, "พิมพ์เป็นตัวเลขครับ")
            _close_state[chat_id] = state  # still waiting, put it back
            return jsonify(ok=True)
        send_message(chat_id, close_trade(state["trade_id"], exit_price))
        return jsonify(ok=True)

    if text.startswith("/start") or text.startswith("/help"):
        send_message(
            chat_id,
            "สวัสดีครับ 👋\n"
            "พิมพ์ชื่อย่อหุ้น เช่น PTT, ADVANC, AAPL, GOOGL "
            "แล้วบอทจะตอบราคา ข่าวล่าสุด และพื้นฐานบริษัทให้ครับ\n\n"
            "หรือพิมพ์สัญลักษณ์ทอง/ดัชนี/คู่เงิน เช่น XAUUSD, US100, EURUSD "
            "เพื่อเช็คราคาสดและ Bias จาก Fibo 4H ตามระบบ ize\n\n"
            "<b>บันทึกไม้เทรด:</b>\n"
            "พิมพ์ /log หรือ \"บันทึก\" เฉยๆ ให้บอทถามทีละขั้นตอนพร้อมปุ่มกด\n"
            "(หรือพิมพ์รวดเดียว: /log BUY XAUUSD 2650 2620 2700)\n"
            "/close หรือ \"ปิดไม้\" — เลือกไม้ที่จะปิดจากปุ่ม\n"
            "/trades — ดูไม้ที่เปิดอยู่\n"
            "/stats — สรุปผลรวม\n\n"
            "<b>Watchlist หุ้น:</b>\n"
            "/watch SYMBOL — เพิ่มเข้า watchlist พิมพ์หลายตัวคั่นด้วยเว้นวรรคได้ เช่น /watch PTT ADVANC AAPL\n"
            "/unwatch SYMBOL — เอาออก (พิมพ์หลายตัวได้เหมือนกัน)\n"
            "/watchlist — ดูราคาทุกตัวใน watchlist ตอนนี้\n\n"
            "<b>แจ้งเตือนราคาหุ้น:</b>\n"
            "/alert SYMBOL ราคาเป้าหมาย เช่น /alert PTT 35\n"
            "/alerts — ดูแจ้งเตือนที่ตั้งไว้\n"
            "/unalert ID — ยกเลิกแจ้งเตือน\n\n"
            "<b>Macro:</b>\n"
            "/macro — DXY, US 10Y Yield, SET Index แบบสรุปเร็ว\n"
            "/cot [SYMBOL] — COT Report ตำแหน่งสถาบัน (default XAUUSD) เช่น /cot XAUUSD\n"
            "/qt — TDO Bias + โมเดล AMDX/XAMD ของทอง (Quarterly Theory)\n\n"
            "<b>คำนวณขนาดโพซิชัน:</b>\n"
            "/size SYMBOL ทุน RISK% ราคาเข้า SL\n"
            "เช่น /size XAUUSD 10000 1 2650 2620\n\n"
            "<b>วงเงินขาดทุน (auto lockout):</b>\n"
            "/setlimit daily 3 หรือ /setlimit weekly 6 (หน่วยเป็น R)\n"
            "/limits — ดูสถานะปัจจุบัน\n\n"
            f"Chat ID ของคุณ (เอาไปตั้งค่าแจ้งเตือน Forex): <code>{chat_id}</code>",
        )
        return jsonify(ok=True)

    if text.startswith("/log") or text == "บันทึก":
        parts = text.split()
        if len(parts) <= 1:
            start_log_flow(chat_id)
            return jsonify(ok=True)
        if len(parts) < 6:
            send_message(
                chat_id,
                "รูปแบบ: /log BUY หรือ SELL SYMBOL ENTRY SL TP [หมายเหตุ]\n"
                "ตัวอย่าง: /log BUY XAUUSD 2650 2620 2700\n"
                "หรือพิมพ์ /log เฉยๆ ให้บอทถามทีละขั้นตอนก็ได้ครับ",
            )
            return jsonify(ok=True)
        _, side, symbol, entry, sl, tp, *note_parts = parts
        side = side.upper()
        if side not in ("BUY", "SELL"):
            send_message(chat_id, "ระบุ BUY หรือ SELL เท่านั้นครับ")
            return jsonify(ok=True)
        try:
            entry_f, sl_f, tp_f = float(entry), float(sl), float(tp)
        except ValueError:
            send_message(chat_id, "ราคา entry/SL/TP ต้องเป็นตัวเลขครับ")
            return jsonify(ok=True)
        note = " ".join(note_parts)
        allowed, reason = check_trading_allowed()
        if not allowed:
            send_message(chat_id, reason)
            return jsonify(ok=True)
        send_message(chat_id, log_trade(side, symbol.upper(), entry_f, sl_f, tp_f, note))
        return jsonify(ok=True)

    if text.startswith("/close") or text == "ปิดไม้":
        parts = text.split()
        if len(parts) <= 1:
            start_close_flow(chat_id)
            return jsonify(ok=True)
        if len(parts) != 3:
            send_message(
                chat_id,
                "รูปแบบ: /close ID ราคาที่ปิด\nตัวอย่าง: /close 1 2680\n"
                "หรือพิมพ์ /close เฉยๆ ให้เลือกจากปุ่มก็ได้ครับ",
            )
            return jsonify(ok=True)
        _, trade_id, exit_price = parts
        try:
            exit_price_f = float(exit_price)
        except ValueError:
            send_message(chat_id, "ราคาที่ปิดต้องเป็นตัวเลขครับ")
            return jsonify(ok=True)
        send_message(chat_id, close_trade(trade_id, exit_price_f))
        return jsonify(ok=True)

    if text.startswith("/trades"):
        send_message(chat_id, list_open_trades())
        return jsonify(ok=True)

    if text.startswith("/stats"):
        send_message(chat_id, get_stats())
        return jsonify(ok=True)

    if text.startswith("/watch ") or text.startswith("/watch\n"):
        symbols_part = text.split(maxsplit=1)[1].strip()
        symbols = [s for s in symbols_part.upper().split() if s]
        if not symbols:
            send_message(chat_id, "รูปแบบ: /watch SYMBOL [SYMBOL ...]\nตัวอย่าง: /watch PTT หรือ /watch PTT ADVANC AAPL")
            return jsonify(ok=True)
        results = [add_to_watchlist(sym) for sym in symbols]
        send_message(chat_id, "\n".join(results))
        return jsonify(ok=True)

    if text.startswith("/unwatch"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            send_message(chat_id, "รูปแบบ: /unwatch SYMBOL [SYMBOL ...]\nตัวอย่าง: /unwatch PTT หรือ /unwatch PTT ADVANC")
            return jsonify(ok=True)
        symbols = [s for s in parts[1].strip().upper().split() if s]
        results = [remove_from_watchlist(sym) for sym in symbols]
        send_message(chat_id, "\n".join(results))
        return jsonify(ok=True)

    if text.startswith("/watchlist"):
        send_message(chat_id, build_watchlist_summary())
        return jsonify(ok=True)

    if text.startswith("/alert ") and not text.startswith("/alerts"):
        parts = text.split()
        if len(parts) != 3:
            send_message(chat_id, "รูปแบบ: /alert SYMBOL ราคาเป้าหมาย\nตัวอย่าง: /alert PTT 35")
            return jsonify(ok=True)
        _, symbol, target = parts
        symbol = symbol.upper()
        try:
            target_f = float(target)
        except ValueError:
            send_message(chat_id, "ราคาต้องเป็นตัวเลขครับ")
            return jsonify(ok=True)
        current = get_price(symbol)
        if not current:
            send_message(chat_id, f"หาราคา {symbol} ไม่เจอครับ")
            return jsonify(ok=True)
        direction = "UP" if target_f >= current["price"] else "DOWN"
        send_message(chat_id, add_alert(symbol, target_f, direction))
        return jsonify(ok=True)

    if text.startswith("/unalert"):
        parts = text.split()
        if len(parts) != 2:
            send_message(chat_id, "รูปแบบ: /unalert ID\nตัวอย่าง: /unalert 1")
            return jsonify(ok=True)
        send_message(chat_id, remove_alert(parts[1]))
        return jsonify(ok=True)

    if text.startswith("/alerts"):
        send_message(chat_id, list_alerts())
        return jsonify(ok=True)

    if text.startswith("/macro"):
        send_message(chat_id, build_macro_report())
        return jsonify(ok=True)

    if text.startswith("/cot"):
        parts = text.split()
        symbol = parts[1].upper() if len(parts) > 1 else "XAUUSD"
        send_message(chat_id, build_cot_report(symbol))
        return jsonify(ok=True)

    if text.startswith("/qt"):
        send_message(chat_id, build_qt_report())
        return jsonify(ok=True)

    if text.startswith("/size"):
        parts = text.split()
        if len(parts) != 5:
            send_message(
                chat_id,
                "รูปแบบ: /size SYMBOL ทุน RISK% ราคาเข้า SL\n"
                "ตัวอย่าง: /size XAUUSD 10000 1 2650 2620",
            )
            return jsonify(ok=True)
        _, size_symbol, capital, risk_pct, entry_price, sl_price = parts
        try:
            capital_f = float(capital)
            risk_pct_f = float(risk_pct)
            entry_f = float(entry_price)
            sl_f = float(sl_price)
        except ValueError:
            send_message(chat_id, "ทุน/RISK%/ราคาเข้า/SL ต้องเป็นตัวเลขครับ")
            return jsonify(ok=True)
        send_message(chat_id, format_position_size_message(size_symbol, capital_f, risk_pct_f, entry_f, sl_f))
        return jsonify(ok=True)

    if text.startswith("/setlimit"):
        parts = text.split()
        if len(parts) != 3 or parts[1].lower() not in ("daily", "weekly"):
            send_message(
                chat_id,
                "รูปแบบ: /setlimit daily ตัวเลขR หรือ /setlimit weekly ตัวเลขR\n"
                "ตัวอย่าง: /setlimit daily 3",
            )
            return jsonify(ok=True)
        try:
            limit_value = float(parts[2])
        except ValueError:
            send_message(chat_id, "ค่า limit ต้องเป็นตัวเลขครับ")
            return jsonify(ok=True)
        if parts[1].lower() == "daily":
            send_message(chat_id, set_daily_limit(limit_value))
        else:
            send_message(chat_id, set_weekly_limit(limit_value))
        return jsonify(ok=True)

    if text.startswith("/limits"):
        send_message(chat_id, build_limits_status())
        return jsonify(ok=True)

    if resolve_forex_symbol(text):
        report = build_symbol_report(text)
    else:
        report = build_report(text)

    if report:
        send_message(chat_id, report)
    return jsonify(ok=True)


def setup_bot_commands():
    """Register the command list with Telegram so it shows up as a native
    tappable menu (the '/' button, or the menu icon, next to the message
    box) — this is the real Telegram equivalent of a commands popup, no
    custom UI code needed, just this one-time registration call. Runs
    automatically every time the app starts (cheap and idempotent, so
    Render's free-tier cold starts calling it repeatedly is harmless)."""
    commands = [
        {"command": "log", "description": "บันทึกไม้เทรดใหม่ (ถามทีละขั้นตอน)"},
        {"command": "close", "description": "ปิดไม้ที่เปิดอยู่"},
        {"command": "trades", "description": "ดูไม้ที่เปิดอยู่"},
        {"command": "stats", "description": "สรุปผลการเทรด + วินัย"},
        {"command": "cancel", "description": "ยกเลิกฟอร์มที่ทำค้างอยู่"},
        {"command": "watch", "description": "เพิ่มหุ้นเข้า watchlist"},
        {"command": "unwatch", "description": "เอาหุ้นออกจาก watchlist"},
        {"command": "watchlist", "description": "ดูราคาหุ้นใน watchlist"},
        {"command": "alert", "description": "ตั้งแจ้งเตือนราคาหุ้น"},
        {"command": "alerts", "description": "ดูแจ้งเตือนที่ตั้งไว้"},
        {"command": "unalert", "description": "ยกเลิกแจ้งเตือน"},
        {"command": "macro", "description": "ดู DXY / US10Y Yield / SET Index"},
        {"command": "cot", "description": "COT Report ตำแหน่งสถาบัน (เช่น /cot XAUUSD)"},
        {"command": "qt", "description": "TDO Bias + โมเดล AMDX/XAMD (Quarterly Theory)"},
        {"command": "size", "description": "คำนวณขนาดโพซิชัน"},
        {"command": "setlimit", "description": "ตั้งวงเงินขาดทุนสูงสุด (daily/weekly)"},
        {"command": "limits", "description": "เช็คสถานะวงเงินขาดทุน"},
        {"command": "help", "description": "ดูเมนูคำสั่งทั้งหมด"},
    ]
    try:
        requests.post(f"{TELEGRAM_API}/setMyCommands", json={"commands": commands}, timeout=10)
    except Exception:
        logger.exception("setup_bot_commands failed")


setup_bot_commands()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
