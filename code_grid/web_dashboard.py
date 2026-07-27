"""
web_dashboard.py
================
FastAPI web server that serves a live V2G monitoring dashboard on port 8080.

Endpoints
---------
GET  /          HTML dashboard page
WS   /ws        WebSocket — pushes JSON state every 500 ms
POST /api/control  Set grid demand mode {"action": "auto"|"v2g"|"charge"}

grid_state (code_grid.grid_state) is written by:
  - iec104_panda.py  (IEC 104 readings, grid load-flow results, timing)
  - ocpp_central_system_2.py  (OCPP MeterValues telemetry)
"""

import asyncio
import re
import subprocess
import time

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from code_grid.grid_state import grid_state
from code_grid.perf_logger import perf_logger
from config import Config


def _cert_expiry(cert_path: str) -> str:
    """Return 'YYYY-MM-DD' expiry of a PEM cert, or '' if unreadable."""
    try:
        r = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
            capture_output=True, text=True, timeout=3,
        )
        # Output: "notAfter=Jun 20 17:46:09 2028 GMT"
        m = re.search(r"notAfter=(.*)", r.stdout)
        if m:
            from datetime import datetime
            return datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z").strftime("%Y-%m-%d")
    except Exception:
        pass
    return ""


def _init_security_state() -> None:
    """Populate static security fields from cert files. Called once at startup."""
    sec = grid_state.security

    # OCPP — cert on the grid Pi (CSMS server cert)
    ocpp_expiry = _cert_expiry(Config.OCPP_CSMS_CERT)
    sec.ocpp.configured   = bool(ocpp_expiry)
    sec.ocpp.cert_expiry  = ocpp_expiry

    # IEC 104 — cert on the grid Pi (client cert)
    iec_expiry = _cert_expiry(Config.IEC104_CLIENT_CERT)
    sec.iec104.configured  = bool(iec_expiry)
    sec.iec104.cert_expiry = iec_expiry
    if sec.iec104.configured:
        sec.iec104.tls_version = "TLS 1.2+"   # c104 doesn't expose live session info

    # ISO 15118 — TLS is mandated by the standard; cert lives on the charger Pi
    sec.iso15118.configured  = True   # always required — no cert to read from here
    sec.iso15118.tls_version = "TLS 1.2"
    sec.iso15118.cipher      = "Protocol-mandated"


_init_security_state()

app = FastAPI(title="V2G Grid Dashboard")

# ──────────────────────────────────────────────────────────────────────────────
#  Dashboard HTML (inline so no static-file paths are needed on the Pi)
# ──────────────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>V2G Grid Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg:      #0d1117;
      --surface: #161b22;
      --border:  #30363d;
      --text:    #c9d1d9;
      --muted:   #6e7681;
      --green:   #3fb950;
      --orange:  #d29922;
      --red:     #f85149;
      --blue:    #58a6ff;
      --purple:  #bc8cff;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 1rem;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0.75rem 1.25rem;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      margin-bottom: 1rem;
    }
    header h1 { font-size: 1rem; font-weight: 600; letter-spacing: 0.02em; }
    .indicators { display: flex; gap: 1.5rem; align-items: center; }
    .indicator { display: flex; align-items: center; gap: 0.4rem; font-size: 0.78rem; color: var(--muted); }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); transition: background 0.4s; }
    .dot.ok   { background: var(--green); }
    .dot.warn { background: var(--orange); }
    .dot.err  { background: var(--red); }

    .dashboard {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 1rem;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem 1.25rem;
    }
    .card.span2 { grid-column: span 2; }
    .card.span3 { grid-column: 1 / -1; }
    .card-label {
      font-size: 0.68rem;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.09em;
      margin-bottom: 0.9rem;
    }

    /* ── Power Flow ── */
    .power-row { display: flex; align-items: baseline; gap: 0.4rem; }
    .power-num { font-size: 3rem; font-weight: 700; line-height: 1; transition: color 0.3s; font-variant-numeric: tabular-nums; }
    .power-unit { font-size: 1.1rem; color: var(--muted); }
    .power-dir { margin-top: 0.5rem; font-size: 0.85rem; display: flex; align-items: center; gap: 0.3rem; }
    .power-ocpp { margin-top: 0.5rem; font-size: 0.75rem; color: var(--muted); }

    /* ── SoC ── */
    .soc-row { display: flex; align-items: baseline; gap: 0.25rem; }
    .soc-num { font-size: 2.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }
    .soc-pct { font-size: 1.2rem; color: var(--muted); }
    .soc-bar-bg { margin-top: 0.9rem; height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
    .soc-bar-fill { height: 100%; background: var(--green); border-radius: 4px; transition: width 0.5s, background 0.3s; }
    .soc-sub { margin-top: 0.6rem; font-size: 0.75rem; color: var(--muted); display: flex; gap: 1rem; }

    /* ── Grid Health ── */
    .metric-row {
      display: flex; justify-content: space-between; align-items: center;
      padding: 0.35rem 0;
      border-bottom: 1px solid var(--border);
      font-size: 0.85rem;
    }
    .metric-row:last-child { border-bottom: none; }
    .metric-label { color: var(--muted); }
    .metric-val { font-weight: 600; font-variant-numeric: tabular-nums; transition: color 0.3s; }

    /* ── Timing ── */
    .timing-item { margin-bottom: 0.55rem; }
    .timing-header { display: flex; justify-content: space-between; font-size: 0.75rem; margin-bottom: 0.2rem; }
    .timing-name { color: var(--muted); }
    .timing-ms { font-variant-numeric: tabular-nums; font-weight: 500; }
    .timing-bg { height: 5px; background: var(--border); border-radius: 3px; overflow: hidden; }
    .timing-fill { height: 100%; border-radius: 3px; transition: width 0.4s; }
    .timing-total {
      margin-top: 0.75rem; padding-top: 0.75rem;
      border-top: 1px solid var(--border);
      display: flex; justify-content: space-between;
      font-size: 0.85rem;
    }
    .timing-total-val { font-weight: 700; font-variant-numeric: tabular-nums; }
    .timing-sub { margin-top: 0.6rem; font-size: 0.72rem; color: var(--muted); }

    /* ── Chart ── */
    .chart-wrap { position: relative; height: 190px; }

    /* ── Command Log ── */
    .log-scroll { max-height: 220px; overflow-y: auto; }
    .log-entry {
      display: flex; gap: 0.5rem; align-items: center;
      padding: 0.3rem 0;
      border-bottom: 1px solid var(--border);
      font-size: 0.78rem;
    }
    .log-entry:last-child { border-bottom: none; }
    .log-time { color: var(--muted); font-family: monospace; min-width: 58px; }
    .badge {
      font-size: 0.62rem; padding: 0.1rem 0.35rem;
      border-radius: 3px; font-weight: 700; text-transform: uppercase;
    }
    .badge-higher { background: rgba(248,81,73,0.15); color: var(--red); }
    .badge-lower  { background: rgba(63,185,80,0.15);  color: var(--green); }
    .badge-auto   { background: rgba(88,166,255,0.1);  color: var(--blue); }
    .badge-manual { background: rgba(188,140,255,0.1); color: var(--purple); }

    /* ── Security ── */
    .sec-row {
      display: grid;
      grid-template-columns: 1.4rem 1fr auto;
      align-items: center;
      gap: 0 0.6rem;
      padding: 0.5rem 0;
      border-bottom: 1px solid var(--border);
      font-size: 0.82rem;
    }
    .sec-row:last-child { border-bottom: none; }
    .sec-lock { font-size: 1rem; }
    .sec-proto { font-weight: 600; color: var(--text); font-size: 0.82rem; }
    .sec-ver   { font-size: 0.72rem; font-weight: 600; }
    .sec-auth  { color: var(--muted); font-size: 0.7rem; margin-top: 0.1rem; }
    .sec-expiry { font-size: 0.7rem; color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }

    /* ── Manual Control ── */
    .ctrl-desc { font-size: 0.8rem; color: var(--muted); line-height: 1.6; margin-bottom: 0.9rem; }
    .ctrl-buttons { display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 0.9rem; }
    .ctrl-btn {
      padding: 0.55rem 1.2rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg);
      color: var(--text);
      font-size: 0.875rem;
      cursor: pointer;
      transition: all 0.2s;
      font-weight: 500;
    }
    .ctrl-btn:hover { filter: brightness(1.3); }
    .ctrl-btn.active-auto   { border-color: var(--blue);   color: var(--blue);   background: rgba(88,166,255,0.1); font-weight: 700; }
    .ctrl-btn.active-v2g    { border-color: var(--red);    color: var(--red);    background: rgba(248,81,73,0.1);  font-weight: 700; }
    .ctrl-btn.active-charge { border-color: var(--green);  color: var(--green);  background: rgba(63,185,80,0.1);  font-weight: 700; }
    .ctrl-btn.active-vstab  { border-color: var(--purple); color: var(--purple); background: rgba(188,140,255,0.1); font-weight: 700; }
    .ctrl-status {
      font-size: 0.78rem; padding: 0.4rem 0.8rem;
      border-radius: 4px; border: 1px solid transparent;
      transition: all 0.3s;
    }
    .ctrl-status.mode-auto   { background: rgba(88,166,255,0.08); color: var(--blue);   border-color: rgba(88,166,255,0.2); }
    .ctrl-status.mode-v2g    { background: rgba(248,81,73,0.08);  color: var(--red);    border-color: rgba(248,81,73,0.2);  }
    .ctrl-status.mode-charge { background: rgba(63,185,80,0.08);  color: var(--green);  border-color: rgba(63,185,80,0.2);  }
    .ctrl-status.mode-vstab  { background: rgba(188,140,255,0.08); color: var(--purple); border-color: rgba(188,140,255,0.2); }

    @media (max-width: 800px) {
      .dashboard { grid-template-columns: 1fr; }
      .card.span2 { grid-column: span 1; }
    }
  </style>
</head>
<body>

<header>
  <h1>V2G Grid Dashboard</h1>
  <div class="indicators">
    <div class="indicator"><div id="dot-ws"   class="dot"></div>WebSocket</div>
    <div class="indicator"><div id="dot-iec"  class="dot"></div>IEC&nbsp;104</div>
    <div class="indicator"><div id="dot-ocpp" class="dot"></div>OCPP</div>
  </div>
</header>

<div class="dashboard">

  <!-- Power Flow -->
  <div class="card">
    <div class="card-label">Power Flow (IEC 104)</div>
    <div class="power-row">
      <div id="power-num" class="power-num">—</div>
      <div class="power-unit">kW</div>
    </div>
    <div id="power-dir" class="power-dir" style="color:var(--muted)">Waiting…</div>
    <div class="power-ocpp">OCPP reported: <span id="ocpp-power">—</span> W
      &nbsp;|&nbsp; Energy: <span id="ocpp-energy">—</span> Wh</div>
  </div>

  <!-- SoC -->
  <div class="card">
    <div class="card-label">State of Charge</div>
    <div class="soc-row">
      <div id="soc-num" class="soc-num">—</div>
      <div class="soc-pct">%</div>
    </div>
    <div class="soc-bar-bg"><div id="soc-bar" class="soc-bar-fill" style="width:0%"></div></div>
    <div class="soc-sub">
      <span>OCPP SoC: <strong id="ocpp-soc">—</strong>%</span>
      <span>Temp: <strong id="temp-val">—</strong>°C</span>
    </div>
  </div>

  <!-- Grid Health -->
  <div class="card">
    <div class="card-label">Grid Health (PandaPower)</div>
    <div class="metric-row">
      <span class="metric-label">Bus 2 Voltage</span>
      <span id="g-voltage" class="metric-val">—</span>
    </div>
    <div class="metric-row">
      <span class="metric-label">Trafo Loading</span>
      <span id="g-trafo" class="metric-val">—</span>
    </div>
    <div class="metric-row">
      <span class="metric-label">Line Loading</span>
      <span id="g-line" class="metric-val">—</span>
    </div>
    <div class="metric-row">
      <span class="metric-label">IEC 104 Data Age</span>
      <span id="iec-age" class="metric-val">—</span>
    </div>
    <div class="metric-row">
      <span class="metric-label">OCPP Data Age</span>
      <span id="ocpp-age" class="metric-val">—</span>
    </div>
    <div id="vstab-rows" style="display:none">
      <div class="metric-row" style="margin-top:0.5rem;border-top:1px solid var(--border);padding-top:0.5rem">
        <span class="metric-label" style="color:var(--purple)">Voltage Target</span>
        <span class="metric-val" style="color:var(--purple)">0.975 pu</span>
      </div>
      <div class="metric-row">
        <span class="metric-label" style="color:var(--purple)">Sim Background Load</span>
        <span id="vstab-bg-load" class="metric-val" style="color:var(--purple)">0.0 kW</span>
      </div>
    </div>
  </div>

  <!-- Power History chart (2 cols) -->
  <div class="card span2">
    <div class="card-label">Power History — last 60 s (IEC 104 kW)</div>
    <div class="chart-wrap"><canvas id="power-chart"></canvas></div>
  </div>

  <!-- Protocol Timing -->
  <div class="card">
    <div class="card-label">Protocol Timing</div>

    <div class="timing-item">
      <div class="timing-header">
        <span class="timing-name">IEC104 Read</span>
        <span id="t-read" class="timing-ms">—</span>
      </div>
      <div class="timing-bg"><div id="tb-read" class="timing-fill" style="width:0%;background:var(--blue)"></div></div>
    </div>

    <div class="timing-item">
      <div class="timing-header">
        <span class="timing-name">PandaPower compute</span>
        <span id="t-compute" class="timing-ms">—</span>
      </div>
      <div class="timing-bg"><div id="tb-compute" class="timing-fill" style="width:0%;background:var(--purple)"></div></div>
    </div>

    <div class="timing-item">
      <div class="timing-header">
        <span class="timing-name">IEC104 Transmit</span>
        <span id="t-transmit" class="timing-ms">—</span>
      </div>
      <div class="timing-bg"><div id="tb-transmit" class="timing-fill" style="width:0%;background:var(--orange)"></div></div>
    </div>

    <div class="timing-total">
      <span>Read + Compute cycle</span>
      <span id="t-cycle" class="timing-total-val">—</span>
    </div>
    <div class="timing-sub">Transmit runs every 4 s independent of the 1 s read cycle.</div>
  </div>

  <!-- ISO 15118 Stats (2 cols) -->
  <div class="card span2">
    <div class="card-label">ISO 15118 Charge Loop <span id="iso-age" style="font-size:0.72rem;color:var(--muted);font-weight:400;margin-left:0.5rem">—</span></div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.5rem 1.2rem;margin-bottom:0.6rem">
      <div class="metric-row">
        <span class="metric-label">EV Voltage</span>
        <span id="iso-voltage" class="metric-val">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">EV Current</span>
        <span id="iso-current" class="metric-val">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">EV Power (V×I)</span>
        <span id="iso-power" class="metric-val">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">EVSE Max Charge</span>
        <span id="iso-charge-lim" class="metric-val">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">EVSE Max Discharge</span>
        <span id="iso-discharge-lim" class="metric-val">—</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">Loop Time</span>
        <span id="iso-loop-ms" class="metric-val">—</span>
      </div>
    </div>
  </div>

  <!-- Security Status (1 col — sits in col 3 next to ISO 15118) -->
  <div class="card">
    <div class="card-label">Security Status</div>
    <div id="sec-rows">
      <div class="sec-row" id="sec-ocpp">
        <span class="sec-lock">&#128274;</span>
        <div>
          <div class="sec-proto">OCPP 2.1</div>
          <div class="sec-auth">mTLS &middot; Profile&nbsp;3</div>
        </div>
        <div style="text-align:right">
          <div class="sec-ver" id="sec-ocpp-ver">—</div>
          <div class="sec-expiry" id="sec-ocpp-exp">—</div>
        </div>
      </div>
      <div class="sec-row" id="sec-iec104">
        <span class="sec-lock">&#128274;</span>
        <div>
          <div class="sec-proto">IEC 104</div>
          <div class="sec-auth">mTLS &middot; IEC&nbsp;62351-3</div>
        </div>
        <div style="text-align:right">
          <div class="sec-ver" id="sec-iec-ver">—</div>
          <div class="sec-expiry" id="sec-iec-exp">—</div>
        </div>
      </div>
      <div class="sec-row" id="sec-iso">
        <span class="sec-lock">&#128274;</span>
        <div>
          <div class="sec-proto">ISO 15118</div>
          <div class="sec-auth">V2G PKI</div>
        </div>
        <div style="text-align:right">
          <div class="sec-ver" id="sec-iso-ver">—</div>
          <div class="sec-expiry" id="sec-iso-exp">—</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Billing (2 cols — cols 1-2) -->
  <div class="card span2">
    <div class="card-label">Session Billing</div>
    <div class="ctrl-desc">
      Set your energy tariff rates. Charge cost and V2G credit are calculated from OCPP metered energy this session.
    </div>
    <div style="display:flex;align-items:flex-end;gap:1rem;margin-bottom:1rem;flex-wrap:wrap">
      <div style="display:flex;flex-direction:column;gap:0.3rem">
        <label class="metric-label" for="tariff-charge">Charge rate (p/kWh)</label>
        <input id="tariff-charge" type="number" min="0" step="0.1" value="28.0"
               style="width:130px;padding:0.35rem 0.5rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:0.95rem">
      </div>
      <div style="display:flex;flex-direction:column;gap:0.3rem">
        <label class="metric-label" for="tariff-v2g">V2G export rate (p/kWh)</label>
        <input id="tariff-v2g" type="number" min="0" step="0.1" value="15.0"
               style="width:130px;padding:0.35rem 0.5rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:0.95rem">
      </div>
      <button class="ctrl-btn" onclick="saveTariff()">&#10003; Apply</button>
      <span id="tariff-status" style="font-size:0.85rem;color:var(--green)"></span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:0.5rem 1.2rem">
      <div class="metric-row" style="flex-direction:column;align-items:flex-start;gap:0.15rem;padding:0.6rem 0">
        <span class="metric-label">Energy Charged</span>
        <span id="bill-charge-kwh" class="metric-val" style="font-size:1.4rem;color:var(--green)">—</span>
        <span id="bill-charge-cost" style="font-size:0.8rem;color:var(--muted)">£—</span>
      </div>
      <div class="metric-row" style="flex-direction:column;align-items:flex-start;gap:0.15rem;padding:0.6rem 0">
        <span class="metric-label">V2G Energy Exported</span>
        <span id="bill-v2g-kwh" class="metric-val" style="font-size:1.4rem;color:var(--red)">—</span>
        <span id="bill-v2g-credit" style="font-size:0.8rem;color:var(--muted)">£—</span>
      </div>
      <div class="metric-row" style="flex-direction:column;align-items:flex-start;gap:0.15rem;padding:0.6rem 0">
        <span class="metric-label">Net Session Cost</span>
        <span id="bill-net" class="metric-val" style="font-size:1.4rem">—</span>
        <span style="font-size:0.75rem;color:var(--muted)">charge cost − V2G credit</span>
      </div>
    </div>
  </div>

  <!-- Command Log (1 col — col 3, same row as Billing) -->
  <div class="card">
    <div class="card-label">Transmitted Command Log</div>
    <div id="log-list" class="log-scroll"></div>
  </div>

  <!-- Manual Control (2 cols) -->
  <div class="card span2">
    <div class="card-label">Grid Demand Control</div>
    <div class="ctrl-desc">
      Override the automatic PandaPower-based control.
      <strong>Force V2G</strong> sends a continuous LOWER step command — the charger reduces charge or starts discharging back to the grid.
      <strong>Force Charge</strong> sends HIGHER — the charger increases charge power.
      Return to <strong>Auto</strong> to restore load-flow–driven control.
    </div>
    <div class="ctrl-buttons">
      <button id="btn-auto"   class="ctrl-btn" onclick="setControl('auto')">&#9679; Auto</button>
      <button id="btn-v2g"    class="ctrl-btn" onclick="setControl('v2g')">&#8593; Force V2G Demand</button>
      <button id="btn-charge" class="ctrl-btn" onclick="setControl('charge')">&#8595; Force Charge</button>
      <button id="btn-vstab"  class="ctrl-btn" onclick="setControl('voltage_stab')">&#9889; Voltage Stabilisation</button>
    </div>
    <div id="ctrl-status" class="ctrl-status mode-auto">Mode: Auto — grid-controlled via PandaPower load-flow</div>
  </div>

  <!-- User Preferences -->
  <div class="card span2">
    <div class="card-label">User Preferences</div>
    <div class="ctrl-desc">
      Set SoC limits and departure time. Saved preferences are pushed to the charger via OCPP <strong>SetVariables</strong>.
      The auto-control logic will respect these thresholds in real time.
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.8rem 1.2rem;margin-bottom:0.8rem">
      <div class="metric-row" style="flex-direction:column;align-items:flex-start;gap:0.3rem">
        <label class="metric-label" for="pref-min-soc">Min SoC (V2G floor) %</label>
        <input id="pref-min-soc" type="number" min="0" max="100" step="1" value="20"
               style="width:100%;padding:0.35rem 0.5rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:0.95rem">
      </div>
      <div class="metric-row" style="flex-direction:column;align-items:flex-start;gap:0.3rem">
        <label class="metric-label" for="pref-max-soc">Max SoC (charge cap) %</label>
        <input id="pref-max-soc" type="number" min="0" max="100" step="1" value="80"
               style="width:100%;padding:0.35rem 0.5rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:0.95rem">
      </div>
      <div class="metric-row" style="flex-direction:column;align-items:flex-start;gap:0.3rem">
        <label class="metric-label" for="pref-target-soc">Target SoC at departure %</label>
        <input id="pref-target-soc" type="number" min="0" max="100" step="1" value="80"
               style="width:100%;padding:0.35rem 0.5rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:0.95rem">
      </div>
      <div class="metric-row" style="flex-direction:column;align-items:flex-start;gap:0.3rem">
        <label class="metric-label" for="pref-departure">Departure time</label>
        <input id="pref-departure" type="time" value=""
               style="width:100%;padding:0.35rem 0.5rem;background:var(--surface-2);border:1px solid var(--border);border-radius:6px;color:var(--fg);font-size:0.95rem">
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:1rem">
      <button class="ctrl-btn" onclick="savePreferences()">&#10003; Save &amp; Send to Charger</button>
      <span id="pref-status" style="font-size:0.85rem;color:var(--green)"></span>
    </div>
  </div>


  <!-- Performance Statistics (full width) -->
  <div class="card span3">
    <div class="card-label">Performance Statistics
      <span id="perf-session" style="font-weight:400;color:var(--muted);margin-left:0.5rem;font-size:0.68rem"></span>
      <span style="margin-left:auto;display:inline-flex;gap:0.5rem;float:right">
        <a id="dl-iec104"   href="/api/perf/csv/iec104"   download style="font-size:0.7rem;color:var(--blue);text-decoration:none">&#11015; IEC 104 CSV</a>
        <a id="dl-ocpp"     href="/api/perf/csv/ocpp"     download style="font-size:0.7rem;color:var(--blue);text-decoration:none">&#11015; OCPP CSV</a>
        <a id="dl-iso15118" href="/api/perf/csv/iso15118" download style="font-size:0.7rem;color:var(--blue);text-decoration:none">&#11015; ISO 15118 CSV</a>
      </span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.4rem 1.5rem">

      <!-- IEC 104 Latency -->
      <div>
        <div style="font-size:0.72rem;color:var(--muted);font-weight:600;margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.07em">IEC 104 Latency</div>
        <div class="metric-row"><span class="metric-label">Transmit mean</span><span id="ps-tx-mean" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Transmit p95</span><span id="ps-tx-p95" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">PandaPower mean</span><span id="ps-pp-mean" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">PandaPower p95</span><span id="ps-pp-p95" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Command success</span><span id="ps-success" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Samples</span><span id="ps-tx-n" class="metric-val" style="color:var(--muted)">—</span></div>
      </div>

      <!-- OCPP Message Sizes -->
      <div>
        <div style="font-size:0.72rem;color:var(--muted);font-weight:600;margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.07em">OCPP Frame Sizes</div>
        <div class="metric-row"><span class="metric-label">Incoming mean</span><span id="ps-oin-mean" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Incoming min / max</span><span id="ps-oin-range" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Outgoing mean</span><span id="ps-oout-mean" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Outgoing min / max</span><span id="ps-oout-range" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Handler mean</span><span id="ps-oproc-mean" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Samples (in/out)</span><span id="ps-o-n" class="metric-val" style="color:var(--muted)">—</span></div>
      </div>

      <!-- ISO 15118 -->
      <div>
        <div style="font-size:0.72rem;color:var(--muted);font-weight:600;margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.07em">ISO 15118 Charge Loop</div>
        <div class="metric-row"><span class="metric-label">Loop time mean</span><span id="ps-iso-mean" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Loop time p95</span><span id="ps-iso-p95" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Loop time min</span><span id="ps-iso-min" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Loop time max</span><span id="ps-iso-max" class="metric-val">—</span></div>
        <div class="metric-row"><span class="metric-label">Samples</span><span id="ps-iso-n" class="metric-val" style="color:var(--muted)">—</span></div>
      </div>

      <!-- IEC 104 Theoretical Message Sizes -->
      <div>
        <div style="font-size:0.72rem;color:var(--muted);font-weight:600;margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.07em">IEC 104 APDU Sizes (theoretical)</div>
        <div class="metric-row"><span class="metric-label">C_RC_TA_1 (cmd)</span><span class="metric-val" style="color:var(--purple)">23 B</span></div>
        <div class="metric-row"><span class="metric-label">M_ME_NC_1 (meas.)</span><span class="metric-val" style="color:var(--purple)">20 B</span></div>
        <div class="metric-row"><span class="metric-label">U-frame (ctrl)</span><span class="metric-val" style="color:var(--purple)">6 B</span></div>
        <div class="metric-row"><span class="metric-label">S-frame (ack)</span><span class="metric-val" style="color:var(--purple)">6 B</span></div>
        <div class="metric-row" style="border-top:1px solid var(--border);margin-top:0.4rem;padding-top:0.4rem">
          <span class="metric-label" style="font-size:0.68rem">Per IEC&nbsp;60870-5-104</span>
        </div>
      </div>

    </div>
  </div>

</div><!-- .dashboard -->

<script>
// ── Config ──────────────────────────────────────────────────────────────────
const MAX_PTS = 120;   // 60 s at 500 ms intervals

// ── State ────────────────────────────────────────────────────────────────────
const powerBuf  = { labels: [], data: [] };
let chart, wsTimer;

// ── Chart ────────────────────────────────────────────────────────────────────
function initChart() {
  const ctx = document.getElementById('power-chart').getContext('2d');
  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: powerBuf.labels,
      datasets: [{
        label: 'Power kW',
        data: powerBuf.data,
        borderColor: '#58a6ff',
        backgroundColor: 'rgba(88,166,255,0.07)',
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        tension: 0.3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: { display: false },
        y: {
          grid: { color: '#21262d' },
          ticks: { color: '#6e7681', font: { size: 10 },
                   callback: v => v.toFixed(1) + ' kW' }
        }
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: c => c.parsed.y.toFixed(2) + ' kW' } }
      }
    }
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function ageFmt(ms) {
  if (ms === null || ms === undefined) return '—';
  if (ms < 2000)  return ms + ' ms';
  return (ms / 1000).toFixed(1) + ' s';
}

function setColor(el, val, warnHi, errHi) {
  el.style.color = val > errHi ? 'var(--red)' : val > warnHi ? 'var(--orange)' : 'var(--text)';
}

// ── Main update ───────────────────────────────────────────────────────────────
function updateUI(d) {
  const now = new Date();
  const kw  = d.iec104.power_kw;

  // ── Power Flow ──
  const pNum = document.getElementById('power-num');
  const pDir = document.getElementById('power-dir');
  pNum.textContent = Math.abs(kw).toFixed(2);
  if (kw > 0.1) {
    pNum.style.color = 'var(--green)';
    pDir.innerHTML   = '<span style="font-size:1.1rem">&#8595;</span> Charging (Grid &#8594; EV)';
    pDir.style.color = 'var(--green)';
  } else if (kw < -0.1) {
    pNum.style.color = 'var(--red)';
    pDir.innerHTML   = '<span style="font-size:1.1rem">&#8593;</span> V2G Discharge (EV &#8594; Grid)';
    pDir.style.color = 'var(--red)';
  } else {
    pNum.style.color = 'var(--muted)';
    pDir.textContent = '— Idle';
    pDir.style.color = 'var(--muted)';
  }
  document.getElementById('ocpp-power').textContent  = d.ocpp.power_w.toFixed(0);
  document.getElementById('ocpp-energy').textContent = d.ocpp.energy_wh.toFixed(1);

  // ── SoC ──
  const soc = d.iec104.soc_pct;
  document.getElementById('soc-num').textContent = soc.toFixed(1);
  const bar = document.getElementById('soc-bar');
  bar.style.width      = soc + '%';
  bar.style.background = soc < 20 ? 'var(--red)' : soc < 40 ? 'var(--orange)' : 'var(--green)';
  document.getElementById('ocpp-soc').textContent = d.ocpp.soc_pct.toFixed(1);
  document.getElementById('temp-val').textContent = d.iec104.temp_c.toFixed(1);

  // ── Grid Health ──
  const v = d.grid.voltage_pu;
  const vEl = document.getElementById('g-voltage');
  vEl.textContent  = v.toFixed(4) + ' pu';
  vEl.style.color  = v < 0.95 ? 'var(--red)' : v > 1.05 ? 'var(--orange)' : 'var(--green)';

  const trEl = document.getElementById('g-trafo');
  const liEl = document.getElementById('g-line');
  if (d.grid.idle) {
    trEl.textContent = '—';  trEl.style.color = 'var(--text)';
    liEl.textContent = '—';  liEl.style.color = 'var(--text)';
  } else {
    const tr = d.grid.trafo_pct;
    trEl.textContent = tr.toFixed(1) + '%';
    setColor(trEl, tr, 80, 90);
    const li = d.grid.line_pct;
    liEl.textContent = li.toFixed(1) + '%';
    setColor(liEl, li, 80, 90);
  }

  const iecAge = d.iec104.age_ms;
  const iecAgeEl = document.getElementById('iec-age');
  iecAgeEl.textContent = ageFmt(iecAge);
  iecAgeEl.style.color = iecAge > 5000 ? 'var(--red)' : iecAge > 2000 ? 'var(--orange)' : 'var(--text)';

  const ocppAge = d.ocpp.age_ms;
  const ocppAgeEl = document.getElementById('ocpp-age');
  ocppAgeEl.textContent = ageFmt(ocppAge);
  ocppAgeEl.style.color = ocppAge > 15000 ? 'var(--red)' : ocppAge > 8000 ? 'var(--orange)' : 'var(--text)';

  // ── Status dots ──
  document.getElementById('dot-iec').className  = 'dot ' + (iecAge !== null  && iecAge  < 3000  ? 'ok' : 'warn');
  document.getElementById('dot-ocpp').className = 'dot ' + (ocppAge !== null && ocppAge < 15000 ? 'ok' : ocppAge !== null ? 'warn' : '');

  // ── Voltage stab background load display ──
  if (d.grid.sim_bg_load_kw !== undefined) {
    const bgEl = document.getElementById('vstab-bg-load');
    const bgKw = d.grid.sim_bg_load_kw;
    bgEl.textContent = (bgKw >= 0 ? '+' : '') + bgKw.toFixed(1) + ' kW';
    bgEl.style.color = bgKw > 0 ? 'var(--red)' : bgKw < 0 ? 'var(--green)' : 'var(--purple)';
  }

  // ── Timing bars ──
  const t    = d.timing;
  const peak = Math.max(t.cycle_ms, 50);
  document.getElementById('t-read').textContent       = t.read_ms.toFixed(1) + ' ms';
  document.getElementById('tb-read').style.width      = (t.read_ms    / peak * 100) + '%';
  document.getElementById('t-compute').textContent    = t.compute_ms.toFixed(1) + ' ms';
  document.getElementById('tb-compute').style.width   = (t.compute_ms / peak * 100) + '%';
  document.getElementById('t-transmit').textContent   = t.transmit_ms.toFixed(1) + ' ms';
  document.getElementById('tb-transmit').style.width  = (t.transmit_ms / peak * 100) + '%';
  document.getElementById('t-cycle').textContent      = t.cycle_ms.toFixed(1) + ' ms';

  // ── Power chart ──
  const label = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  powerBuf.labels.push(label);
  powerBuf.data.push(kw);
  if (powerBuf.labels.length > MAX_PTS) { powerBuf.labels.shift(); powerBuf.data.shift(); }
  // Colour the line by current direction
  chart.data.datasets[0].borderColor     = kw < -0.1 ? '#f85149' : kw > 0.1 ? '#3fb950' : '#58a6ff';
  chart.data.datasets[0].backgroundColor = kw < -0.1 ? 'rgba(248,81,73,0.06)' : kw > 0.1 ? 'rgba(63,185,80,0.06)' : 'rgba(88,166,255,0.07)';
  chart.update('none');

  // ── ISO 15118 ──
  const iso = d.iso15118;
  if (iso) {
    const active = iso.voltage_v > 0;
    const ageEl = document.getElementById('iso-age');
    if (iso.age_ms !== null && iso.age_ms !== undefined) {
      ageEl.textContent = '· ' + ageFmt(iso.age_ms);
      ageEl.style.color = iso.age_ms > 30000 ? 'var(--red)' : iso.age_ms > 15000 ? 'var(--orange)' : 'var(--muted)';
    }
    document.getElementById('iso-voltage').textContent      = active ? iso.voltage_v.toFixed(1) + ' V'  : '—';
    document.getElementById('iso-current').textContent      = active ? iso.current_a.toFixed(1) + ' A'  : '—';
    const pw = iso.power_kw;
    const pwEl = document.getElementById('iso-power');
    pwEl.textContent  = active ? Math.abs(pw).toFixed(2) + ' kW' : '—';
    pwEl.style.color  = !active ? 'var(--muted)' : pw < -0.05 ? 'var(--red)' : pw > 0.05 ? 'var(--green)' : 'var(--muted)';
    document.getElementById('iso-charge-lim').textContent   = active ? iso.evse_charge_kw.toFixed(1)    + ' kW' : '—';
    document.getElementById('iso-discharge-lim').textContent= active ? iso.evse_discharge_kw.toFixed(1) + ' kW' : '—';
    document.getElementById('iso-loop-ms').textContent      = active ? iso.loop_ms.toFixed(1) + ' ms'           : '—';
  }

  // ── Command log ──
  const logEl = document.getElementById('log-list');
  if (d.log && d.log.length) {
    logEl.innerHTML = d.log.map(e =>
      '<div class="log-entry">'
      + '<span class="log-time">' + e.t + '</span>'
      + '<span class="badge badge-' + e.cmd.toLowerCase() + '">' + e.cmd + '</span>'
      + '<span class="badge badge-' + e.src + '">'  + e.src + '</span>'
      + '</div>'
    ).join('');
  }

  // ── Control mode ──
  syncControlButtons(d.control);

  // ── Billing ──
  updateBilling(d.billing);

  // ── Performance stats ──
  updatePerf(d.perf);

  // ── User preferences ──
  syncPreferences(d.prefs);

  // ── Security ──
  updateSecurity(d.security);
}

// ── Security card ────────────────────────────────────────────────────────────
function updateSecurity(sec) {
  if (!sec) return;

  function renderRow(rowId, verId, expId, proto) {
    const row   = document.getElementById(rowId);
    const lock  = row ? row.querySelector('.sec-lock') : null;
    const verEl = document.getElementById(verId);
    const expEl = document.getElementById(expId);
    const ok    = proto.configured;
    const active= proto.connected;

    if (lock) {
      lock.textContent  = ok ? '🔒' : '🔓';
      lock.style.filter = ok ? (active ? 'none' : 'opacity(0.5)') : 'grayscale(1) opacity(0.4)';
    }
    if (verEl) {
      verEl.textContent = proto.tls_version || (ok ? 'TLS' : 'Not configured');
      verEl.style.color = ok ? 'var(--green)' : 'var(--red)';
    }
    if (expEl) {
      if (!proto.cert_expiry) {
        expEl.textContent = ok ? 'on peer' : '—';
        expEl.style.color = 'var(--muted)';
      } else {
        const daysLeft = Math.round((new Date(proto.cert_expiry) - new Date()) / 86400000);
        expEl.textContent = proto.cert_expiry + (daysLeft < 60 ? ' (' + daysLeft + ' d)' : '');
        expEl.style.color = daysLeft < 30 ? 'var(--red)' : daysLeft < 90 ? 'var(--orange)' : 'var(--muted)';
      }
    }
  }

  renderRow('sec-ocpp',   'sec-ocpp-ver', 'sec-ocpp-exp', sec.ocpp);
  renderRow('sec-iec104', 'sec-iec-ver',  'sec-iec-exp',  sec.iec104);
  renderRow('sec-iso',    'sec-iso-ver',  'sec-iso-exp',  sec.iso15118);
}

// ── Control buttons ───────────────────────────────────────────────────────────
function syncControlButtons(ctrl) {
  const isAuto   = ctrl.auto;
  const override = ctrl.override;
  const isVStab  = ctrl.voltage_stab;

  document.getElementById('btn-auto').className   = 'ctrl-btn' + (isAuto && !isVStab               ? ' active-auto'   : '');
  document.getElementById('btn-v2g').className    = 'ctrl-btn' + (!isAuto && override === 'LOWER'   ? ' active-v2g'    : '');
  document.getElementById('btn-charge').className = 'ctrl-btn' + (!isAuto && override === 'HIGHER'  ? ' active-charge' : '');
  document.getElementById('btn-vstab').className  = 'ctrl-btn' + (isVStab                           ? ' active-vstab'  : '');

  document.getElementById('vstab-rows').style.display = isVStab ? '' : 'none';

  const s = document.getElementById('ctrl-status');
  if (isVStab) {
    s.textContent = 'Mode: Voltage Stabilisation — V2G responding to bus 2 voltage, target 0.975 pu (±0.003 deadband)';
    s.className   = 'ctrl-status mode-vstab';
  } else if (isAuto) {
    s.textContent  = 'Mode: Auto — grid-controlled via PandaPower load-flow';
    s.className    = 'ctrl-status mode-auto';
  } else if (override === 'LOWER') {
    s.textContent  = 'Mode: Manual V2G Demand — sending LOWER commands (reduce charge / increase discharge)';
    s.className    = 'ctrl-status mode-v2g';
  } else {
    s.textContent  = 'Mode: Manual Charge — sending HIGHER commands (increase charge power)';
    s.className    = 'ctrl-status mode-charge';
  }
}

function setControl(action) {
  fetch('/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action })
  });
}

// ── Performance statistics ────────────────────────────────────────────────────
function updatePerf(p) {
  if (!p) return;

  function fmt(stats, unit) {
    if (!stats || !stats.count) return '—';
    return stats.mean.toFixed(unit === 'B' ? 0 : 1) + ' ' + unit;
  }
  function fmtRange(stats, unit) {
    if (!stats || !stats.count) return '—';
    return stats.min.toFixed(0) + '–' + stats.max.toFixed(0) + ' ' + unit;
  }
  function fmtP95(stats, unit) {
    if (!stats || !stats.count) return '—';
    return stats.p95.toFixed(1) + ' ' + unit;
  }

  // IEC 104
  const tx = p.iec104_transmit_ms;
  const pp = p.iec104_pandapower_ms;
  setText('ps-tx-mean',  fmt(tx, 'ms'));
  setText('ps-tx-p95',   fmtP95(tx, 'ms'));
  setText('ps-pp-mean',  fmt(pp, 'ms'));
  setText('ps-pp-p95',   fmtP95(pp, 'ms'));
  setText('ps-tx-n',     tx && tx.count ? tx.count + ' tx' : '—');
  const sr = p.iec104_success_rate;
  const srEl = document.getElementById('ps-success');
  if (srEl) {
    srEl.textContent  = sr !== null && sr !== undefined ? (sr * 100).toFixed(1) + '%' : '—';
    srEl.style.color  = sr === null ? 'var(--muted)' : sr >= 0.99 ? 'var(--green)' : sr >= 0.95 ? 'var(--orange)' : 'var(--red)';
  }

  // OCPP
  const oin  = p.ocpp_incoming_bytes;
  const oout = p.ocpp_outgoing_bytes;
  const oproc = p.ocpp_processing_ms;
  setText('ps-oin-mean',  fmt(oin, 'B'));
  setText('ps-oin-range', fmtRange(oin, 'B'));
  setText('ps-oout-mean', fmt(oout, 'B'));
  setText('ps-oout-range',fmtRange(oout, 'B'));
  setText('ps-oproc-mean',fmt(oproc, 'ms'));
  setText('ps-o-n', (oin && oin.count ? oin.count : 0) + ' / ' + (oout && oout.count ? oout.count : 0));

  // ISO 15118
  const iso = p.iso_loop_ms;
  setText('ps-iso-mean', fmt(iso, 'ms'));
  setText('ps-iso-p95',  fmtP95(iso, 'ms'));
  setText('ps-iso-min',  iso && iso.count ? iso.min.toFixed(1) + ' ms' : '—');
  setText('ps-iso-max',  iso && iso.count ? iso.max.toFixed(1) + ' ms' : '—');
  setText('ps-iso-n',    iso && iso.count ? iso.count + ' samples' : '—');
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── Billing ───────────────────────────────────────────────────────────────────
function updateBilling(b) {
  if (!b) return;
  const chKwh = b.charge_kwh;
  const v2Kwh = b.v2g_kwh;
  const chCost = b.charge_cost_gbp;
  const v2Cred = b.v2g_credit_gbp;
  const net    = b.net_cost_gbp;

  document.getElementById('bill-charge-kwh').textContent  = chKwh.toFixed(3) + ' kWh';
  document.getElementById('bill-charge-cost').textContent = '£' + chCost.toFixed(2) + ' charge cost';
  document.getElementById('bill-v2g-kwh').textContent     = v2Kwh.toFixed(3) + ' kWh';
  document.getElementById('bill-v2g-credit').textContent  = '£' + v2Cred.toFixed(2) + ' credit';

  const netEl = document.getElementById('bill-net');
  netEl.textContent  = (net >= 0 ? '£' : '−£') + Math.abs(net).toFixed(2);
  netEl.style.color  = net < 0 ? 'var(--green)' : net > 0 ? 'var(--red)' : 'var(--text)';

  // Sync tariff inputs (don't overwrite if focused)
  const chEl = document.getElementById('tariff-charge');
  const v2El = document.getElementById('tariff-v2g');
  if (document.activeElement !== chEl) chEl.value = b.tariff_charge_p;
  if (document.activeElement !== v2El) v2El.value = b.tariff_v2g_p;
}

function saveTariff() {
  const body = {
    charge_pence_per_kwh: parseFloat(document.getElementById('tariff-charge').value),
    v2g_pence_per_kwh:    parseFloat(document.getElementById('tariff-v2g').value),
  };
  fetch('/api/tariff', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json()).then(() => {
    const el = document.getElementById('tariff-status');
    el.textContent = '✓ Applied';
    setTimeout(() => { el.textContent = ''; }, 3000);
  }).catch(() => {
    document.getElementById('tariff-status').textContent = '✗ Failed';
  });
}

// ── User Preferences ──────────────────────────────────────────────────────────
function syncPreferences(prefs) {
  if (!prefs) return;
  const map = {
    'pref-min-soc':    'min_soc_pct',
    'pref-max-soc':    'max_soc_pct',
    'pref-target-soc': 'target_soc_pct',
    'pref-departure':  'departure_time',
  };
  Object.entries(map).forEach(([id, key]) => {
    const el = document.getElementById(id);
    if (el && document.activeElement !== el) el.value = prefs[key];
  });
}

function savePreferences() {
  const body = {
    min_soc_pct:    parseFloat(document.getElementById('pref-min-soc').value),
    max_soc_pct:    parseFloat(document.getElementById('pref-max-soc').value),
    target_soc_pct: parseFloat(document.getElementById('pref-target-soc').value),
    departure_time:  document.getElementById('pref-departure').value,
  };
  fetch('/api/preferences', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json()).then(d => {
    const el = document.getElementById('pref-status');
    el.textContent = d.ocpp === 'sent' ? '✓ Saved & sent to charger' : '✓ Saved (charger offline — will sync on reconnect)';
    setTimeout(() => { el.textContent = ''; }, 4000);
  }).catch(() => {
    document.getElementById('pref-status').textContent = '✗ Save failed';
  });
}

// ── WebSocket ─────────────────────────────────────────────────────────────────
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws    = new WebSocket(proto + '://' + location.host + '/ws');

  ws.onopen = () => {
    document.getElementById('dot-ws').className = 'dot ok';
    clearTimeout(wsTimer);
  };

  ws.onmessage = evt => {
    try { updateUI(JSON.parse(evt.data)); } catch(e) { console.error(e); }
  };

  ws.onclose = ws.onerror = () => {
    document.getElementById('dot-ws').className = 'dot err';
    wsTimer = setTimeout(connect, 3000);
  };
}

initChart();
connect();
</script>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────────────
#  FastAPI endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


@app.post("/api/control")
async def control(request: Request):
    body = await request.json()
    action = body.get("action", "")
    if action == "v2g":
        grid_state.manual_override   = "LOWER"
        grid_state.auto_control      = False
        grid_state.voltage_stab_mode = False
    elif action == "charge":
        grid_state.manual_override   = "HIGHER"
        grid_state.auto_control      = False
        grid_state.voltage_stab_mode = False
    elif action == "auto":
        grid_state.manual_override   = None
        grid_state.auto_control      = True
        grid_state.voltage_stab_mode = False
    elif action == "voltage_stab":
        grid_state.manual_override   = None
        grid_state.auto_control      = True
        grid_state.voltage_stab_mode = True
    return {"status": "ok", "action": action}


@app.post("/api/tariff")
async def set_tariff(request: Request):
    body = await request.json()
    if "charge_pence_per_kwh" in body:
        grid_state.tariff.charge_pence_per_kwh = max(0.0, float(body["charge_pence_per_kwh"]))
    if "v2g_pence_per_kwh" in body:
        grid_state.tariff.v2g_pence_per_kwh = max(0.0, float(body["v2g_pence_per_kwh"]))
    return {"status": "ok"}


@app.post("/api/preferences")
async def set_preferences(request: Request):
    body = await request.json()
    prefs = grid_state.prefs
    if "min_soc_pct" in body:
        prefs.min_soc_pct    = max(0.0, min(100.0, float(body["min_soc_pct"])))
    if "max_soc_pct" in body:
        prefs.max_soc_pct    = max(0.0, min(100.0, float(body["max_soc_pct"])))
    if "target_soc_pct" in body:
        prefs.target_soc_pct = max(0.0, min(100.0, float(body["target_soc_pct"])))
    if "departure_time" in body:
        prefs.departure_time = str(body["departure_time"])

    cp = grid_state.connected_charge_point
    if cp:
        try:
            await cp.send_preferences(prefs)
            return {"status": "ok", "ocpp": "sent"}
        except Exception as exc:
            return {"status": "ok", "ocpp": f"error: {exc}"}
    return {"status": "ok", "ocpp": "not_connected"}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(_build_payload())
            await asyncio.sleep(0.5)
    except (WebSocketDisconnect, Exception):
        pass


def _build_billing(ocpp) -> dict:
    t = grid_state.tariff
    charge_kwh   = ocpp.energy_wh   / 1000.0
    v2g_kwh      = ocpp.v2g_energy_wh / 1000.0
    charge_cost  = charge_kwh * t.charge_pence_per_kwh / 100.0
    v2g_credit   = v2g_kwh   * t.v2g_pence_per_kwh    / 100.0
    return {
        "charge_kwh":      round(charge_kwh,  4),
        "v2g_kwh":         round(v2g_kwh,     4),
        "charge_cost_gbp": round(charge_cost, 4),
        "v2g_credit_gbp":  round(v2g_credit,  4),
        "net_cost_gbp":    round(charge_cost - v2g_credit, 4),
        "tariff_charge_p": t.charge_pence_per_kwh,
        "tariff_v2g_p":    t.v2g_pence_per_kwh,
    }


def _build_payload() -> dict:
    now = time.time()
    iec  = grid_state.iec104
    ocpp = grid_state.ocpp
    grid = grid_state.grid

    return {
        "ts": now,
        "iec104": {
            "power_kw": round(iec.power_kw, 2),
            "soc_pct":  round(iec.soc_percent, 1),
            "temp_c":   round(iec.temp_c, 1),
            "age_ms":   round((now - iec.timestamp) * 1000) if iec.timestamp else None,
        },
        "ocpp": {
            "power_w":   round(ocpp.power_w, 1),
            "energy_wh": round(ocpp.energy_wh, 2),
            "soc_pct":   round(ocpp.soc_percent, 1),
            "age_ms":    round((now - ocpp.timestamp) * 1000) if ocpp.timestamp else None,
        },
        "grid": {
            "voltage_pu":     round(grid.bus2_voltage_pu, 4),
            "trafo_pct":      round(grid.trafo_loading_pct, 1),
            "line_pct":       round(grid.line_loading_pct, 1),
            "idle":           grid_state.charger_idle,
            "sim_bg_load_kw": round(grid.sim_bg_load_kw, 1),
        },
        "timing": {
            "read_ms":     round(grid_state.iec104_read_ms, 1),
            "compute_ms":  round(grid_state.pandapower_ms, 1),
            "transmit_ms": round(grid_state.transmit_ms, 1),
            "cycle_ms":    round(grid_state.cycle_ms, 1),
        },
        "control": {
            "auto":          grid_state.auto_control,
            "override":      grid_state.manual_override,
            "voltage_stab":  grid_state.voltage_stab_mode,
        },
        "iso15118": {
            "voltage_v":         round(iec.voltage_v, 1),
            "current_a":         round(iec.current_a, 2),
            "power_kw":          round((iec.voltage_v * iec.current_a) / 1000.0, 2),
            "evse_charge_kw":    round(ocpp.evse_max_charge_kw, 1),
            "evse_discharge_kw": round(ocpp.evse_max_discharge_kw, 1),
            "loop_ms":           round(iec.iso_loop_ms, 1),
            "age_ms":            round((now - iec.iso_timestamp) * 1000) if iec.iso_timestamp else None,
        },
        "prefs": {
            "min_soc_pct":    grid_state.prefs.min_soc_pct,
            "max_soc_pct":    grid_state.prefs.max_soc_pct,
            "target_soc_pct": grid_state.prefs.target_soc_pct,
            "departure_time": grid_state.prefs.departure_time,
        },
        "log": [
            {
                "t":   time.strftime("%H:%M:%S", time.localtime(e.timestamp)),
                "cmd": e.command,
                "src": e.source,
            }
            for e in grid_state.command_log
        ],
        "billing": _build_billing(ocpp),
        "perf":    perf_logger.get_live_stats(),
        "security": {
            "ocpp": {
                "configured":   grid_state.security.ocpp.configured,
                "connected":    grid_state.security.ocpp.connected,
                "tls_version":  grid_state.security.ocpp.tls_version,
                "cipher":       grid_state.security.ocpp.cipher,
                "cert_expiry":  grid_state.security.ocpp.cert_expiry,
                "auth":         "mTLS (Profile 3)",
            },
            "iec104": {
                "configured":   grid_state.security.iec104.configured,
                "connected":    grid_state.security.iec104.connected,
                "tls_version":  grid_state.security.iec104.tls_version,
                "cipher":       grid_state.security.iec104.cipher,
                "cert_expiry":  grid_state.security.iec104.cert_expiry,
                "auth":         "mTLS (IEC 62351-3)",
            },
            "iso15118": {
                "configured":   grid_state.security.iso15118.configured,
                "connected":    grid_state.security.iso15118.connected,
                "tls_version":  grid_state.security.iso15118.tls_version,
                "cipher":       grid_state.security.iso15118.cipher,
                "cert_expiry":  "",
                "auth":         "V2G PKI",
            },
        },
    }


@app.get("/api/perf/summary")
async def perf_summary():
    return perf_logger.get_summary()


@app.get("/api/perf/csv/{name}")
async def perf_csv(name: str):
    paths = perf_logger.csv_paths()
    if name not in paths:
        return {"error": f"unknown log name '{name}' — valid: {list(paths)}"}
    from pathlib import Path
    p = Path(paths[name])
    if not p.exists():
        return {"error": "log file not yet created (no data recorded this session)"}
    return FileResponse(p, filename=p.name, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{p.name}"'})


async def run_web_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
