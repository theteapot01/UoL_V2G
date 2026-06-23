"""
perf_logger.py
==============
Session performance logger for the V2G grid-Pi stack.

Writes append-only CSV files to Logs/ and maintains in-memory rolling
statistics (count / min / mean / max / p95) for the live dashboard API.

Log files (named with session start timestamp):
  Logs/iec104_YYYYMMDD_HHMMSS.csv    per 4-s transmit cycle: latencies + outcome
  Logs/ocpp_YYYYMMDD_HHMMSS.csv      per OCPP frame: size (bytes) + handler time
  Logs/iso15118_YYYYMMDD_HHMMSS.csv  per 4-s ISO 15118 sample: loop latency

IEC 104 APDU sizes are theoretical (IEC 60870-5-104); the c104 library does
not expose raw byte counts.  OCPP sizes are measured from the live WebSocket
frames.  ISO 15118 loop time is measured on the charger Pi and relayed via
IOA 17.
"""

import csv
import math
import threading
import time
from collections import deque
from pathlib import Path

_LOG_DIR = Path(__file__).parent.parent / "Logs"
_SESSION = time.strftime("%Y%m%d_%H%M%S")

_P95_WINDOW = 1000  # rolling window size for percentile calculation


# ── Online statistics accumulator ─────────────────────────────────────────────

class _Stats:
    def __init__(self):
        self.count  = 0
        self.total  = 0.0
        self.min    = math.inf
        self.max    = -math.inf
        self._win: deque = deque(maxlen=_P95_WINDOW)

    def record(self, v: float) -> None:
        self.count += 1
        self.total += v
        if v < self.min:
            self.min = v
        if v > self.max:
            self.max = v
        self._win.append(v)

    def to_dict(self) -> dict:
        if self.count == 0:
            return {"count": 0}
        mean = self.total / self.count
        p95  = sorted(self._win)[int(len(self._win) * 0.95)]
        return {
            "count": self.count,
            "mean":  round(mean, 3),
            "min":   round(self.min, 3),
            "max":   round(self.max, 3),
            "p95":   round(p95, 3),
        }


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _init_csv(path: Path, headers: list) -> None:
    if not path.exists():
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(headers)


def _append(path: Path, row: list) -> None:
    with open(path, "a", newline="") as f:
        csv.writer(f).writerow(row)


# ── PerfLogger ────────────────────────────────────────────────────────────────

class PerfLogger:
    """
    Theoretical IEC 104 APDU sizes (bytes).
    APCI = 6 B (start + length + 4-byte control field).
    ASDU header = type_id(1) + VSQ(1) + COT(2) + common_addr(2) = 6 B.
    Per-object payload varies by type.
    """
    IEC104_MSG_SIZES: dict = {
        "C_RC_TA_1": {
            "bytes": 23,
            "breakdown": "APCI(6) + ASDU_hdr(6) + IOA(3) + RCO(1) + CP56Time2a(7)",
            "note": "step command with time tag — IOA 12",
        },
        "M_ME_NC_1": {
            "bytes": 20,
            "breakdown": "APCI(6) + ASDU_hdr(6) + IOA(3) + ShortFloat(4) + Quality(1)",
            "note": "measured short float — IOAs 11,13,14,15,16,17",
        },
        "U-frame": {
            "bytes": 6,
            "breakdown": "APCI(6) only",
            "note": "STARTDT/STOPDT/TESTFR activate and confirm",
        },
        "S-frame": {
            "bytes": 6,
            "breakdown": "APCI(6) only",
            "note": "supervisory (receive acknowledgement)",
        },
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        _LOG_DIR.mkdir(parents=True, exist_ok=True)

        self._iec104_path = _LOG_DIR / f"iec104_{_SESSION}.csv"
        self._ocpp_path   = _LOG_DIR / f"ocpp_{_SESSION}.csv"
        self._iso_path    = _LOG_DIR / f"iso15118_{_SESSION}.csv"

        _init_csv(self._iec104_path, [
            "timestamp_unix", "timestamp_iso",
            "cmd", "bursts", "success",
            "transmit_ms", "read_ms", "pandapower_ms", "cycle_ms",
        ])
        _init_csv(self._ocpp_path, [
            "timestamp_unix", "timestamp_iso",
            "direction", "msg_type", "size_bytes", "processing_ms",
        ])
        _init_csv(self._iso_path, [
            "timestamp_unix", "timestamp_iso",
            "loop_ms", "voltage_v", "current_a", "power_kw", "soc_pct",
        ])

        # Running statistics
        self.iec104_transmit_ms   = _Stats()
        self.iec104_read_ms       = _Stats()
        self.iec104_pandapower_ms = _Stats()
        self.iec104_cycle_ms      = _Stats()
        self._iec104_ok           = 0
        self._iec104_total        = 0

        self.ocpp_incoming_bytes  = _Stats()
        self.ocpp_outgoing_bytes  = _Stats()
        self.ocpp_processing_ms   = _Stats()

        self.iso_loop_ms          = _Stats()

    # ── IEC 104 ──────────────────────────────────────────────────────────────

    def log_iec104(
        self,
        cmd: str,
        bursts: int,
        success: bool,
        transmit_ms: float,
        read_ms: float,
        pandapower_ms: float,
        cycle_ms: float,
    ) -> None:
        now = time.time()
        with self._lock:
            self._iec104_total += 1
            self.iec104_read_ms.record(read_ms)
            self.iec104_pandapower_ms.record(pandapower_ms)
            self.iec104_cycle_ms.record(cycle_ms)
            if cmd != "HOLD":
                self.iec104_transmit_ms.record(transmit_ms)
                if success:
                    self._iec104_ok += 1
            _append(self._iec104_path, [
                f"{now:.3f}",
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                cmd, bursts, int(success),
                f"{transmit_ms:.3f}", f"{read_ms:.3f}",
                f"{pandapower_ms:.3f}", f"{cycle_ms:.3f}",
            ])

    # ── OCPP ─────────────────────────────────────────────────────────────────

    def log_ocpp_message(
        self,
        direction: str,         # "incoming" | "outgoing"
        msg_type: str,          # OCPP action name or "CallResult"
        size_bytes: int = 0,
        processing_ms: float = 0.0,
    ) -> None:
        now = time.time()
        with self._lock:
            if size_bytes > 0:
                if direction == "incoming":
                    self.ocpp_incoming_bytes.record(size_bytes)
                else:
                    self.ocpp_outgoing_bytes.record(size_bytes)
            if processing_ms > 0:
                self.ocpp_processing_ms.record(processing_ms)
            _append(self._ocpp_path, [
                f"{now:.3f}",
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                direction, msg_type, size_bytes, f"{processing_ms:.3f}",
            ])

    # ── ISO 15118 ─────────────────────────────────────────────────────────────

    def log_iso15118(
        self,
        loop_ms: float,
        voltage_v: float,
        current_a: float,
        power_kw: float,
        soc_pct: float,
    ) -> None:
        if loop_ms <= 0:
            return
        now = time.time()
        with self._lock:
            self.iso_loop_ms.record(loop_ms)
            _append(self._iso_path, [
                f"{now:.3f}",
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                f"{loop_ms:.3f}", f"{voltage_v:.2f}",
                f"{current_a:.3f}", f"{power_kw:.3f}", f"{soc_pct:.1f}",
            ])

    # ── Summary / export ─────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        with self._lock:
            total = self._iec104_total
            ok    = self._iec104_ok
            return {
                "session": _SESSION,
                "log_dir": str(_LOG_DIR),
                "iec104": {
                    "transmit_ms":     self.iec104_transmit_ms.to_dict(),
                    "read_ms":         self.iec104_read_ms.to_dict(),
                    "pandapower_ms":   self.iec104_pandapower_ms.to_dict(),
                    "cycle_ms":        self.iec104_cycle_ms.to_dict(),
                    "success_rate":    round(ok / total, 4) if total else None,
                    "total_transmits": total,
                    "message_sizes":   self.IEC104_MSG_SIZES,
                },
                "ocpp": {
                    "processing_ms":   self.ocpp_processing_ms.to_dict(),
                    "incoming_bytes":  self.ocpp_incoming_bytes.to_dict(),
                    "outgoing_bytes":  self.ocpp_outgoing_bytes.to_dict(),
                },
                "iso15118": {
                    "loop_ms": self.iso_loop_ms.to_dict(),
                },
            }

    def get_live_stats(self) -> dict:
        """Compact version for the 500 ms WebSocket payload."""
        with self._lock:
            total = self._iec104_total
            ok    = self._iec104_ok
            return {
                "iec104_transmit_ms":   self.iec104_transmit_ms.to_dict(),
                "iec104_pandapower_ms": self.iec104_pandapower_ms.to_dict(),
                "iec104_success_rate":  round(ok / total, 4) if total else None,
                "ocpp_incoming_bytes":  self.ocpp_incoming_bytes.to_dict(),
                "ocpp_processing_ms":   self.ocpp_processing_ms.to_dict(),
                "iso_loop_ms":          self.iso_loop_ms.to_dict(),
            }

    def csv_paths(self) -> dict:
        return {
            "iec104":   str(self._iec104_path),
            "ocpp":     str(self._ocpp_path),
            "iso15118": str(self._iso_path),
        }


perf_logger = PerfLogger()
