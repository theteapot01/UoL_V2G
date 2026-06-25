#!/usr/bin/env python3
"""
battery_degradation.py
======================
Demonstrate SOH (State of Health) degradation under three V2G usage profiles,
directly validating the throughput-based cycle-aging model in SimulatedBattery.

Scenarios
---------
  charge_only   — charge 20 % → 80 %, reset, repeat (no V2G discharge)
  moderate_v2g  — charge to 80 %, discharge to 30 %, repeat
  heavy_v2g     — charge to 80 %, discharge to 20 % (floor), repeat

The difference in cycles-to-EOL between scenarios is the "cost of providing
grid services" described in the SimulatedBattery docstring.

Output
------
  Logs/degradation_<scenario>_<session>.csv  — per-cycle data
  Logs/degradation_soh_<session>.png         — SOH vs cycles and vs EFC
  Logs/degradation_temp_<session>.png        — peak temperature vs cycles

Usage
-----
    python tools/battery_degradation.py
    python tools/battery_degradation.py --n-cycles 500 --cycle-life 200
    python tools/battery_degradation.py --no-plot   # CSV only
"""

import argparse
import csv
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "code_iso15118_custom"))

try:
    from simulated_battery import SimulatedBattery
except ImportError as e:
    print(f"ERROR: could not import SimulatedBattery: {e}", file=sys.stderr)
    sys.exit(1)

_LOG_DIR = _ROOT / "Logs"
_SESSION = time.strftime("%Y%m%d_%H%M%S")

_SCENARIOS = [
    {
        "name":           "charge_only",
        "label":          "Charge only (no V2G)",
        "discharge_kw":   0.0,
        "discharge_to":   None,   # no discharge phase
    },
    {
        "name":           "moderate_v2g",
        "label":          "Moderate V2G (discharge to 30 %)",
        "discharge_kw":   20.0,
        "discharge_to":   30.0,
    },
    {
        "name":           "heavy_v2g",
        "label":          "Heavy V2G (discharge to 20 %)",
        "discharge_kw":   20.0,
        "discharge_to":   20.0,   # floor — battery protection kicks in
    },
]

_CSV_HEADER = [
    "cycle", "soh_pct", "throughput_kwh", "efc",
    "energy_in_kwh", "energy_out_kwh", "peak_temp_c", "end_temp_c",
]


# ── Simulation ────────────────────────────────────────────────────────────────

def run_scenario(
    *,
    discharge_kw: float,
    discharge_to: float | None,
    n_cycles: int,
    dt_s: float,
    capacity_kwh: float,
    cycle_life: int,
    charge_kw: float,
    charge_floor: float = 20.0,
    charge_ceiling: float = 80.0,
) -> list[dict]:
    """
    Run one usage-profile scenario and return a list of per-cycle dicts.
    SOH/throughput carry over across cycles; SoC resets to charge_floor.
    """
    bat = SimulatedBattery(
        capacity_kwh=capacity_kwh,
        cycle_life=cycle_life,
        soc_init=charge_floor,
        soc_floor=charge_floor,
        soc_ceiling=charge_ceiling,
        max_charge_kw=charge_kw,
        max_discharge_kw=max(discharge_kw, 0.0),
        target_soc=None,
    )

    records: list[dict] = []
    _MAX_TICKS = int(n_cycles * (charge_ceiling - charge_floor + (
        (charge_ceiling - (discharge_to or charge_floor))
    )) / 0.05 + 1000)   # generous safety limit

    for cycle in range(1, n_cycles + 1):
        energy_in  = 0.0
        energy_out = 0.0
        peak_temp  = bat.temperature_c
        ticks      = 0

        # Phase 1: charge to ceiling
        bat.set_power_setpoint(charge_kw)
        while bat.soc_percent < charge_ceiling - 0.02 and ticks < _MAX_TICKS:
            bat.tick(dt_s=dt_s)
            ticks += 1
            p = bat.power_kw
            if p > 0:
                energy_in += p * dt_s / 3600.0
            peak_temp = max(peak_temp, bat.temperature_c)

        # Phase 2: discharge (if this scenario has one)
        if discharge_kw > 0.0 and discharge_to is not None:
            bat.set_power_setpoint(-discharge_kw)
            while bat.soc_percent > discharge_to + 0.02 and ticks < _MAX_TICKS:
                bat.tick(dt_s=dt_s)
                ticks += 1
                p = bat.power_kw
                if p < 0:
                    energy_out += abs(p) * dt_s / 3600.0
                peak_temp = max(peak_temp, bat.temperature_c)

        records.append({
            "cycle":          cycle,
            "soh_pct":        round(bat.soh_percent, 4),
            "throughput_kwh": round(bat.throughput_kwh, 3),
            "efc":            round(bat.equivalent_full_cycles, 4),
            "energy_in_kwh":  round(energy_in, 3),
            "energy_out_kwh": round(energy_out, 3),
            "peak_temp_c":    round(peak_temp, 2),
            "end_temp_c":     round(bat.temperature_c, 2),
        })

        if bat.soh_percent <= 80.0:
            break

        # Reset SoC for next cycle; SOH and throughput persist
        bat.reset()

    return records


# ── Output ────────────────────────────────────────────────────────────────────

def save_csv(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_HEADER)
        w.writeheader()
        w.writerows(records)
    print(f"  CSV: {path}")


def print_summary(scenario_results: list[tuple[dict, list[dict]]]) -> None:
    print()
    print("Battery Degradation Summary")
    print(f"  Pack: 82.5 kWh LFP  |  EOL at SOH = 80%")
    print("-" * 72)
    fmt = "{:<30} {:>8} {:>10} {:>10} {:>10}"
    print(fmt.format("Scenario", "Cycles", "Final SOH%", "EFC", "Throughput"))
    print("-" * 72)
    for sc, recs in scenario_results:
        last = recs[-1]
        print(fmt.format(
            sc["label"][:30],
            last["cycle"],
            f"{last['soh_pct']:.2f}",
            f"{last['efc']:.1f}",
            f"{last['throughput_kwh']:.0f} kWh",
        ))
    print("-" * 72)
    print()


# ── Plotting ──────────────────────────────────────────────────────────────────

def _plot(scenario_results: list[tuple[dict, list[dict]]], out_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed — skipping plots (pip install matplotlib)")
        return

    plt.rcParams.update({
        "figure.dpi": 150, "savefig.dpi": 150,
        "font.size": 10, "axes.grid": True, "grid.alpha": 0.3,
        "figure.autolayout": True,
    })
    colours = ["#1f77b4", "#ff7f0e", "#d62728"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # Figure 1: SOH vs cycle | SOH vs EFC
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    for (sc, recs), colour in zip(scenario_results, colours):
        cycles = [r["cycle"]  for r in recs]
        efcs   = [r["efc"]    for r in recs]
        sohs   = [r["soh_pct"] for r in recs]
        ax1.plot(cycles, sohs, label=sc["label"], color=colour, linewidth=1.5)
        ax2.plot(efcs,   sohs, label=sc["label"], color=colour, linewidth=1.5)

    for ax in (ax1, ax2):
        ax.axhline(80.0, color="red", linewidth=0.9, linestyle="--", label="EOL (80% SOH)")
        ax.set_ylabel("State of Health (%)")
        ax.set_ylim(76, 101)
        ax.legend(fontsize=8)

    ax1.set_xlabel("Charge/Discharge Cycle")
    ax1.set_title("SOH Degradation vs Cycle Count")
    ax2.set_xlabel("Equivalent Full Cycles (EFC)")
    ax2.set_title("SOH Degradation vs EFC")

    path1 = out_dir / f"degradation_soh_{_SESSION}.png"
    fig.savefig(path1, bbox_inches="tight")
    plt.close(fig)
    print(f"  Plot: {path1}")

    # Figure 2: Peak temperature vs cycle
    fig, ax = plt.subplots(figsize=(8, 4))
    for (sc, recs), colour in zip(scenario_results, colours):
        ax.plot(
            [r["cycle"] for r in recs],
            [r["peak_temp_c"] for r in recs],
            label=sc["label"], color=colour, linewidth=1.2,
        )
    ax.set_xlabel("Charge/Discharge Cycle")
    ax.set_ylabel("Peak Pack Temperature (°C)")
    ax.set_title("Peak Temperature per Cycle")
    ax.legend(fontsize=8)
    path2 = out_dir / f"degradation_temp_{_SESSION}.png"
    fig.savefig(path2, bbox_inches="tight")
    plt.close(fig)
    print(f"  Plot: {path2}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate V2G battery SOH degradation across usage profiles.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--n-cycles",    type=int,   default=800,  help="Max cycles per scenario.")
    parser.add_argument("--cycle-life",  type=int,   default=200,
                        help="EFC to 80%% SOH (200 = accelerated demo; realistic LFP = 5000).")
    parser.add_argument("--capacity",    type=float, default=82.5, metavar="KWH",
                        help="Pack usable capacity [kWh].")
    parser.add_argument("--charge-kw",  type=float, default=50.0,
                        help="Charge power [kW].")
    parser.add_argument("--discharge-kw", type=float, default=20.0,
                        help="V2G discharge power [kW].")
    parser.add_argument("--dt",          type=float, default=30.0, metavar="S",
                        help="Integration timestep [s].")
    parser.add_argument("--out-dir",     default=str(_LOG_DIR), metavar="DIR",
                        help="Output directory for CSV files.")
    parser.add_argument("--no-plot",     action="store_true",
                        help="Skip matplotlib plots (CSV output only).")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    print(
        f"\nBattery degradation simulation\n"
        f"  Capacity: {args.capacity} kWh  |  Cycle life: {args.cycle_life} EFC  "
        f"|  Charge: {args.charge_kw} kW  |  V2G discharge: {args.discharge_kw} kW\n"
        f"  dt = {args.dt}s  |  Max cycles/scenario = {args.n_cycles}\n"
    )

    scenario_results: list[tuple[dict, list[dict]]] = []
    for sc in _SCENARIOS:
        print(f"  Running: {sc['label']} ...", end="", flush=True)
        t0 = time.time()
        recs = run_scenario(
            discharge_kw=sc["discharge_kw"] if sc["discharge_kw"] <= args.discharge_kw else args.discharge_kw,
            discharge_to=sc["discharge_to"],
            n_cycles=args.n_cycles,
            dt_s=args.dt,
            capacity_kwh=args.capacity,
            cycle_life=args.cycle_life,
            charge_kw=args.charge_kw,
        )
        elapsed = time.time() - t0
        print(f" {recs[-1]['cycle']} cycles, final SOH {recs[-1]['soh_pct']:.2f}% ({elapsed:.1f}s)")
        csv_path = out_dir / f"degradation_{sc['name']}_{_SESSION}.csv"
        save_csv(recs, csv_path)
        scenario_results.append((sc, recs))

    print_summary(scenario_results)

    if not args.no_plot:
        print("Generating plots ...")
        _plot(scenario_results, out_dir / "plots")


if __name__ == "__main__":
    main()
