"""
iso15118_perf.py
================
Byte-counting stream proxies for ISO 15118 message size measurement.

Wraps asyncio.StreamReader / asyncio.StreamWriter to count raw bytes flowing
through the ISO 15118 TLS connection.  Measurements are application-layer bytes
(post-TLS-decryption), representing actual EXI-encoded V2GMessage sizes.

Usage (called by run_secc.py and run_evcc.py via monkey-patch):
    from iso15118_perf import CountingStreamReader, CountingStreamWriter

A session CSV is written to Logs/iso15118_bytes_YYYYMMDD_HHMMSS.csv with:
    timestamp_unix, timestamp_iso, direction, size_bytes,
    cumulative_rx_bytes, cumulative_tx_bytes

In-memory summary accessible via get_summary().

Used on both the Charger (run_secc.py) and EV (run_evcc.py) sides — each
process gets its own independent counters and CSV.

Core functions/classes:
    _log()                 — records one read/write event's byte count into the CSV and cumulative totals.
    get_summary()           — returns totals, rates, elapsed time, and log path.
    CountingStreamReader    — wraps asyncio.StreamReader; intercepts read()/readexactly()/readline()/readuntil() to count incoming bytes.
    CountingStreamWriter    — wraps asyncio.StreamWriter; intercepts write()/writelines() to count outgoing bytes.
"""

import asyncio
import csv
import threading
import time
from pathlib import Path

_LOG_DIR = Path(__file__).parent.parent / "Logs"
_SESSION = time.strftime("%Y%m%d_%H%M%S")
_iso_bytes_path = _LOG_DIR / f"iso15118_bytes_{_SESSION}.csv"

_lock = threading.Lock()
_rx_bytes_total = 0
_tx_bytes_total = 0
_rx_count = 0
_tx_count = 0
_session_start = time.time()


def _init() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not _iso_bytes_path.exists():
        with open(_iso_bytes_path, "w", newline="") as f:
            csv.writer(f).writerow([
                "timestamp_unix", "timestamp_iso",
                "direction", "size_bytes",
                "cumulative_rx_bytes", "cumulative_tx_bytes",
            ])


def _log(direction: str, size: int) -> None:
    global _rx_bytes_total, _tx_bytes_total, _rx_count, _tx_count
    now = time.time()
    with _lock:
        if direction == "rx":
            _rx_bytes_total += size
            _rx_count += 1
        else:
            _tx_bytes_total += size
            _tx_count += 1
        with open(_iso_bytes_path, "a", newline="") as f:
            csv.writer(f).writerow([
                f"{now:.3f}",
                time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                direction, size,
                _rx_bytes_total, _tx_bytes_total,
            ])


def get_summary() -> dict:
    with _lock:
        elapsed = max(time.time() - _session_start, 1.0)
        return {
            "rx_bytes_total":  _rx_bytes_total,
            "tx_bytes_total":  _tx_bytes_total,
            "rx_msg_count":    _rx_count,
            "tx_msg_count":    _tx_count,
            "rx_bytes_per_sec": round(_rx_bytes_total / elapsed, 2),
            "tx_bytes_per_sec": round(_tx_bytes_total / elapsed, 2),
            "elapsed_s":       round(elapsed, 1),
            "log_path":        str(_iso_bytes_path),
        }


_init()


# ── Stream proxies ─────────────────────────────────────────────────────────────

class CountingStreamReader:
    """Proxy for asyncio.StreamReader that counts received bytes."""

    def __init__(self, inner: asyncio.StreamReader) -> None:
        self._inner = inner

    async def read(self, n: int = -1) -> bytes:
        data = await self._inner.read(n)
        if data:
            _log("rx", len(data))
        return data

    async def readexactly(self, n: int) -> bytes:
        data = await self._inner.readexactly(n)
        _log("rx", len(data))
        return data

    async def readline(self) -> bytes:
        data = await self._inner.readline()
        if data:
            _log("rx", len(data))
        return data

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        data = await self._inner.readuntil(separator)
        if data:
            _log("rx", len(data))
        return data

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class CountingStreamWriter:
    """Proxy for asyncio.StreamWriter that counts sent bytes."""

    def __init__(self, inner: asyncio.StreamWriter) -> None:
        self._inner = inner

    def write(self, data: bytes) -> None:
        if data:
            _log("tx", len(data))
        return self._inner.write(data)

    def writelines(self, data) -> None:
        for chunk in data:
            if chunk:
                _log("tx", len(chunk))
        return self._inner.writelines(data)

    async def drain(self) -> None:
        await self._inner.drain()

    def close(self) -> None:
        self._inner.close()

    async def wait_closed(self) -> None:
        await self._inner.wait_closed()

    def __getattr__(self, name: str):
        return getattr(self._inner, name)
