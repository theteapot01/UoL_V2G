"""
control_latency.py
==================
Logs end-to-end control latency on the charger Pi: the time between
on_step_command() updating SharedState and send_charging_command()
applying the new EVSE limits in the next DC_ChargeLoopRes.

This latency captures the ISO 15118 charge-loop scheduling delay —
how long the EV takes to send the next DC_ChargeLoopReq after the
grid issues a step command.

CSV: Logs/control_latency_YYYYMMDD_HHMMSS.csv
  timestamp_unix, timestamp_iso, cmd, setpoint_kw, latency_ms

Core functions:
    _init()        — creates Logs/ and writes the CSV header on first import.
    log()          — appends one latency sample to the CSV and updates the in-memory count/mean/min/max/p95 accumulators.
    get_summary()  — returns the rolling {count, mean_ms, min_ms, max_ms, p95_ms, log_path} dict.
"""

import csv
import math
import threading
import time
from pathlib import Path

_LOG_DIR = Path(__file__).parent.parent / "Logs"
_SESSION = time.strftime("%Y%m%d_%H%M%S")
_path = _LOG_DIR / f"control_latency_{_SESSION}.csv"

_lock = threading.Lock()
_count = 0
_total_ms = 0.0
_min_ms = math.inf
_max_ms = -math.inf
_samples: list = []  # bounded window for p95

_P95_MAX = 1000


def _init() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not _path.exists():
        with open(_path, "w", newline="") as f:
            csv.writer(f).writerow([
                "timestamp_unix", "timestamp_iso",
                "cmd", "setpoint_kw", "latency_ms",
            ])


def log(cmd: str, setpoint_kw: float, latency_ms: float) -> None:
    global _count, _total_ms, _min_ms, _max_ms
    now = time.time()
    with _lock:
        _count += 1
        _total_ms += latency_ms
        if latency_ms < _min_ms:
            _min_ms = latency_ms
        if latency_ms > _max_ms:
            _max_ms = latency_ms
        if len(_samples) < _P95_MAX:
            _samples.append(latency_ms)
        with open(_path, "a", newline="") as f:
            csv.writer(f).writerow([
                f"{now:.3f}",
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                cmd, f"{setpoint_kw:.2f}", f"{latency_ms:.3f}",
            ])


def get_summary() -> dict:
    with _lock:
        if _count == 0:
            return {"count": 0, "log_path": str(_path)}
        mean = _total_ms / _count
        p95 = sorted(_samples)[int(len(_samples) * 0.95)]
        return {
            "count":    _count,
            "mean_ms":  round(mean, 3),
            "min_ms":   round(_min_ms, 3),
            "max_ms":   round(_max_ms, 3),
            "p95_ms":   round(p95, 3),
            "log_path": str(_path),
        }


_init()
