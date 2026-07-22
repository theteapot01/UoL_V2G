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

Combined latency validation (IEC 104 / pandapower / control / ISO 15118):
    python tools/plot_results.py latency \\
        --iec104 Logs/iec104_20240101_120000.csv \\
        --control-latency Logs/control_latency_20240101_120000.csv \\
        --iso15118 Logs/iso15118_20240101_120000.csv

    python tools/plot_results.py latency --dir Logs/   # auto-picks latest of each

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


def _drop_empty_sessions(
    labels: list[str],
    summaries: list[dict],
    raw_data: Optional[list[dict]],
) -> tuple:
    """
    Drop sessions with zero transmit cycles (e.g. a process started and
    stopped before any command was staged) before plotting. Plotting these
    alongside real sessions renders a "0.0%" delivery-rate bar indistinguishable
    from an actual failure, when it's really "no data was collected."
    """
    keep = [i for i, s in enumerate(summaries) if s["transmit"] > 0]
    dropped = len(summaries) - len(keep)
    if dropped:
        print(f"  Excluding {dropped} empty session(s) with 0 transmit cycles from plots")
    labels    = [labels[i] for i in keep]
    summaries = [summaries[i] for i in keep]
    raw_data  = [raw_data[i] for i in keep] if raw_data else None
    return labels, summaries, raw_data


# ── Reliability — plotting ─────────────────────────────────────────────────────

def plot_reliability(
    labels: list[str],
    summaries: list[dict],
    raw_data: Optional[list[dict]],
    out_dir: Path,
    scenario_label: str = "Session",
) -> None:
    """
    scenario_label names what each category on the x-axis actually is. It
    defaults to the neutral "Session" because, absent real tc-netem loss
    injection (tools/reliability_test.sh), the categories are just logged
    sessions, not loss scenarios — titling the chart "Under Packet Loss" for
    baseline data would misrepresent what was actually tested. Pass
    scenario_label="Packet Loss Scenario" once genuine labelled-loss CSVs
    (e.g. "0% loss", "5% loss") are fed in.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(labels)
    x = np.arange(n)
    rot = 25 if n > 4 else 0

    def _tick(ax):
        ax.set_xticks(x)
        if rot:
            ax.set_xticklabels(labels, rotation=rot, ha="right")
        else:
            ax.set_xticklabels(labels)

    # Figure 1: Delivery rate bar chart
    fig, ax = plt.subplots(figsize=(max(5, n * 1.9), 4.5))
    bars = ax.bar(x, [s["delivery_rate"] for s in summaries],
                  color=_C[:n] * (n // len(_C) + 1), edgecolor="white", width=0.5)
    ax.axhline(100, color="green", linewidth=0.8, linestyle="--", label="100% baseline")
    _tick(ax)
    ax.set_ylabel("Delivery Rate (%)")
    ax.set_title(f"IEC 104 Command Delivery Rate by {scenario_label}")
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
    fig, ax = plt.subplots(figsize=(max(6, n * 2.3), 4.5))
    width = 0.35
    ax.bar(x - width / 2, [s["mean_transmit_ms"] for s in summaries],
           width, label="Mean", color=_C[0])
    ax.bar(x + width / 2, [s["p95_transmit_ms"] for s in summaries],
           width, label="p95", color=_C[1])
    _tick(ax)
    ax.set_xlabel(scenario_label)
    ax.set_ylabel("Transmit Latency (ms)")
    ax.set_title(f"IEC 104 Transmit Latency by {scenario_label}")
    ax.legend()
    _save(fig, out_dir / "reliability_latency.png")

    # Figure 3: Latency distribution — box plots (raw data only)
    if raw_data and all(d["transmit_ms"] for d in raw_data):
        fig, ax = plt.subplots(figsize=(max(6, n * 2.3), 4.5))
        bp = ax.boxplot(
            [d["transmit_ms"] for d in raw_data],
            patch_artist=True, notch=False,
        )
        ax.set_xticks(range(1, n + 1))
        if rot:
            ax.set_xticklabels(labels, rotation=rot, ha="right")
        else:
            ax.set_xticklabels(labels)
        for patch, color in zip(bp["boxes"], _C[:n] * (n // len(_C) + 1)):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)
        ax.set_xlabel(scenario_label)
        ax.set_ylabel("Transmit Latency (ms)")
        ax.set_title("IEC 104 Transmit Latency Distribution")
        _save(fig, out_dir / "reliability_latency_dist.png")

    # Figure 4: Command mix stacked bar
    fig, ax = plt.subplots(figsize=(max(5, n * 1.9), 4.5))
    highs = [s["higher"] for s in summaries]
    lows  = [s["lower"]  for s in summaries]
    ax.bar(x, highs, color=_C[0], label="HIGHER (increase charge)")
    ax.bar(x, lows,  bottom=highs, color=_C[1], label="LOWER (reduce charge / V2G)")
    _tick(ax)
    ax.set_xlabel(scenario_label)
    ax.set_ylabel("Command Count")
    ax.set_title(f"IEC 104 Command Distribution by {scenario_label}")
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


# ── Latency — data loading ─────────────────────────────────────────────────────

def _load_control_latency_csv(path: Path) -> list:
    """Load control_latency_*.csv and return the latency_ms column."""
    values: list[float] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            v = _safe_float(row.get("latency_ms"))
            if v is not None:
                values.append(v)
    return values


def _load_iso15118_loop_csv(path: Path) -> list:
    """Load iso15118_*.csv and return the loop_ms column (blank/startup rows skipped)."""
    values: list[float] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            v = _safe_float(row.get("loop_ms"))
            if v is not None:
                values.append(v)
    return values


def _robust_range(values: list) -> tuple:
    """
    Full data range, unless one or two extreme values are disproportionate
    enough to flatten the rest of the distribution to invisibility — e.g. the
    first pandapower call after cold start, which pays a one-off JIT/import
    cost the following 1000+ calls don't. Detected as max > 5x the 99th
    percentile; in that case the view is capped at p99 and the excluded
    count is reported in the panel annotation rather than silently dropped.
    Ordinary long right tails (e.g. control latency's spread up to ~1 s,
    which is itself the evidence being validated) are left untouched — this
    only trims genuine one-off transients, not skew.
    """
    sv = sorted(values)
    n = len(sv)
    lo, hi = sv[0], sv[-1]
    if n < 20:
        return lo, hi, 0
    p99 = sv[int(n * 0.99)]
    if p99 <= 0 or hi <= p99 * 5:
        return lo, hi, 0
    n_excluded = sum(1 for v in sv if v > p99)
    return lo, p99, n_excluded


# ── Latency — plotting ──────────────────────────────────────────────────────────

def plot_latency(
    iec104_transmit_ms: list,
    pandapower_ms: list,
    control_latency_ms: list,
    iso15118_loop_ms: list,
    out_dir: Path,
    session_label: str = "",
) -> None:
    """
    One combined figure, four latency metrics as small multiples. Each panel
    keeps its own x-axis rather than sharing one scale across the figure —
    control latency (hundreds of ms) and ISO 15118 loop time (under 2 ms) are
    two orders of magnitude apart, so a shared axis would flatten the faster
    metrics to an invisible sliver.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    panels = [
        ("IEC 104 Command Transmit", iec104_transmit_ms, _C[0]),
        ("Pandapower Load-Flow Compute", pandapower_ms, _C[1]),
        ("Control Latency (Setpoint → EVSE Limit)", control_latency_ms, _C[3]),
        ("ISO 15118 Charge-Loop Iteration", iso15118_loop_ms, _C[2]),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    axes = axes.flatten()

    any_data = False
    for ax, (title, values, color) in zip(axes, panels):
        if not values:
            ax.set_visible(False)
            continue
        any_data = True
        mean_v = sum(values) / len(values)
        p95_v  = _p95(values)
        max_v  = max(values)
        lo, hi, n_excluded = _robust_range(values)
        bins   = min(40, max(10, len(values) // 10))
        ax.hist(values, bins=bins, range=(lo, hi), color=color, alpha=0.75, edgecolor="white")
        ax.axvline(mean_v, color="black", linewidth=1.2, linestyle="--",
                   label=f"Mean {mean_v:.2f} ms")
        if lo <= p95_v <= hi:
            ax.axvline(p95_v, color="red", linewidth=1.2, linestyle=":",
                       label=f"p95 {p95_v:.2f} ms")
        else:
            ax.axvline(hi, color="red", linewidth=1.2, linestyle=":",
                       label=f"p95 {p95_v:.2f} ms (off-scale)")
        ax.set_xlim(lo, hi)
        ax.set_title(title)
        ax.set_xlabel("Latency (ms)")
        ax.set_ylabel("Sample Count")
        ax.legend(fontsize=8)
        note = f"n={len(values)}\nmax={max_v:.2f} ms"
        if n_excluded:
            note += f"\n{n_excluded} outlier(s) off-scale"
        ax.annotate(
            note,
            xy=(0.98, 0.97), xycoords="axes fraction",
            ha="right", va="top", fontsize=8,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.7),
        )

    if not any_data:
        plt.close(fig)
        print("  No latency data to plot — skipping")
        return

    title_suffix = f" — {session_label}" if session_label else ""
    fig.suptitle(f"Latency Validation Across Protocol Layers{title_suffix}", fontsize=12)
    stem = f"latency_validation_{session_label}" if session_label else "latency_validation"
    _save(fig, out_dir / f"{stem}.png")


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


# ── Degradation — data loading & plotting ─────────────────────────────────────

def _load_degradation_csv(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "cycle":          int(row["cycle"]),
                "soh_pct":        float(row["soh_pct"]),
                "throughput_kwh": float(row["throughput_kwh"]),
                "efc":            float(row["efc"]),
                "energy_in_kwh":  float(row["energy_in_kwh"]),
                "energy_out_kwh": float(row["energy_out_kwh"]),
                "peak_temp_c":    float(row["peak_temp_c"]),
                "end_temp_c":     float(row["end_temp_c"]),
            })
    return rows


def plot_degradation(
    datasets: list[tuple[str, list[dict]]],   # (label, records)
    out_dir: Path,
) -> None:
    """Two-panel figure: SOH vs cycles | SOH vs EFC; plus temperature figure."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Figure 1: SOH vs cycle count and vs EFC
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    for (label, recs), colour in zip(datasets, _C):
        cycles = [r["cycle"]   for r in recs]
        efcs   = [r["efc"]     for r in recs]
        sohs   = [r["soh_pct"] for r in recs]
        ax1.plot(cycles, sohs, label=label, color=colour, linewidth=1.5)
        ax2.plot(efcs,   sohs, label=label, color=colour, linewidth=1.5)

    for ax in (ax1, ax2):
        ax.axhline(80.0, color="red", linewidth=0.9, linestyle="--", label="EOL (80% SOH)")
        ax.set_ylabel("State of Health (%)")
        ax.set_ylim(76, 101)
        ax.legend(fontsize=8)
    ax1.set_xlabel("Charge/Discharge Cycle")
    ax1.set_title("SOH Degradation vs Cycle Count")
    ax2.set_xlabel("Equivalent Full Cycles (EFC)")
    ax2.set_title("SOH Degradation vs EFC")

    _save(fig, out_dir / "degradation_soh.png")

    # Figure 2: Peak temperature vs cycle
    fig, ax = plt.subplots(figsize=(8, 4))
    for (label, recs), colour in zip(datasets, _C):
        ax.plot([r["cycle"] for r in recs], [r["peak_temp_c"] for r in recs],
                label=label, color=colour, linewidth=1.2)
    ax.set_xlabel("Charge/Discharge Cycle")
    ax.set_ylabel("Peak Pack Temperature (°C)")
    ax.set_title("Peak Temperature per Cycle")
    ax.legend(fontsize=8)
    _save(fig, out_dir / "degradation_temperature.png")


# ── Resource — data loading & plotting ────────────────────────────────────────

def _load_resource_csv(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def plot_resource(rows: list[dict], out_dir: Path) -> None:
    """CPU and memory usage over session time."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not rows:
        return

    t0    = float(rows[0]["timestamp_unix"])
    t_min = [(float(r["timestamp_unix"]) - t0) / 60.0 for r in rows]
    cpu   = [float(r["system_cpu_pct"])      for r in rows]
    mem   = [float(r["system_mem_used_mb"])  for r in rows]

    proc_cols = [k for k in rows[0].keys() if k.endswith("_rss_mb")]
    proc_names = [k[:-len("_rss_mb")] for k in proc_cols]
    n_extra = len(proc_names)

    fig, axes = plt.subplots(2 + n_extra, 1, figsize=(9, 3 * (2 + n_extra)), squeeze=False)

    axes[0][0].plot(t_min, cpu, linewidth=1, color=_C[0])
    axes[0][0].set_ylabel("CPU (%)")
    axes[0][0].set_title("System CPU Usage")
    axes[0][0].set_ylim(0, max(100, max(cpu) * 1.1))

    axes[1][0].plot(t_min, mem, linewidth=1, color=_C[1])
    axes[1][0].set_ylabel("Memory Used (MB)")
    axes[1][0].set_title("System Memory Usage")

    for i, name in enumerate(proc_names):
        ax = axes[2 + i][0]
        ax2 = ax.twinx()
        rss   = [float(r[f"{name}_rss_mb"])  for r in rows]
        cpu_p = [float(r[f"{name}_cpu_pct"]) for r in rows]
        ax.plot(t_min, rss,   linewidth=1, color=_C[i % len(_C)], label="RSS MB")
        ax2.plot(t_min, cpu_p, linewidth=1, color=_C[i % len(_C)],
                 linestyle="--", alpha=0.6, label="CPU %")
        ax.set_ylabel("RSS (MB)")
        ax2.set_ylabel("CPU (%)")
        ax.set_title(f"Process: {name}")
        ax.legend(loc="upper left", fontsize=8)
        ax2.legend(loc="upper right", fontsize=8)

    for ax_row in axes:
        ax_row[0].set_xlabel("Session Time (min)")

    _save(fig, out_dir / "resource_usage.png")


# ── Voltage stabilisation — data loading & plotting ──────────────────────────

def _load_voltage_stab_csv(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "timestamp_unix":  float(row["timestamp_unix"]),
                "bus2_voltage_pu": float(row["bus2_voltage_pu"]),
                "setpoint_pu":     float(row["setpoint_pu"]),
                "error_pu":        float(row["error_pu"]),
                "bg_load_kw":      float(row["bg_load_kw"]),
                "cmd":             row["cmd"].strip(),
            })
    return rows


def plot_voltage_stab(rows: list[dict], out_dir: Path, label: str = "") -> None:
    """Two-panel figure: voltage time series + error distribution."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if not rows:
        return

    t0       = rows[0]["timestamp_unix"]
    t_min    = [(r["timestamp_unix"] - t0) / 60.0 for r in rows]
    volts    = [r["bus2_voltage_pu"] for r in rows]
    errors   = [r["error_pu"]        for r in rows]
    bg_load  = [r["bg_load_kw"]      for r in rows]
    setpoint = rows[0]["setpoint_pu"]
    deadband = max(abs(r["error_pu"]) for r in rows[:1]) if rows else 0.003

    # Read deadband from actual data spread near setpoint
    # Use a fixed value matching VDROOP_DEADBAND = 0.003 from iec104_panda.py
    _DEADBAND = 0.003

    sse  = sum(e ** 2 for e in errors)
    rmse = (sse / len(errors)) ** 0.5 if errors else 0.0
    mae  = sum(abs(e) for e in errors) / len(errors) if errors else 0.0
    title_suffix = f" — {label}" if label else ""

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))

    # ── Left: voltage time series ─────────────────────────────────────────────
    ax1.plot(t_min, volts, linewidth=1.0, color=_C[0], label="Bus 2 voltage")
    ax1.axhline(setpoint, color="red", linewidth=1.2, linestyle="--",
                label=f"Setpoint ({setpoint:.3f} pu)")
    ax1.axhspan(setpoint - _DEADBAND, setpoint + _DEADBAND,
                alpha=0.12, color="green", label=f"Deadband (±{_DEADBAND} pu)")

    ax1b = ax1.twinx()
    ax1b.plot(t_min, bg_load, linewidth=0.8, color=_C[3], alpha=0.5, linestyle=":")
    ax1b.set_ylabel("Background Disturbance (kW)", color=_C[3], fontsize=8)
    ax1b.tick_params(axis="y", labelcolor=_C[3], labelsize=8)

    ax1.set_xlabel("Session Time (min)")
    ax1.set_ylabel("Bus Voltage (pu)")
    ax1.set_title(f"Bus 2 Voltage vs Setpoint{title_suffix}")
    ax1.legend(fontsize=8, loc="lower left")
    ax1.annotate(
        f"RMSE={rmse*1000:.3f} mpu\nMAE={mae*1000:.3f} mpu",
        xy=(0.02, 0.97), xycoords="axes fraction",
        va="top", fontsize=8,
        bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.7),
    )

    # ── Right: error distribution ─────────────────────────────────────────────
    ax2.hist([e * 1000 for e in errors], bins=40, color=_C[0], alpha=0.75, edgecolor="white")
    ax2.axvline(0, color="red", linewidth=1.2, linestyle="--", label="Zero error")
    ax2.axvspan(-_DEADBAND * 1000, _DEADBAND * 1000,
                alpha=0.12, color="green", label=f"Deadband (±{_DEADBAND*1000:.1f} mpu)")
    ax2.set_xlabel("Voltage Error (mpu = 0.001 pu)")
    ax2.set_ylabel("Sample Count")
    ax2.set_title("Voltage Error Distribution")
    ax2.legend(fontsize=8)

    fig.suptitle(f"Voltage Stabilisation Accuracy{title_suffix}", fontsize=12)
    stem = f"voltage_stab_{label}" if label else "voltage_stab"
    _save(fig, out_dir / f"{stem}.png")


# ── Multi-EV V2G vs no-V2G comparison ─────────────────────────────────────────

def plot_multiev_comparison(
    v2g_summaries: list[dict],
    nov2g_summaries: list[dict],
    out_dir: Path,
) -> None:
    """Side-by-side bars: V2G enabled vs charge-only (no V2G) per fleet size."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Align on common fleet sizes
    v2g_by_n   = {s["n_evs"]: s for s in v2g_summaries}
    nov2g_by_n = {s["n_evs"]: s for s in nov2g_summaries}
    fleet_sizes = sorted(set(v2g_by_n) & set(nov2g_by_n))
    if not fleet_sizes:
        print("  No common fleet sizes between V2G and no-V2G summaries — skipping comparison plot")
        return

    labels = [str(n) for n in fleet_sizes]
    x = np.arange(len(fleet_sizes))
    w = 0.35

    metrics = [
        ("peak_trafo",   "Peak Trafo Loading (%)",     _TRAFO_EMERG,  "Emergency (80%)"),
        ("min_voltage",  "Min Bus Voltage (pu)",        _VOLTAGE_MIN,  "Min threshold (0.95 pu)"),
        ("stress_ticks", "Grid Stress Events (ticks)",  None,          None),
        ("mean_final_soc","Mean Final SoC (%)",         None,          None),
    ]

    fig, axes = plt.subplots(2, 2, figsize=(13, 8))
    axes = axes.flatten()

    for ax, (key, ylabel, threshold, threshold_label) in zip(axes, metrics):
        v2g_vals   = [v2g_by_n[n].get(key, 0) or 0   for n in fleet_sizes]
        nov2g_vals = [nov2g_by_n[n].get(key, 0) or 0 for n in fleet_sizes]
        ax.bar(x - w/2, v2g_vals,   w, label="V2G enabled",     color=_C[0], alpha=0.85)
        ax.bar(x + w/2, nov2g_vals, w, label="Charge only",     color=_C[2], alpha=0.85)
        if threshold is not None:
            ax.axhline(threshold, color="red", linewidth=0.9, linestyle="--",
                       label=threshold_label)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_xlabel("Fleet Size (EVs)")
        ax.set_ylabel(ylabel)
        ax.set_title(ylabel)
        ax.legend(fontsize=8)

    fig.suptitle("V2G Enabled vs Charge-Only (No V2G) — Grid Impact Comparison", fontsize=12)
    _save(fig, out_dir / "multiev_v2g_comparison.png")


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

    labels, summaries, raw_data = _drop_empty_sessions(labels, summaries, raw_data)
    if not summaries:
        print("ERROR: no non-empty sessions to plot", file=sys.stderr)
        sys.exit(1)

    print(f"Generating reliability plots → {out_dir}/")
    plot_reliability(labels, summaries, raw_data, out_dir, scenario_label=args.scenario_label)


def cmd_latency(args) -> None:
    out_dir = Path(args.out_dir)
    _setup_style(args.dpi)

    iec104_path  = Path(args.iec104) if args.iec104 else None
    control_path = Path(args.control_latency) if args.control_latency else None
    iso_path     = Path(args.iso15118) if args.iso15118 else None

    if getattr(args, "dir", None):
        log_dir = Path(args.dir)
        if not iec104_path:
            found = sorted(log_dir.glob("iec104_*.csv"))
            iec104_path = found[-1] if found else None
        if not control_path:
            found = sorted(log_dir.glob("control_latency_*.csv"))
            control_path = found[-1] if found else None
        if not iso_path:
            found = sorted(p for p in log_dir.glob("iso15118_*.csv") if "_bytes_" not in p.name)
            iso_path = found[-1] if found else None

    if not any([iec104_path, control_path, iso_path]):
        print("ERROR: provide --iec104/--control-latency/--iso15118 or --dir", file=sys.stderr)
        sys.exit(1)

    iec104_transmit_ms: list = []
    pandapower_ms: list = []
    control_latency_ms: list = []
    iso15118_loop_ms: list = []

    if iec104_path and iec104_path.exists():
        d = _load_reliability_raw(iec104_path)
        iec104_transmit_ms = d["transmit_ms"]
        pandapower_ms      = d["pandapower_ms"]
        print(f"  IEC 104: {len(iec104_transmit_ms)} transmit samples from {iec104_path.name}")
    elif iec104_path:
        print(f"WARNING: {iec104_path} not found — skipping IEC 104 panel", file=sys.stderr)

    if control_path and control_path.exists():
        control_latency_ms = _load_control_latency_csv(control_path)
        print(f"  Control latency: {len(control_latency_ms)} samples from {control_path.name}")
    elif control_path:
        print(f"WARNING: {control_path} not found — skipping control-latency panel", file=sys.stderr)

    if iso_path and iso_path.exists():
        iso15118_loop_ms = _load_iso15118_loop_csv(iso_path)
        print(f"  ISO 15118 loop: {len(iso15118_loop_ms)} samples from {iso_path.name}")
    elif iso_path:
        print(f"WARNING: {iso_path} not found — skipping ISO 15118 panel", file=sys.stderr)

    if not any([iec104_transmit_ms, pandapower_ms, control_latency_ms, iso15118_loop_ms]):
        print("ERROR: no latency data loaded from any source", file=sys.stderr)
        sys.exit(1)

    print(f"Generating latency validation plot → {out_dir}/")
    plot_latency(
        iec104_transmit_ms, pandapower_ms, control_latency_ms, iso15118_loop_ms,
        out_dir, session_label=args.label or "",
    )


def cmd_degradation(args) -> None:
    out_dir = Path(args.out_dir)
    _setup_style(args.dpi)

    # Each positional file is one scenario CSV; label defaults to stem
    if not args.files:
        print("ERROR: provide degradation_*.csv files", file=sys.stderr)
        sys.exit(1)

    datasets: list[tuple[str, list[dict]]] = []
    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"WARNING: {p} not found — skipping", file=sys.stderr)
            continue
        label = p.stem.replace("degradation_", "").replace("_", " ").title()
        recs  = _load_degradation_csv(p)
        if recs:
            datasets.append((label, recs))

    if not datasets:
        print("ERROR: no degradation data loaded", file=sys.stderr)
        sys.exit(1)

    print(f"Generating degradation plots ({len(datasets)} scenarios) → {out_dir}/")
    plot_degradation(datasets, out_dir)


def cmd_resource(args) -> None:
    out_dir = Path(args.out_dir)
    _setup_style(args.dpi)

    paths = [Path(f) for f in args.files] if args.files else []
    if not paths and getattr(args, "dir", None):
        paths = sorted(Path(args.dir).glob("resource_*.csv"))
    if not paths:
        print("ERROR: provide resource CSV files or --dir", file=sys.stderr)
        sys.exit(1)

    for p in paths:
        if not p.exists():
            print(f"WARNING: {p} not found — skipping", file=sys.stderr)
            continue
        rows = _load_resource_csv(p)
        print(f"Generating resource plot for {p.name} → {out_dir}/")
        # suffix output name with the CSV stem to avoid collisions across sessions
        sub = out_dir / p.stem
        plot_resource(rows, sub)


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

    # Optional: V2G vs no-V2G comparison
    if getattr(args, "no_v2g_summary", None):
        p = Path(args.no_v2g_summary)
        if not p.exists():
            print(f"WARNING: --no-v2g-summary {p} not found — skipping comparison", file=sys.stderr)
        else:
            nov2g = _load_multiev_summary(p)
            print(f"Generating V2G comparison plot → {out_dir}/")
            plot_multiev_comparison(summaries, nov2g, out_dir)


def cmd_voltage_stab(args) -> None:
    out_dir = Path(args.out_dir)
    _setup_style(args.dpi)

    paths = [Path(f) for f in args.files] if getattr(args, "files", None) else []
    if not paths and getattr(args, "dir", None):
        paths = sorted(Path(args.dir).glob("voltage_stab_*.csv"))
    if not paths:
        print("ERROR: provide voltage_stab_*.csv file(s) or --dir", file=sys.stderr)
        sys.exit(1)

    for p in paths:
        if not p.exists():
            print(f"WARNING: {p} not found — skipping", file=sys.stderr)
            continue
        rows = _load_voltage_stab_csv(p)
        if not rows:
            print(f"WARNING: {p.name} has no data rows — skipping", file=sys.stderr)
            continue
        print(f"Generating voltage-stab plot for {p.name} ({len(rows)} samples) → {out_dir}/")
        sse  = sum(r["error_pu"] ** 2 for r in rows)
        rmse = (sse / len(rows)) ** 0.5
        print(f"  RMSE={rmse*1000:.3f} mpu  samples={len(rows)}")
        label = p.stem.replace("voltage_stab_", "")
        plot_voltage_stab(rows, out_dir, label=label)


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

    labels, summaries, raw_data = _drop_empty_sessions(labels, summaries, raw_data)
    if summaries:
        print(f"  Generating reliability plots → {out_dir}/")
        plot_reliability(labels, summaries, raw_data, out_dir)

    # ── Latency (combined) ───────────────────────────────────────────────────
    lat_iec_csvs  = sorted(log_dir.glob("iec104_*.csv"))
    lat_ctrl_csvs = sorted(log_dir.glob("control_latency_*.csv"))
    lat_iso_csvs  = sorted(p for p in log_dir.glob("iso15118_*.csv") if "_bytes_" not in p.name)

    if lat_iec_csvs or lat_ctrl_csvs or lat_iso_csvs:
        lat_iec_transmit, lat_pandapower = [], []
        if lat_iec_csvs:
            d = _load_reliability_raw(lat_iec_csvs[-1])
            lat_iec_transmit, lat_pandapower = d["transmit_ms"], d["pandapower_ms"]
        lat_control = _load_control_latency_csv(lat_ctrl_csvs[-1]) if lat_ctrl_csvs else []
        lat_iso_loop = _load_iso15118_loop_csv(lat_iso_csvs[-1]) if lat_iso_csvs else []
        if any([lat_iec_transmit, lat_pandapower, lat_control, lat_iso_loop]):
            print(f"\n[Latency] Generating combined latency plot → {out_dir}/")
            plot_latency(lat_iec_transmit, lat_pandapower, lat_control, lat_iso_loop, out_dir)
    else:
        print("[Latency] No latency-related CSVs found — skipping")

    # ── Multi-EV ─────────────────────────────────────────────────────────────
    # Only V2G-enabled summaries — exclude _nov2g variants
    multi_summaries = sorted(
        p for p in log_dir.glob("multi_ev_summary_*.csv") if "_nov2g_" not in p.name
    )
    # Only V2G-enabled per-tick CSVs
    multi_csvs = sorted(
        p for p in log_dir.glob("multi_ev_*ev_*.csv") if "_nov2g_" not in p.name
    )

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

    # ── V2G vs no-V2G comparison ─────────────────────────────────────────────
    nov2g_summaries_paths = sorted(log_dir.glob("multi_ev_summary_nov2g_*.csv"))
    if ev_summaries and nov2g_summaries_paths:
        print(f"\n[V2G Comparison] Loading no-V2G summary: {nov2g_summaries_paths[-1].name}")
        nov2g_ev = _load_multiev_summary(nov2g_summaries_paths[-1])
        print(f"  Generating comparison plot → {out_dir}/")
        plot_multiev_comparison(ev_summaries, nov2g_ev, out_dir)

    # ── Battery degradation ───────────────────────────────────────────────────
    deg_csvs = sorted(log_dir.glob("degradation_*.csv"))
    if deg_csvs:
        print(f"\n[Degradation] Found {len(deg_csvs)} scenario CSV(s)")
        datasets: list[tuple[str, list[dict]]] = []
        for p in deg_csvs:
            label = p.stem.replace("degradation_", "").replace("_", " ").title()
            # strip trailing session timestamp from label
            parts = label.rsplit(" ", 2)
            if len(parts) == 3 and parts[-1].isdigit() and parts[-2].isdigit():
                label = parts[0]
            recs = _load_degradation_csv(p)
            if recs:
                datasets.append((label, recs))
        if datasets:
            print(f"  Generating degradation plots → {out_dir}/")
            plot_degradation(datasets, out_dir)
    else:
        print("[Degradation] No degradation_*.csv found — skipping")

    # ── Resource monitor ─────────────────────────────────────────────────────
    resource_csvs = sorted(log_dir.glob("resource_*.csv"))
    if resource_csvs:
        print(f"\n[Resource] Found {len(resource_csvs)} resource CSV(s)")
        for p in resource_csvs:
            rows = _load_resource_csv(p)
            sub = out_dir / p.stem
            print(f"  Generating resource plot for {p.name} → {sub}/")
            plot_resource(rows, sub)
    else:
        print("[Resource] No resource_*.csv found — skipping")

    # ── Voltage stabilisation ─────────────────────────────────────────────────
    vstab_csvs = sorted(log_dir.glob("voltage_stab_*.csv"))
    if vstab_csvs:
        print(f"\n[VoltageStab] Found {len(vstab_csvs)} voltage_stab CSV(s)")
        for p in vstab_csvs:
            rows = _load_voltage_stab_csv(p)
            if not rows:
                print(f"  {p.name}: no data rows — skipping")
                continue
            sse  = sum(r["error_pu"] ** 2 for r in rows)
            rmse = (sse / len(rows)) ** 0.5
            print(f"  {p.name}: {len(rows)} samples, RMSE={rmse*1000:.3f} mpu")
            label = p.stem.replace("voltage_stab_", "")
            plot_voltage_stab(rows, out_dir, label=label)
    else:
        print("[VoltageStab] No voltage_stab_*.csv found — skipping")


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
    p_rel.add_argument(
        "--scenario-label", dest="scenario_label", default="Session", metavar="LABEL",
        help="What each x-axis category represents (default: 'Session'). "
             "Pass 'Packet Loss Scenario' only when the data is genuine tc-netem loss-injection output.",
    )

    # ── latency ──────────────────────────────────────────────────────────────
    p_lat = sub.add_parser(
        "latency",
        parents=[_shared],
        help="Plot combined latency validation (IEC 104, pandapower, control, ISO 15118).",
    )
    p_lat.add_argument("--iec104", metavar="PATH",
                        help="iec104_*.csv (transmit + pandapower latency).")
    p_lat.add_argument("--control-latency", metavar="PATH", dest="control_latency",
                        help="control_latency_*.csv (setpoint -> EVSE limit handoff).")
    p_lat.add_argument("--iso15118", metavar="PATH",
                        help="iso15118_*.csv (charge-loop iteration time).")
    p_lat.add_argument("--dir", metavar="DIR",
                        help="Auto-discover the latest matching CSV of each kind in this directory.")
    p_lat.add_argument("--label", metavar="LABEL", default="",
                        help="Optional session label appended to the title/filename.")

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
    p_multi.add_argument(
        "--no-v2g-summary", metavar="PATH", dest="no_v2g_summary",
        help="Load charge-only (no-V2G) summary to generate a comparison figure.",
    )

    # ── degradation ───────────────────────────────────────────────────────────
    p_deg = sub.add_parser(
        "degradation",
        parents=[_shared],
        help="Plot battery SOH degradation from battery_degradation.py output CSVs.",
    )
    p_deg.add_argument(
        "files", nargs="+", metavar="CSV",
        help="Per-scenario degradation_*.csv files.",
    )

    # ── resource ──────────────────────────────────────────────────────────────
    p_res = sub.add_parser(
        "resource",
        parents=[_shared],
        help="Plot CPU/memory usage from resource_monitor.py output CSVs.",
    )
    p_res.add_argument(
        "files", nargs="*", metavar="CSV",
        help="resource_*.csv files.",
    )
    p_res.add_argument(
        "--dir", metavar="DIR",
        help="Auto-discover resource_*.csv in this directory.",
    )

    # ── voltage-stab ──────────────────────────────────────────────────────────
    p_vstab = sub.add_parser(
        "voltage-stab",
        parents=[_shared],
        help="Plot voltage-stabilisation accuracy from voltage_stab_*.csv.",
    )
    p_vstab.add_argument(
        "files", nargs="*", metavar="CSV",
        help="voltage_stab_*.csv files.",
    )
    p_vstab.add_argument(
        "--dir", metavar="DIR",
        help="Auto-discover voltage_stab_*.csv in this directory.",
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
    elif args.command == "latency":
        cmd_latency(args)
    elif args.command == "multi-ev":
        cmd_multiev(args)
    elif args.command == "degradation":
        cmd_degradation(args)
    elif args.command == "resource":
        cmd_resource(args)
    elif args.command == "voltage-stab":
        cmd_voltage_stab(args)
    elif args.command == "all":
        cmd_all(args)


if __name__ == "__main__":
    main()
