import logging

import yfinance as yf

logger = logging.getLogger(__name__)

MACRO_TICKERS = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "SET": "^SET.BK",
}

LABELS_TH = {
    "DXY": "ดัชนีดอลลาร์ (DXY)",
    "US10Y": "บอนด์ยิลด์ 10 ปีสหรัฐฯ",
    "SET": "SET Index",
}


def _get_series(ticker_symbol, period="6mo"):
    try:
        hist = yf.Ticker(ticker_symbol).history(period=period, interval="1d")
    except Exception:
        return None
    if hist is None or hist.empty:
        return None
    return hist["Close"].dropna()


def get_macro_snapshot():
    snapshot = {}
    for key, symbol in MACRO_TICKERS.items():
        series = _get_series(symbol)
        if series is None or series.empty:
            snapshot[key] = None
            continue
        latest = float(series.iloc[-1])
        prev = float(series.iloc[-2]) if len(series) > 1 else None
        change_pct = ((latest - prev) / prev) if prev else None
        snapshot[key] = {"price": latest, "change_pct": change_pct, "series": series}
    return snapshot


def build_macro_report():
    snapshot = get_macro_snapshot()
    lines = ["🌐 <b>Macro Snapshot</b>"]
    for key in ["DXY", "US10Y", "SET"]:
        data = snapshot.get(key)
        label = LABELS_TH[key]
        if not data:
            lines.append(f"{label}: ดึงข้อมูลไม่ได้")
            continue
        change = data["change_pct"]
        arrow = "🟢" if (change or 0) >= 0 else "🔴"
        pct = f"{change * 100:+.2f}%" if change is not None else "-"
        suffix = "%" if key == "US10Y" else ""
        lines.append(f"{label}: {data['price']:,.2f}{suffix} {arrow} {pct} (1D)")
    return "\n".join(lines)


def build_macro_dashboard_html():
    snapshot = get_macro_snapshot()
    colors = {"DXY": "#60a5fa", "US10Y": "#f59e0b", "SET": "#4ade80"}

    labels = None
    datasets_js = []
    cards_html = []

    for key in ["DXY", "US10Y", "SET"]:
        data = snapshot.get(key)
        label = LABELS_TH[key]
        if not data:
            cards_html.append(
                f'<div class="card"><div class="label">{label}</div><div class="value">-</div></div>'
            )
            continue

        series = data["series"]
        if labels is None:
            labels = [d.strftime("%d %b") for d in series.index]
        base = float(series.iloc[0])
        normalized = [round((v / base - 1) * 100, 3) for v in series.tolist()]
        color = colors[key]
        datasets_js.append(
            f"{{label:'{label}', data:{normalized}, borderColor:'{color}', "
            f"backgroundColor:'transparent', tension:0.15, pointRadius:0, borderWidth:2}}"
        )

        change = data["change_pct"]
        arrow_class = "pos" if (change or 0) >= 0 else "neg"
        pct = f"{change * 100:+.2f}%" if change is not None else "-"
        suffix = "%" if key == "US10Y" else ""
        cards_html.append(
            f'<div class="card"><div class="label">{label}</div>'
            f'<div class="value">{data["price"]:,.2f}{suffix}</div>'
            f'<div class="change {arrow_class}">{pct} (1D)</div></div>'
        )

    labels_js = labels if labels else []
    datasets_joined = ",\n      ".join(datasets_js)

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Macro Dashboard — ize</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; padding:20px; background:#0f1115; color:#e8e8ea; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  h1 {{ font-size:19px; font-weight:600; margin:0 0 6px; }}
  p.sub {{ color:#9a9fab; font-size:12px; margin:0 0 18px; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:20px; }}
  .card {{ background:#191c22; border:1px solid #262a33; border-radius:12px; padding:14px; }}
  .card .label {{ font-size:11px; color:#9a9fab; margin-bottom:6px; }}
  .card .value {{ font-size:20px; font-weight:700; }}
  .card .change {{ font-size:12px; margin-top:4px; }}
  .change.pos {{ color:#4ade80; }}
  .change.neg {{ color:#f87171; }}
  .box {{ background:#191c22; border:1px solid #262a33; border-radius:12px; padding:14px; }}
  .section-title {{ font-size:12px; color:#9a9fab; margin:0 0 12px; text-transform:uppercase; letter-spacing:0.04em; }}
</style>
</head>
<body>
  <h1>🌐 Macro Dashboard — ize</h1>
  <p class="sub">DXY, US 10Y Yield, SET Index — เทียบเป็น % เปลี่ยนแปลงจากจุดเริ่มต้นของช่วง 6 เดือน เพื่อดูความสัมพันธ์กันได้ง่ายแม้สเกลราคาต่างกันมาก</p>

  <div class="cards">
    {''.join(cards_html)}
  </div>

  <div class="box">
    <div class="section-title">% เปลี่ยนแปลงเทียบกัน (6 เดือน)</div>
    <canvas id="macroChart" height="110"></canvas>
  </div>

<script>
  new Chart(document.getElementById('macroChart'), {{
    type: 'line',
    data: {{
      labels: {labels_js},
      datasets: [
      {datasets_joined}
      ]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ display: true, labels: {{ color: '#e8e8ea' }} }} }},
      scales: {{
        x: {{ grid: {{ color: '#262a33' }}, ticks: {{ color: '#9a9fab', maxTicksLimit: 10 }} }},
        y: {{ grid: {{ color: '#262a33' }}, ticks: {{ color: '#9a9fab', callback: function(v) {{ return v + '%'; }} }} }},
      }}
    }}
  }});
</script>
</body>
</html>"""
