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
import time

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from code_grid.grid_state import grid_state

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
    .ctrl-status {
      font-size: 0.78rem; padding: 0.4rem 0.8rem;
      border-radius: 4px; border: 1px solid transparent;
      transition: all 0.3s;
    }
    .ctrl-status.mode-auto   { background: rgba(88,166,255,0.08); color: var(--blue);  border-color: rgba(88,166,255,0.2); }
    .ctrl-status.mode-v2g    { background: rgba(248,81,73,0.08);  color: var(--red);   border-color: rgba(248,81,73,0.2);  }
    .ctrl-status.mode-charge { background: rgba(63,185,80,0.08);  color: var(--green); border-color: rgba(63,185,80,0.2);  }

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

  <!-- Manual Control (2 cols) -->
  <div class="card span2">
    <div class="card-label">Grid Demand Control</div>
    <div class="ctrl-desc">
      Override the automatic PandaPower-based control.
      <strong>Force V2G</strong> sends a continuous HIGHER step command — the charger reduces charge or starts discharging back to the grid.
      <strong>Force Charge</strong> sends LOWER — the charger increases charge power.
      Return to <strong>Auto</strong> to restore load-flow–driven control.
    </div>
    <div class="ctrl-buttons">
      <button id="btn-auto"   class="ctrl-btn" onclick="setControl('auto')">&#9679; Auto</button>
      <button id="btn-v2g"    class="ctrl-btn" onclick="setControl('v2g')">&#8593; Force V2G Demand</button>
      <button id="btn-charge" class="ctrl-btn" onclick="setControl('charge')">&#8595; Force Charge</button>
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

  <!-- Command Log -->
  <div class="card">
    <div class="card-label">Transmitted Command Log</div>
    <div id="log-list" class="log-scroll"></div>
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

  // ── User preferences ──
  syncPreferences(d.prefs);
}

// ── Control buttons ───────────────────────────────────────────────────────────
function syncControlButtons(ctrl) {
  const isAuto   = ctrl.auto;
  const override = ctrl.override;

  document.getElementById('btn-auto').className   = 'ctrl-btn' + (isAuto                   ? ' active-auto'   : '');
  document.getElementById('btn-v2g').className    = 'ctrl-btn' + (!isAuto && override === 'HIGHER' ? ' active-v2g'    : '');
  document.getElementById('btn-charge').className = 'ctrl-btn' + (!isAuto && override === 'LOWER'  ? ' active-charge' : '');

  const s = document.getElementById('ctrl-status');
  if (isAuto) {
    s.textContent  = 'Mode: Auto — grid-controlled via PandaPower load-flow';
    s.className    = 'ctrl-status mode-auto';
  } else if (override === 'HIGHER') {
    s.textContent  = 'Mode: Manual V2G Demand — sending HIGHER commands (reduce charge / increase discharge)';
    s.className    = 'ctrl-status mode-v2g';
  } else {
    s.textContent  = 'Mode: Manual Charge — sending LOWER commands (increase charge power)';
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
        grid_state.manual_override = "HIGHER"
        grid_state.auto_control = False
    elif action == "charge":
        grid_state.manual_override = "LOWER"
        grid_state.auto_control = False
    elif action == "auto":
        grid_state.manual_override = None
        grid_state.auto_control = True
    return {"status": "ok", "action": action}


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
            "voltage_pu": round(grid.bus2_voltage_pu, 4),
            "trafo_pct":  round(grid.trafo_loading_pct, 1),
            "line_pct":   round(grid.line_loading_pct, 1),
            "idle":       grid_state.charger_idle,
        },
        "timing": {
            "read_ms":     round(grid_state.iec104_read_ms, 1),
            "compute_ms":  round(grid_state.pandapower_ms, 1),
            "transmit_ms": round(grid_state.transmit_ms, 1),
            "cycle_ms":    round(grid_state.cycle_ms, 1),
        },
        "control": {
            "auto":     grid_state.auto_control,
            "override": grid_state.manual_override,
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
    }


async def run_web_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=8080, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
