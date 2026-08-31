import html


def _is_num(v):
    return isinstance(v, (int, float)) and v == v  # v == v excludes NaN


def _rows_table(trades):
    rows = []
    for t in reversed(trades):
        if t.get("status") != "CLOSED":
            continue
        r = t.get("r_multiple")
        r_str = f"{r:+.2f}R" if _is_num(r) else "-"
        css_class = "win" if _is_num(r) and r > 0 else ("loss" if _is_num(r) and r < 0 else "")
        rows.append(
            f"<tr class='{css_class}'>"
            f"<td>#{html.escape(str(t.get('id', '')))}</td>"
            f"<td>{html.escape(str(t.get('side', '')))}</td>"
            f"<td>{html.escape(str(t.get('symbol', '')))}</td>"
            f"<td>{html.escape(str(t.get('entry', '')))}</td>"
            f"<td>{html.escape(str(t.get('exit_price', '')))}</td>"
            f"<td>{r_str}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def build_dashboard_html(trades):
    closed = [t for t in trades if t.get("status") == "CLOSED" and _is_num(t.get("r_multiple"))]

    # Sheet row order == the order trades were logged in, used here as a
    # simple stand-in for chronological order.
    equity = []
    running = 0.0
    for t in closed:
        running += float(t["r_multiple"])
        equity.append(round(running, 3))

    wins = sum(1 for t in closed if t["r_multiple"] > 0)
    losses = sum(1 for t in closed if t["r_multiple"] < 0)
    total_r = sum(t["r_multiple"] for t in closed)
    win_rate = (wins / len(closed) * 100) if closed else 0

    peak = float("-inf")
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        max_dd = min(max_dd, v - peak)

    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    labels = list(range(1, len(equity) + 1))
    table_rows = _rows_table(trades)
    total_r_class = "pos" if total_r >= 0 else "neg"

    disciplined = [t for t in closed if t.get("idm_ok") is True and t.get("turtle_ok") is True]
    undisciplined = [t for t in closed if not (t.get("idm_ok") is True and t.get("turtle_ok") is True)]

    def _win_rate(lst):
        if not lst:
            return None
        wins = sum(1 for t in lst if t["r_multiple"] > 0)
        return wins / len(lst) * 100

    disc_wr = _win_rate(disciplined)
    undisc_wr = _win_rate(undisciplined)
    disc_wr_text = f"{disc_wr:.1f}%" if disc_wr is not None else "-"
    undisc_wr_text = f"{undisc_wr:.1f}%" if undisc_wr is not None else "-"

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trade Dashboard — ize</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 20px; background: #0f1115; color: #e8e8ea;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  h1 {{ font-size: 19px; font-weight: 600; margin: 0 0 18px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 20px; }}
  .card {{ background: #191c22; border: 1px solid #262a33; border-radius: 12px; padding: 14px; }}
  .card .label {{ font-size: 11px; color: #9a9fab; margin-bottom: 6px; }}
  .card .value {{ font-size: 20px; font-weight: 700; }}
  .value.pos {{ color: #4ade80; }}
  .value.neg {{ color: #f87171; }}
  .box {{ background: #191c22; border: 1px solid #262a33; border-radius: 12px; padding: 14px; margin-bottom: 20px; overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; min-width: 420px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #262a33; white-space: nowrap; }}
  th {{ color: #9a9fab; font-weight: 500; }}
  tr.win td:last-child {{ color: #4ade80; font-weight: 600; }}
  tr.loss td:last-child {{ color: #f87171; font-weight: 600; }}
  .section-title {{ font-size: 12px; color: #9a9fab; margin: 0 0 12px; text-transform: uppercase; letter-spacing: 0.04em; }}
</style>
</head>
<body>
  <h1>📊 Trade Dashboard — ize</h1>

  <div class="cards">
    <div class="card"><div class="label">Total R</div><div class="value {total_r_class}">{total_r:+.2f}R</div></div>
    <div class="card"><div class="label">Win rate</div><div class="value">{win_rate:.1f}%</div></div>
    <div class="card"><div class="label">ปิดแล้ว</div><div class="value">{len(closed)}</div></div>
    <div class="card"><div class="label">ชนะ / แพ้</div><div class="value">{wins} / {losses}</div></div>
    <div class="card"><div class="label">Max Drawdown</div><div class="value neg">{max_dd:.2f}R</div></div>
    <div class="card"><div class="label">ไม้เปิดอยู่</div><div class="value">{len(open_trades)}</div></div>
    <div class="card"><div class="label">Win rate (ทำครบระบบ)</div><div class="value">{disc_wr_text}</div></div>
    <div class="card"><div class="label">Win rate (ทำไม่ครบ)</div><div class="value">{undisc_wr_text}</div></div>
  </div>

  <div class="box">
    <div class="section-title">Equity Curve (สะสมเป็น R)</div>
    <canvas id="equityChart" height="100"></canvas>
  </div>

  <div class="box">
    <div class="section-title">ประวัติการเทรด</div>
    <table>
      <thead><tr><th>#</th><th>Side</th><th>Symbol</th><th>Entry</th><th>Exit</th><th>ผล</th></tr></thead>
      <tbody>
        {table_rows if table_rows else '<tr><td colspan="6">ยังไม่มีไม้ที่ปิดแล้ว</td></tr>'}
      </tbody>
    </table>
  </div>

<script>
  new Chart(document.getElementById('equityChart'), {{
    type: 'line',
    data: {{
      labels: {labels},
      datasets: [{{
        label: 'Equity (R)',
        data: {equity},
        borderColor: '#60a5fa',
        backgroundColor: 'rgba(96,165,250,0.12)',
        fill: true,
        tension: 0.2,
        pointRadius: 2,
      }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ grid: {{ color: '#262a33' }}, ticks: {{ color: '#9a9fab' }} }},
        y: {{ grid: {{ color: '#262a33' }}, ticks: {{ color: '#9a9fab' }} }},
      }}
    }}
  }});
</script>
</body>
</html>"""
