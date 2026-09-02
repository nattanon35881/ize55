import logging

import requests

logger = logging.getLogger(__name__)

CFTC_DISAGG_FUTURES_ONLY = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"

# Maps our bot's symbols to a search term expected inside CFTC's
# "market_and_exchange_names" field. Kept as a substring search (not an
# exact match) on purpose — the exact market-name strings couldn't be
# verified against the live API from this sandbox (outbound network is
# domain-restricted here), so matching loosely + picking the
# highest-open-interest candidate is the robust choice.
COT_MARKET_SEARCH = {
    "XAUUSD": "GOLD",
    "GOLD": "GOLD",
    "XAGUSD": "SILVER",
    "SILVER": "SILVER",
    "EURUSD": "EURO FX",
    "GBPUSD": "BRITISH POUND",
    "AUDUSD": "AUSTRALIAN DOLLAR",
    "USDCAD": "CANADIAN DOLLAR",
    "USOIL": "WTI",
    "WTI": "WTI",
    "US100": "NASDAQ-100",
    "NAS100": "NASDAQ-100",
    "US500": "S&P 500",
    "SPX500": "S&P 500",
    "US30": "DOW JONES",
}

# The scheduled weekly digest covers these symbols by default — add more
# to this list any time.
WEEKLY_COT_SYMBOLS = ["XAUUSD"]


def resolve_cot_search_term(symbol):
    return COT_MARKET_SEARCH.get(symbol.strip().upper())


def _find_key(row, *substrings):
    subs = [s.lower() for s in substrings]
    for key in row.keys():
        kl = key.lower()
        if all(s in kl for s in subs):
            return key
    return None


def _get_num(row, *substrings):
    key = _find_key(row, *substrings)
    if key is None:
        return None
    try:
        return float(row[key])
    except (TypeError, ValueError):
        return None


def fetch_cot_history(search_term, weeks=104):
    """Fetch up to the last `weeks` weekly reports for whichever market
    best matches search_term. A loose substring match can hit more than
    one contract (e.g. 'GOLD' also matches 'GOLD KILO') — the one with
    the highest open interest on the latest date is reliably the main,
    most-liquid contract, so that's the one kept.
    """
    try:
        resp = requests.get(
            CFTC_DISAGG_FUTURES_ONLY,
            params={
                "$where": f"upper(market_and_exchange_names) like '%{search_term.upper()}%'",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": 1000,
            },
            timeout=20,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.exception("CFTC fetch failed for %s", search_term)
        return None, None

    if not rows:
        return None, None

    latest_date = rows[0].get("report_date_as_yyyy_mm_dd")
    latest_candidates = [r for r in rows if r.get("report_date_as_yyyy_mm_dd") == latest_date]
    if not latest_candidates:
        return None, None
    best = max(latest_candidates, key=lambda r: _get_num(r, "open_interest", "all") or 0)
    market_name = best.get("market_and_exchange_names")

    history = [r for r in rows if r.get("market_and_exchange_names") == market_name]
    history.sort(key=lambda r: r.get("report_date_as_yyyy_mm_dd", ""))
    return market_name, history[-weeks:]


def compute_cot_snapshot(symbol):
    search_term = resolve_cot_search_term(symbol)
    if not search_term:
        return None

    market_name, history = fetch_cot_history(search_term)
    if not history:
        return None

    def net_of(row):
        long_ = _get_num(row, "m_money", "long")
        short_ = _get_num(row, "m_money", "short")
        if long_ is None or short_ is None:
            return None
        return long_ - short_

    nets = [n for n in (net_of(r) for r in history) if n is not None]
    if not nets:
        return None

    latest = history[-1]
    prior = history[-2] if len(history) > 1 else None
    latest_net = net_of(latest)
    prior_net = net_of(prior) if prior else None
    change = (latest_net - prior_net) if (latest_net is not None and prior_net is not None) else None
    percentile = (
        (sum(1 for n in nets if n <= latest_net) / len(nets)) * 100 if latest_net is not None else None
    )

    comm_long = _get_num(latest, "prod_merc", "long") or _get_num(latest, "comm_positions", "long")
    comm_short = _get_num(latest, "prod_merc", "short") or _get_num(latest, "comm_positions", "short")
    comm_net = (comm_long - comm_short) if (comm_long is not None and comm_short is not None) else None

    return {
        "market_name": market_name,
        "report_date": str(latest.get("report_date_as_yyyy_mm_dd", ""))[:10],
        "mm_long": _get_num(latest, "m_money", "long"),
        "mm_short": _get_num(latest, "m_money", "short"),
        "mm_net": latest_net,
        "mm_net_change": change,
        "percentile": percentile,
        "weeks_in_percentile": len(nets),
        "comm_net": comm_net,
    }


def build_cot_report(symbol):
    data = compute_cot_snapshot(symbol)
    if not data:
        return (
            f"❌ ดึงข้อมูล COT ของ {symbol.upper()} ไม่ได้ครับ "
            "(อาจไม่มี futures ตัวนี้ใน CFTC หรือ API มีปัญหาชั่วคราว — "
            "ใช้ได้กับทอง/เงิน/น้ำมัน/คู่เงินหลัก/ดัชนีหลักเท่านั้น ไม่รองรับหุ้นไทยรายตัว)"
        )

    lines = [
        f"🏛️ <b>COT Report — {data['market_name']}</b>",
        f"สัปดาห์ล่าสุด: {data['report_date']}",
        "",
        "<b>Managed Money (กองทุนเก็งกำไร):</b>",
    ]
    if data["mm_long"] is not None and data["mm_short"] is not None:
        lines.append(f"  Long: {data['mm_long']:,.0f} | Short: {data['mm_short']:,.0f}")
    if data["mm_net"] is not None:
        change_text = ""
        if data["mm_net_change"] is not None:
            arrow = "📈" if data["mm_net_change"] >= 0 else "📉"
            change_text = f" ({arrow} {data['mm_net_change']:+,.0f} จากสัปดาห์ก่อน)"
        lines.append(f"  Net: {data['mm_net']:+,.0f}{change_text}")

    if data["percentile"] is not None:
        pct = data["percentile"]
        note = ""
        if pct >= 90:
            note = "\n⚠️ ใกล้จุดสูงสุดในรอบที่ดู — ฝูงชนแน่นข้าง Long มาก ระวังกลับตัว"
        elif pct <= 10:
            note = "\n⚠️ ใกล้จุดต่ำสุดในรอบที่ดู — ฝูงชนแน่นข้าง Short มาก ระวังกลับตัว"
        lines.append(
            f"\n📊 ตำแหน่งตอนนี้อยู่เปอร์เซ็นไทล์ที่ {pct:.0f} "
            f"ของ {data['weeks_in_percentile']} สัปดาห์ล่าสุด{note}"
        )

    if data["comm_net"] is not None:
        lines.append(f"\n<b>Commercial/Producer:</b> Net {data['comm_net']:+,.0f}")

    return "\n".join(lines)


def build_cot_weekly_digest():
    parts = [build_cot_report(sym) for sym in WEEKLY_COT_SYMBOLS]
    return "\n\n".join(parts)
