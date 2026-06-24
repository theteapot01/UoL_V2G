#!/usr/bin/env python3
"""
plot_results.py
===============
Generate figures from reliability stress-test and multi-EV scalability
simulation outputs, suitable for dissertation inclusion.

Usage
-----
Reliability plots from raw IEC 104 CSVs:
    python tools/plot_results.py reliability \\
        Logs/iec104_loss0.csv Logs/iec104_loss5.csv Logs/iec104_loss20.csv \\
        --labels "0% loss" "5% loss" "20% loss"

Reliability plots from a pre-computed summary CSV:
    python tools/plot_results.py reliability \\
        --summary Logs/reliability_summary.csv

Multi-EV plots from per-tick CSVs:
    python tools/plot_results.py multi-ev \\
        Logs/multi_ev_1ev_20240101_120000.csv \\
        Logs/multi_ev_5ev_20240101_120000.csv \\
        Logs/multi_ev_10ev_20240101_120000.csv \\
        Logs/multi_ev_20ev_20240101_120000.csv

Multi-EV scalability chart from summary CSV only:
    python tools/plot_results.py multi-ev \\
        --summary Logs/multi_ev_summary_20240101_120000.csv

Auto-discover everything in Logs/ and generate all plots:
    python tools/plot_results.py all --dir Logs/

Output: Logs/plots/*.png  (override with --out-dir)
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError:
    print(
        "ERROR: matplotlib and numpy are required.\n"
        "       Run: pip install matplotlib numpy",
        file=sys.stderr,
    )
    sys.exit(1)

_ROOT = Path(__file__).resolve().parent.parent

# Colourblind-friendly palette (matplotlib default cycle)
_C = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b"]

_MIN_SOC_PCT   = 20.0
_MAX_SOC_PCT   = 80.0
_TRAFO_EMERG   = 80.0
_TRAFO_TARGET  = 70.0
_VOLTAGE_MIN   = 0.95


# ── Style ─────────────────────────────────────────────────────────────────────

def _setup_style(dpi: int = 150) -> None:
    plt.rcParams.update({
        "figure.dpi": dpi,
        "savefig.dpi": dpi,
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.labelsize": 10,
        "legend.fontsize": 9,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "figure.autolayout": True,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _p95(values: list) -> float:
    if not values:
        return 0.0
    sv = sorted(values)
    return sv[min(int(len(sv) * 0.95), len(sv) - 1)]


def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _save(fig, path: Path) -> None:
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


# ── Reliability — data loading ─────────────────────────────────────────────────

def _load_reliability_raw(path: Path) -> dict:
    """Load one raw IEC 104 CSV and return per-column lists."""
    transmit_ms: list[float] = []
    pandapower_ms: list[float] = []
    higher = lower = total = transmit = success = 0

    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            cmd = row["cmd"].strip()
            if cmd == "HOLD":
                continue
            transmit += 1
            transmit_ms.append(float(row["transmit_ms"]))
            pandapower_ms.append(float(row["pandapower_ms"]))
            if int(row["success"]) == 1:
                success += 1
            if cmd == "HIGHER":
                higher += 1
            elif cmd == "LOWER":
                lower += 1

    mean_tx = sum(transmit_ms) / len(transmit_ms) if transmit_ms else 0.0
    mean_pp = sum(pandapower_ms) / len(pandapower_ms) if pandapower_ms else 0.0

    return {
        "transmit_ms": transmit_ms,
        "pandapower_ms": pandapower_ms,
        "total": total,
        "transmit": transmit,
        "success": success,
        "delivery_rate": (success / transmit * 100) if transmit else 0.0,
        "mean_transmit_ms": mean_tx,
        "p95_transmit_ms": _p95(transmit_ms),
        "mean_pandapower_ms": mean_pp,
        "higher": higher,
        "lower": lower,
    }


def _load_reliability_summary(path: Path) -> list[dict]:
    """Load reliability_summary.csv produced by analyse_reliability.py."""
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "label":              row["label"],
                "transmit":           int(row["transmit_cycles"]),
                "success":            int(row["success_cycles"]),
                "delivery_rate":      float(row["delivery_rate_pct"]),
                "mean_transmit_ms":   float(row["mean_transmit_ms"]),
                "p95_transmit_ms":    float(row["p95_transmit_ms"]),
                "mean_pandapower_ms": float(row["mean_pandapower_ms"]),
                "higher":             int(row["higher_count"]),
                "lower":              int(row["lower_count"]),
            })
    return rows


# ── Reliability — plotting ─────────────────────────────────────────────────────

def plot_reliability(
    labels: list[str],
    summaries: list[dict],
    raw_data: Optional[list[dict]],
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(labels)
    x = np.arange(n)

    # Figure 1: Delivery rate bar chart
    fig, ax = plt.subplots(figsize=(max(4, n * 1.8), 4))
    bars = ax.bar(x, [s["delivery_rate"] for s in summaries],
                  color=_C[:n], edgecolor="white", width=0.5)
    ax.axhline(100, color="green", linewidth=0.8, linestyle="--", label="100% baseline")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Delivery Rate (%)")
    ax.set_title("IEC 104 Command Delivery Rate Under Packet Loss")
    ax.set_ylim(0, 115)
    ax.legend()
    for bar, s in zip(bars, summaries):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1.0,
            f"{s['delivery_rate']:.1f}%",
            ha="center", va="bottom", fontsize=9,
        )
    _save(fig, out_dir / "reliability_delivery_rate.png")

    # Figure 2: Latency comparison — mean vs p95 grouped bars
    fig, ax = plt.subplots(figsize=(max(5, n * 2.2), 4))
    width = 0.35
    ax.bar(x - width / 2, [s["mean_transmit_ms"] for s in summaries],
           width, label="Mean", color=_C[0])
    ax.bar(x + width / 2, [s["p95_transmit_ms"] for s in summaries],
           width, label="p95", color=_C[1])
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Packet Loss Scenario")
    ax.set_ylabel("Transmit Latency (ms)")
    ax.set_title("IEC 104 Transmit Latency Under Packet Loss")
    ax.legend()
    _save(fig, out_dir / "reliability_latency.png")

    # Figure 3: Latency distribution — box plots (raw data only)
    if raw_data and all(d["transmit_ms"] for d in raw_data):
        fig, ax = plt.subplots(figsize=(max(5, n * 2.2), 4))
        bp = ax.boxplot(
            [d["transmit_ms"] for d in raw_data],
            patch_artist=True, notch=False,
        )
        ax.set_xticks(range(1, n + 1))
        ax.set_xticklabels(labels)
        for patch, color in zip(bp["boxes"], _C[:n]):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)
        ax.set_xlabel("Packet Loss Scenario")
        ax.set_ylabel("Transmit Latency (ms)")
        ax.set_title("IEC 104 Transmit Latency Distribution")
        _save(fig, out_dir / "reliability_latency_dist.png")

    # Figure 4: Command mix stacked bar
    fig, ax = plt.subplots(figsize=(max(4, n * 1.8), 4))
    highs = [s["higher"] for s in summaries]
    lows  = [s["lower"]  for s in summaries]
    ax.bar(x, highs, color=_C[1], label="HIGHER (reduce charge / V2G)")
    ax.bar(x, lows,  bottom=highs, color=_C[0], label="LOWER (increase charge)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Packet Loss Scenario")
    ax.set_ylabel("Command Count")
    ax.set_title("IEC 104 Command Distribution per Scenario")
    ax.legend()
    _save(fig, out_dir / "reliability_command_mix.png")

    # Figure 5: Transmit latency time series (raw data only)
    if raw_data:
        fig, ax = plt.subplots(figsize=(9, 4))
        for d, label, color in zip(raw_data, labels, _C[:n]):
            ax.plot(d["transmit_ms"], label=label, color=color, linewidth=0.8, alpha=0.85)
        ax.set_xlabel("Transmit Cycle Index")
        ax.set_ylabel("Latency (ms)")
        ax.set_title("IEC 104 Transmit Latency Over Time")
        ax.legend()
        _save(fig, out_dir / "reliability_latency_timeseries.png")


# ── Multi-EV — data loading ────────────────────────────────────────────────────

def _load_multiev_ticks(path: Path) -> dict:
    """Load one multi_ev_*ev_*.csv per-tick file."""
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return {}

    ev_cols = sorted(
        k for k in rows[0].keys()
        if k.startswith("ev") and k.endswith("_soc_pct")
    )
    n_evs = len(ev_cols)

    return {
        "n_evs":        n_evs,
        "path":         path,
        "sim_min":      [float(r["sim_min"])          for r in rows],
        "mean_soc":     [float(r["mean_soc_pct"])     for r in rows],
        "ev_socs":      [[float(r[c]) for r in rows]  for c in ev_cols],
        "total_power":  [float(r["total_power_kw"])   for r in rows],
        "bus2_voltage": [float(r["bus2_voltage_pu"])  for r in rows],
        "trafo_loading":[float(r["trafo_loading_pct"])for r in rows],
        "line_loading": [float(r["line_loading_pct"]) for r in rows],
        "grid_stress":  [int(r["grid_stress"])        for r in rows],
        "cmd":          [r["cmd"]                     for r in rows],
    }


def _load_multiev_summary(path: Path) -> list[dict]:
    """Load multi_ev_summary_*.csv produced by multi_ev_sim.py."""
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "n_evs":            int(row["n_evs"]),
                "sim_duration_min": float(row["sim_duration_min"]),
                "evs_reached":      int(row["evs_reached_target"]),
                "mean_ttt":         _safe_float(row.get("mean_time_to_target_min")),
                "min_ttt":          _safe_float(row.get("min_time_to_target_min")),
                "max_ttt":          _safe_float(row.get("max_time_to_target_min")),
                "mean_final_soc":   float(row["mean_final_soc_pct"]),
                "peak_trafo":       float(row["peak_trafo_loading_pct"]),
                "min_voltage":      float(row["min_bus2_voltage_pu"]),
                "higher_cmds":      int(row["total_higher_cmds"]),
                "lower_cmds":       int(row["total_lower_cmds"]),
                "stress_ticks":     int(row["grid_stress_ticks"]),
            })
    return rows


def _derive_summary_from_ticks(datasets: list[dict]) -> list[dict]:
    """Build a lightweight summary from per-tick data when no summary CSV exists."""
    summaries = []
    for d in datasets:
        n_evs = d["n_evs"]
        ttt = next(
            (t for t, soc in zip(d["sim_min"], d["mean_soc"]) if soc >= _MAX_SOC_PCT),
            None,
        )
        summaries.append({
            "n_evs":            n_evs,
            "sim_duration_min": d["sim_min"][-1] if d["sim_min"] else 0.0,
            "evs_reached":      n_evs if ttt is not None else 0,
            "mean_ttt":         ttt,
            "min_ttt":          ttt,
            "max_ttt":          ttt,
            "mean_final_soc":   d["mean_soc"][-1] if d["mean_soc"] else 0.0,
            "peak_trafo":       max(d["trafo_loading"]) if d["trafo_loading"] else 0.0,
            "min_voltage":      min(d["bus2_voltage"])  if d["bus2_voltage"]  else 1.0,
            "higher_cmds":      d["cmd"].count("HIGHER"),
            "lower_cmds":       d["cmd"].count("LOWER"),
            "stress_ticks":     sum(d["grid_stress"]),
        })
    return summaries


# ── Multi-EV — plotting ────────────────────────────────────────────────────────

def plot_multiev_soc(datasets: list[dict], out_dir: Path) -> None:
    """SoC traces: one subplot per fleet size."""
    out_dir.mkdir(parents=True, exist_ok=True)
    datasets = sorted(datasets, key=lambda d: d["n_evs"])
    n = len(datasets)
    if n == 0:
        return

    cols = min(2, n)
    nrows = (n + cols - 1) // cols
    fig, axes = plt.subplots(nrows, cols, figsize=(7 * cols, 4 * nrows), squeeze=False)

    for idx, data in enumerate(datasets):
        ax = axes[idx // cols][idx % cols]
        t = data["sim_min"]

        for ev_soc in data["ev_socs"]:
            ax.plot(t, ev_soc, linewidth=0.6, alpha=0.35, color="#888888")
        ax.plot(t, data["mean_soc"], linewidth=2.0, color=_C[0], label="Mean SoC")
        ax.axhline(_MIN_SOC_PCT, color="red",   linewidth=0.9, linestyle="--", label=f"Floor ({_MIN_SOC_PCT:.0f}%)")
        ax.axhline(_MAX_SOC_PCT, color="green", linewidth=0.9, linestyle="--", label=f"Target ({_MAX_SOC_PCT:.0f}%)")

        ax.set_title(f"Fleet Size N={data['n_evs']}")
        ax.set_xlabel("Simulation Time (min)")
        ax.set_ylabel("State of Charge (%)")
        ax.set_ylim(0, 108)
        ax.legend(fontsize=8)

    for idx in range(n, nrows * cols):
        axes[idx // cols][idx % cols].set_visible(False)

    fig.suptitle("EV State of Charge Over Time — Multi-EV Fleet Scenarios", fontsize=12, y=1.01)
    _save(fig, out_dir / "multiev_soc_traces.png")


def plot_multiev_grid(datasets: list[dict], out_dir: Path) -> None:
    """Grid health: 3-panel row (power / trafo / voltage) per fleet size."""
    out_dir.mkdir(parents=True, exist_ok=True)
    datasets = sorted(datasets, key=lambda d: d["n_evs"])
    n = len(datasets)
    if n == 0:
        return

    fig, axes = plt.subplots(n, 3, figsize=(15, 4 * n), squeeze=False)

    for row, data in enumerate(datasets):
        t    = data["sim_min"]
        n_ev = data["n_evs"]

        # Panel 1: Total fleet power
        ax = axes[row][0]
        ax.plot(t, data["total_power"], linewidth=1, color=_C[0])
        ax.axhline(0, color="black", linewidth=0.5, linestyle=":")
        ax.set_ylabel("Power (kW)")
        ax.set_title(f"N={n_ev}: Total Fleet Power")
        ax.set_xlabel("Simulation Time (min)")

        # Panel 2: Transformer loading
        ax = axes[row][1]
        ax.plot(t, data["trafo_loading"], linewidth=1, color=_C[1])
        ax.axhline(_TRAFO_EMERG,  color="red",    linewidth=0.9, linestyle="--",
                   label=f"Emergency ({_TRAFO_EMERG:.0f}%)")
        ax.axhline(_TRAFO_TARGET, color="orange", linewidth=0.9, linestyle="--",
                   label=f"Target ({_TRAFO_TARGET:.0f}%)")
        ax.set_ylabel("Loading (%)")
        ax.set_title(f"N={n_ev}: Transformer Loading")
        ax.set_xlabel("Simulation Time (min)")
        ax.legend(fontsize=8)

        # Panel 3: Bus 2 voltage
        ax = axes[row][2]
        ax.plot(t, data["bus2_voltage"], linewidth=1, color=_C[2])
        ax.axhline(_VOLTAGE_MIN, color="red", linewidth=0.9, linestyle="--",
                   label=f"Min ({_VOLTAGE_MIN} pu)")
        ax.set_ylabel("Voltage (pu)")
        ax.set_title(f"N={n_ev}: Bus 2 Voltage")
        ax.set_xlabel("Simulation Time (min)")
        ax.set_ylim(0.92, 1.02)
        ax.legend(fontsize=8)

    fig.suptitle("Grid Health Metrics Over Time — Multi-EV Fleet Scenarios", fontsize=12, y=1.01)
    _save(fig, out_dir / "multiev_grid_health.png")


def plot_multiev_power_overlay(datasets: list[dict], out_dir: Path) -> None:
    """Overlay total fleet power for all fleet sizes on a single axes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    datasets = sorted(datasets, key=lambda d: d["n_evs"])
    if not datasets:
        return

    fig, ax = plt.subplots(figsize=(9, 4))
    for data, color in zip(datasets, _C):
        ax.plot(data["sim_min"], data["total_power"],
                linewidth=1.2, color=color, label=f"N={data['n_evs']}")
    ax.axhline(0, color="black", linewidth=0.5, linestyle=":")
    ax.set_xlabel("Simulation Time (min)")
    ax.set_ylabel("Total Fleet Power (kW)")
    ax.set_title("Aggregate Fleet Power Over Time (all fleet sizes)")
    ax.legend()
    _save(fig, out_dir / "multiev_power_overlay.png")


def plot_multiev_scalability(summaries: list[dict], out_dir: Path) -> None:
    """2×2 scalability summary: TTT, peak trafo, min voltage, grid stress vs fleet size."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = sorted(summaries, key=lambda s: s["n_evs"])
    fleet = [s["n_evs"] for s in summaries]
    labels = [str(f) for f in fleet]
    x = np.arange(len(fleet))

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))

    # (0,0) Time to target SoC with min/max error bars
    ax = axes[0][0]
    valid = [(i, s) for i, s in enumerate(summaries) if s["mean_ttt"] is not None]
    if valid:
        xi   = [v[0] for v in valid]
        ymid = [v[1]["mean_ttt"] for v in valid]
        ylo  = [v[1]["mean_ttt"] - v[1]["min_ttt"] for v in valid]
        yhi  = [v[1]["max_ttt"] - v[1]["mean_ttt"] for v in valid]
        ax.bar(xi, ymid, color=_C[0], alpha=0.85)
        ax.errorbar(xi, ymid, yerr=[ylo, yhi], fmt="none", color="black", capsize=5)
        ax.set_xticks(xi)
        ax.set_xticklabels([labels[i] for i in xi])
    ax.set_xlabel("Fleet Size (EVs)")
    ax.set_ylabel("Time to Target SoC (min)")
    ax.set_title(f"Mean Time-to-Target SoC ({_MAX_SOC_PCT:.0f}%)")

    # (0,1) Peak transformer loading
    ax = axes[0][1]
    ax.bar(x, [s["peak_trafo"] for s in summaries], color=_C[1])
    ax.axhline(_TRAFO_EMERG, color="red", linewidth=1, linestyle="--",
               label=f"Emergency threshold ({_TRAFO_EMERG:.0f}%)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Fleet Size (EVs)")
    ax.set_ylabel("Peak Loading (%)")
    ax.set_title("Peak Transformer Loading")
    ax.legend(fontsize=8)

    # (1,0) Minimum bus 2 voltage
    ax = axes[1][0]
    ax.bar(x, [s["min_voltage"] for s in summaries], color=_C[2])
    ax.axhline(_VOLTAGE_MIN, color="red", linewidth=1, linestyle="--",
               label=f"Minimum threshold ({_VOLTAGE_MIN} pu)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Fleet Size (EVs)")
    ax.set_ylabel("Minimum Voltage (pu)")
    ax.set_title("Minimum Bus 2 Voltage")
    ax.set_ylim(0.90, 1.02)
    ax.legend(fontsize=8)

    # (1,1) Grid stress events
    ax = axes[1][1]
    ax.bar(x, [s["stress_ticks"] for s in summaries], color=_C[3])
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel("Fleet Size (EVs)")
    ax.set_ylabel("Ticks with Grid Emergency")
    ax.set_title("Grid Stress Events")

    fig.suptitle(
        f"Multi-EV Fleet Scalability — 3-bus CIGRE Network, "
        f"Initial SoC 20–60%%, Target {_MAX_SOC_PCT:.0f}%%",
        fontsize=11,
    )
    _save(fig, out_dir / "multiev_scalability.png")


# ── CLI subcommand handlers ────────────────────────────────────────────────────

def cmd_reliability(args) -> None:
    out_dir = Path(args.out_dir)
    _setup_style(args.dpi)

    if args.summary:
        p = Path(args.summary)
        if not p.exists():
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)
        summaries = _load_reliability_summary(p)
        labels    = [s["label"] for s in summaries]
        raw_data  = None
        print(f"Loaded {len(summaries)} scenarios from {p.name}")
    elif args.files:
        paths = [Path(f) for f in args.files]
        for p in paths:
            if not p.exists():
                print(f"ERROR: {p} not found", file=sys.stderr)
                sys.exit(1)
        labels = args.labels if args.labels else [p.stem for p in paths]
        if len(labels) != len(paths):
            print("ERROR: --labels count must match file count", file=sys.stderr)
            sys.exit(1)
        raw_data  = [_load_reliability_raw(p) for p in paths]
        summaries = [
            {
                "label":              lbl,
                "transmit":           d["transmit"],
                "success":            d["success"],
                "delivery_rate":      d["delivery_rate"],
                "mean_transmit_ms":   d["mean_transmit_ms"],
                "p95_transmit_ms":    d["p95_transmit_ms"],
                "mean_pandapower_ms": d["mean_pandapower_ms"],
                "higher":             d["higher"],
                "lower":              d["lower"],
            }
            for d, lbl in zip(raw_data, labels)
        ]
        print(f"Loaded {len(paths)} raw IEC 104 CSV files.")
    else:
        print("ERROR: provide positional CSV files or --summary", file=sys.stderr)
        sys.exit(1)

    print(f"Generating reliability plots → {out_dir}/")
    plot_reliability(labels, summaries, raw_data, out_dir)


def cmd_multiev(args) -> None:
    out_dir = Path(args.out_dir)
    _setup_style(args.dpi)

    tick_datasets: list[dict] = []
    summaries: list[dict] = []

    if getattr(args, "files", None):
        for f in args.files:
            p = Path(f)
            if not p.exists():
                print(f"WARNING: {p} not found — skipping", file=sys.stderr)
                continue
            d = _load_multiev_ticks(p)
            if d:
                tick_datasets.append(d)

    if getattr(args, "summary", None):
        p = Path(args.summary)
        if not p.exists():
            print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)
        summaries = _load_multiev_summary(p)
        print(f"Loaded summary from {p.name} ({len(summaries)} fleet sizes)")

    if not summaries and tick_datasets:
        summaries = _derive_summary_from_ticks(tick_datasets)

    if not tick_datasets and not summaries:
        print("ERROR: no data loaded — provide CSV files or --summary", file=sys.stderr)
        sys.exit(1)

    print(f"Generating multi-EV plots → {out_dir}/")
    if tick_datasets:
        plot_multiev_soc(tick_datasets, out_dir)
        plot_multiev_grid(tick_datasets, out_dir)
        plot_multiev_power_overlay(tick_datasets, out_dir)
    if summaries:
        plot_multiev_scalability(summaries, out_dir)


def cmd_all(args) -> None:
    log_dir = Path(args.dir)
    out_dir = Path(args.out_dir)
    if not log_dir.exists():
        print(f"ERROR: {log_dir} not found", file=sys.stderr)
        sys.exit(1)

    _setup_style(args.dpi)

    # ── Reliability ──────────────────────────────────────────────────────────
    rel_summary = log_dir / "reliability_summary.csv"
    rel_csvs    = sorted(log_dir.glob("iec104_*.csv"))

    if rel_summary.exists():
        print(f"\n[Reliability] Loading summary: {rel_summary.name}")
        summaries = _load_reliability_summary(rel_summary)
        labels    = [s["label"] for s in summaries]
        raw_data  = None
    elif rel_csvs:
        print(f"\n[Reliability] Found {len(rel_csvs)} raw IEC 104 CSV(s)")
        raw_data  = [_load_reliability_raw(p) for p in rel_csvs]
        labels    = [p.stem for p in rel_csvs]
        summaries = [
            {
                "label":              lbl,
                "transmit":           d["transmit"],
                "success":            d["success"],
                "delivery_rate":      d["delivery_rate"],
                "mean_transmit_ms":   d["mean_transmit_ms"],
                "p95_transmit_ms":    d["p95_transmit_ms"],
                "mean_pandapower_ms": d["mean_pandapower_ms"],
                "higher":             d["higher"],
                "lower":              d["lower"],
            }
            for d, lbl in zip(raw_data, labels)
        ]
    else:
        print("[Reliability] No IEC 104 CSVs or reliability_summary.csv found — skipping")
        summaries = []
        labels    = []
        raw_data  = None

    if summaries:
        print(f"  Generating reliability plots → {out_dir}/")
        plot_reliability(labels, summaries, raw_data, out_dir)

    # ── Multi-EV ─────────────────────────────────────────────────────────────
    multi_summaries = sorted(log_dir.glob("multi_ev_summary_*.csv"))
    multi_csvs      = sorted(log_dir.glob("multi_ev_*ev_*.csv"))

    tick_datasets: list[dict] = []
    ev_summaries:  list[dict] = []

    if multi_csvs:
        print(f"\n[Multi-EV] Found {len(multi_csvs)} per-tick CSV(s)")
        for p in multi_csvs:
            d = _load_multiev_ticks(p)
            if d:
                tick_datasets.append(d)

    if multi_summaries:
        print(f"[Multi-EV] Loading summary: {multi_summaries[-1].name}")
        ev_summaries = _load_multiev_summary(multi_summaries[-1])

    if not ev_summaries and tick_datasets:
        ev_summaries = _derive_summary_from_ticks(tick_datasets)

    if tick_datasets or ev_summaries:
        print(f"  Generating multi-EV plots → {out_dir}/")
        if tick_datasets:
            plot_multiev_soc(tick_datasets, out_dir)
            plot_multiev_grid(tick_datasets, out_dir)
            plot_multiev_power_overlay(tick_datasets, out_dir)
        if ev_summaries:
            plot_multiev_scalability(ev_summaries, out_dir)
    else:
        print("[Multi-EV] No multi-EV CSVs found — skipping")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Shared options (available on all subcommands)
    _shared = argparse.ArgumentParser(add_help=False)
    _shared.add_argument(
        "--out-dir", default=str(_ROOT / "Logs" / "plots"),
        metavar="DIR", dest="out_dir",
        help="Output directory for plots (default: Logs/plots/)",
    )
    _shared.add_argument(
        "--dpi", type=int, default=150,
        help="Plot resolution in DPI (default: 150; use 300 for print)",
    )

    parser = argparse.ArgumentParser(
        description="Plot V2G prototype evaluation results.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── reliability ──────────────────────────────────────────────────────────
    p_rel = sub.add_parser(
        "reliability",
        parents=[_shared],
        help="Plot IEC 104 reliability stress-test results.",
    )
    p_rel.add_argument(
        "files", nargs="*", metavar="CSV",
        help="Raw IEC 104 CSV files, one per scenario (ordered baseline → worst).",
    )
    p_rel.add_argument(
        "--labels", nargs="*", metavar="LABEL",
        help="Human-readable label per file (e.g. '0%% loss'). Defaults to file stem.",
    )
    p_rel.add_argument(
        "--summary", metavar="PATH",
        help="Load from reliability_summary.csv instead of raw files.",
    )

    # ── multi-ev ─────────────────────────────────────────────────────────────
    p_multi = sub.add_parser(
        "multi-ev",
        parents=[_shared],
        help="Plot multi-EV scalability simulation results.",
    )
    p_multi.add_argument(
        "files", nargs="*", metavar="CSV",
        help="Per-tick multi_ev_*ev_*.csv files.",
    )
    p_multi.add_argument(
        "--summary", metavar="PATH",
        help="Load scalability summary from multi_ev_summary_*.csv.",
    )

    # ── all ───────────────────────────────────────────────────────────────────
    p_all = sub.add_parser(
        "all",
        parents=[_shared],
        help="Auto-discover and plot all results in Logs/.",
    )
    p_all.add_argument(
        "--dir", default=str(_ROOT / "Logs"),
        metavar="DIR",
        help="Directory to search (default: Logs/)",
    )

    args = parser.parse_args()

    if args.command == "reliability":
        cmd_reliability(args)
    elif args.command == "multi-ev":
        cmd_multiev(args)
    elif args.command == "all":
        cmd_all(args)


if __name__ == "__main__":
    main()
