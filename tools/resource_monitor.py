#!/usr/bin/env python3
"""
resource_monitor.py
===================
Log system and per-process CPU / memory usage during a V2G session.

Run this alongside grid.py on the grid Pi (or charger.py on the charger Pi)
to capture evidence of lightweight operation on constrained hardware, directly
addressing the "resource efficiency" project objective.

Output: Logs/resource_<session>.csv

Columns
-------
  timestamp_unix, timestamp_iso,
  system_cpu_pct, system_mem_used_mb, system_mem_available_mb,
  [<name>_pid, <name>_cpu_pct, <name>_rss_mb  — one set per --process arg]

Usage
-----
    # Monitor system only:
    python tools/resource_monitor.py

    # Track grid.py and charger.py processes as well:
    python tools/resource_monitor.py --process grid.py --process charger.py

    # Stop automatically after 10 minutes:
    python tools/resource_monitor.py --duration 600 --process grid.py

    # Custom output location:
    python tools/resource_monitor.py --out Logs/my_session.csv

    # Quick plot from an existing CSV:
    python tools/resource_monitor.py --plot-only Logs/resource_20240101_120000.csv
"""

import argparse
import csv
import shlex
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ImportError:
    print(
        "ERROR: psutil is required.\n"
        "       Run: pip install psutil",
        file=sys.stderr,
    )
    sys.exit(1)

_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _ROOT / "Logs"
_SESSION = datetime.now().strftime("%Y%m%d_%H%M%S")


# ── Process discovery ─────────────────────────────────────────────────────────

def _find_process(name: str) -> psutil.Process | None:
    """Return the first running process whose command line contains *name*."""
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if any(name in part for part in (proc.info["cmdline"] or [])):
                return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


def _proc_stats(proc: psutil.Process | None) -> tuple[int, float, float]:
    """Return (pid, cpu_pct, rss_mb) for a process, or zeros if gone."""
    if proc is None:
        return 0, 0.0, 0.0
    try:
        with proc.oneshot():
            cpu = proc.cpu_percent()
            rss = proc.memory_info().rss / (1024 * 1024)
        return proc.pid, cpu, rss
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0, 0.0, 0.0


# ── Monitoring loop ───────────────────────────────────────────────────────────

def monitor(
    *,
    process_names: list[str],
    interval_s: float,
    duration_s: float | None,
    out_path: Path,
    launched_pid: int | None = None,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Build CSV header dynamically
    fixed_cols = [
        "timestamp_unix", "timestamp_iso",
        "system_cpu_pct", "system_mem_used_mb", "system_mem_available_mb",
    ]
    proc_col_sets = [
        [f"{n}_pid", f"{n}_cpu_pct", f"{n}_rss_mb"]
        for n in process_names
    ]
    header = fixed_cols + [c for cols in proc_col_sets for c in cols]

    stop = threading.Event()

    def _sighandler(sig, frame):
        print("\nStopping monitor ...")
        stop.set()

    signal.signal(signal.SIGINT,  _sighandler)
    signal.signal(signal.SIGTERM, _sighandler)

    # Discover tracked processes (refresh on each sample to handle restarts)
    tracked: dict[str, psutil.Process | None] = {n: None for n in process_names}
    # Seed the launched process by PID directly to avoid a name-search race.
    if launched_pid is not None and process_names:
        try:
            tracked[process_names[0]] = psutil.Process(launched_pid)
        except psutil.NoSuchProcess:
            pass

    print(f"Resource monitor started. Writing to: {out_path}")
    if process_names:
        print(f"  Tracking processes: {', '.join(process_names)}")
    print(f"  Interval: {interval_s}s  |  Press Ctrl+C to stop.\n")

    start = time.monotonic()
    sample_count = 0

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        f.flush()

        # Prime CPU % (first call returns 0 by psutil design)
        psutil.cpu_percent(interval=None)
        for name in process_names:
            tracked[name] = _find_process(name)
            if tracked[name]:
                tracked[name].cpu_percent()

        while not stop.is_set():
            if duration_s is not None and time.monotonic() - start >= duration_s:
                break

            time.sleep(interval_s)

            now_unix = time.time()
            now_iso  = datetime.fromtimestamp(now_unix, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            cpu_sys  = psutil.cpu_percent(interval=None)
            mem      = psutil.virtual_memory()
            mem_used = mem.used / (1024 * 1024)
            mem_avail = mem.available / (1024 * 1024)

            row = [now_unix, now_iso, f"{cpu_sys:.1f}", f"{mem_used:.1f}", f"{mem_avail:.1f}"]

            for name in process_names:
                # Re-discover if previously not found or process died
                if tracked[name] is None or not tracked[name].is_running():
                    tracked[name] = _find_process(name)
                    if tracked[name]:
                        tracked[name].cpu_percent()  # prime

                pid, cpu, rss = _proc_stats(tracked[name])
                row += [pid, f"{cpu:.1f}", f"{rss:.1f}"]

            writer.writerow(row)
            f.flush()
            sample_count += 1

            if sample_count % 12 == 0:
                print(f"  {now_iso}  CPU {cpu_sys:.1f}%  MEM {mem_used:.0f}/{mem.total/1e6:.0f} MB")

    elapsed = time.monotonic() - start
    print(f"\nMonitor stopped. {sample_count} samples in {elapsed:.0f}s → {out_path}")


# ── Plotting ──────────────────────────────────────────────────────────────────

def plot_resource_csv(csv_path: Path, out_dir: Path | None = None) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plot (pip install matplotlib)")
        return

    rows: list[dict] = []
    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("No data in CSV — nothing to plot.")
        return

    out_dir = out_dir or csv_path.parent / "plots"
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = float(rows[0]["timestamp_unix"])
    t_min = [(float(r["timestamp_unix"]) - t0) / 60.0 for r in rows]
    cpu   = [float(r["system_cpu_pct"])    for r in rows]
    mem   = [float(r["system_mem_used_mb"]) for r in rows]

    # Detect per-process columns
    proc_cols = [k for k in rows[0].keys() if k.endswith("_rss_mb")]
    proc_names = [k[:-len("_rss_mb")] for k in proc_cols]

    n_extra = len(proc_names)
    n_panels = 2 + n_extra
    fig, axes = plt.subplots(n_panels, 1, figsize=(9, 3 * n_panels), squeeze=False)
    plt.rcParams.update({"axes.grid": True, "grid.alpha": 0.3, "figure.autolayout": True})

    axes[0][0].plot(t_min, cpu, linewidth=1, color="#1f77b4")
    axes[0][0].set_ylabel("CPU (%)")
    axes[0][0].set_title("System CPU Usage")
    axes[0][0].set_ylim(0, max(100, max(cpu) * 1.1))

    axes[1][0].plot(t_min, mem, linewidth=1, color="#ff7f0e")
    axes[1][0].set_ylabel("Memory Used (MB)")
    axes[1][0].set_title("System Memory Usage")

    colours = ["#2ca02c", "#d62728", "#9467bd"]
    for i, name in enumerate(proc_names):
        rss_col = f"{name}_rss_mb"
        cpu_col = f"{name}_cpu_pct"
        rss = [float(r[rss_col]) for r in rows]
        cpu_p = [float(r[cpu_col]) for r in rows]
        ax = axes[2 + i][0]
        ax2 = ax.twinx()
        ax.plot(t_min, rss,   linewidth=1, color=colours[i % len(colours)], label="RSS MB")
        ax2.plot(t_min, cpu_p, linewidth=1, color=colours[i % len(colours)],
                 linestyle="--", alpha=0.6, label="CPU %")
        ax.set_ylabel("RSS (MB)")
        ax2.set_ylabel("CPU (%)")
        ax.set_title(f"Process: {name}")
        ax.legend(loc="upper left", fontsize=8)
        ax2.legend(loc="upper right", fontsize=8)

    for ax_row in axes:
        ax_row[0].set_xlabel("Session Time (min)")

    stem = csv_path.stem
    out_path = out_dir / f"{stem}_plot.png"
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    plt.close(fig)
    print(f"Plot saved: {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Log CPU and memory usage during a V2G session.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--process", "-p", action="append", dest="processes", default=[],
        metavar="NAME",
        help="Process name/pattern to track (repeatable, e.g. --process grid.py).",
    )
    parser.add_argument(
        "--interval", "-i", type=float, default=5.0, metavar="S",
        help="Sampling interval in seconds.",
    )
    parser.add_argument(
        "--duration", "-d", type=float, default=None, metavar="S",
        help="Stop after this many seconds (default: run until Ctrl+C).",
    )
    parser.add_argument(
        "--out", "-o", default=None, metavar="PATH",
        help=f"Output CSV path (default: Logs/resource_<session>.csv).",
    )
    parser.add_argument(
        "--plot", action="store_true",
        help="Generate a matplotlib figure immediately after monitoring ends.",
    )
    parser.add_argument(
        "--plot-only", metavar="CSV",
        help="Skip monitoring; generate a plot from an existing CSV and exit.",
    )
    parser.add_argument(
        "--launch", "-l", default=None, metavar="CMD",
        help=(
            "Launch this command as a subprocess and track it automatically. "
            "The monitor exits when the subprocess exits (or Ctrl+C kills both). "
            "Example: --launch 'python grid.py'"
        ),
    )
    args = parser.parse_args()

    if args.plot_only:
        plot_resource_csv(Path(args.plot_only))
        return

    launched_proc: subprocess.Popen | None = None
    if args.launch:
        cmd = shlex.split(args.launch)
        launched_proc = subprocess.Popen(cmd)
        # Auto-add to tracked names so it gets its own CSV columns.
        launch_name = Path(cmd[-1]).name
        if launch_name not in args.processes:
            args.processes.insert(0, launch_name)
        print(f"Launched: {args.launch}  (PID {launched_proc.pid})")

    out_path = Path(args.out) if args.out else _LOG_DIR / f"resource_{_SESSION}.csv"

    try:
        monitor(
            process_names=args.processes,
            interval_s=args.interval,
            duration_s=args.duration,
            out_path=out_path,
            launched_pid=launched_proc.pid if launched_proc else None,
        )
    finally:
        if launched_proc and launched_proc.poll() is None:
            print("Stopping launched process ...")
            launched_proc.send_signal(signal.SIGINT)
            try:
                launched_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                launched_proc.kill()

    if args.plot:
        plot_resource_csv(out_path)


if __name__ == "__main__":
    main()
