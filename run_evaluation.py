#!/usr/bin/env python3
"""
run_evaluation.py
=================
Runs all offline evaluation tools in sequence and generates plots.

Steps executed:
  1. Multi-EV scalability simulation (V2G enabled)
  2. Multi-EV scalability simulation (charge-only baseline)
  3. Battery SOH degradation analysis
  4. Reliability analysis — only if iec104_*.csv logs exist in Logs/
  5. Plot all results

Skipped (require live hardware or sudo):
  tools/reliability_test.sh  — needs root + tc netem on the grid Pi NIC
  tools/resource_monitor.py  — monitors live processes; not standalone

Usage
-----
    python run_evaluation.py                        # full run
    python run_evaluation.py --quick                # reduced ticks/cycles (fast smoke-test)
    python run_evaluation.py --fleet 1 5            # override fleet sizes
    python run_evaluation.py --skip multi-ev plot   # skip specific steps
    python run_evaluation.py --no-plot              # skip final plotting step
    python run_evaluation.py --dpi 300              # print-quality plots
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

_ROOT  = Path(__file__).resolve().parent
_TOOLS = _ROOT / "tools"
_LOGS  = _ROOT / "Logs"


# ── Runner helpers ─────────────────────────────────────────────────────────────

def _banner(text: str) -> None:
    print(f"\n{'=' * 62}")
    print(f"  {text}")
    print(f"{'=' * 62}")


def run_step(label: str, cmd: list) -> bool:
    _banner(label)
    print(f"  $ {' '.join(str(c) for c in cmd)}\n")
    t0 = time.perf_counter()
    result = subprocess.run(cmd)
    elapsed = time.perf_counter() - t0
    ok = result.returncode == 0
    status = "OK" if ok else f"FAILED (exit {result.returncode})"
    print(f"\n  [{status}]  {elapsed:.1f} s")
    return ok


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run all V2G offline evaluation tools in sequence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Reduced ticks/cycles for a fast smoke-test "
             "(ticks=200, cycles=100, fleet=1 5).",
    )
    parser.add_argument(
        "--fleet", nargs="+", type=int, default=None, metavar="N",
        help="Fleet sizes for multi-EV simulation (default: 1 5 10 20, "
             "or 1 5 with --quick).",
    )
    parser.add_argument(
        "--ticks", type=int, default=None, metavar="N",
        help="Simulation ticks per fleet size (default: 1800, or 200 with --quick).",
    )
    parser.add_argument(
        "--n-cycles", type=int, default=None, dest="n_cycles", metavar="N",
        help="Max degradation cycles (default: 800, or 100 with --quick).",
    )
    parser.add_argument(
        "--skip", nargs="*", default=[],
        choices=["multi-ev", "degradation", "reliability", "plot"],
        metavar="STEP",
        help="Steps to skip: multi-ev, degradation, reliability, plot.",
    )
    parser.add_argument(
        "--no-plot", action="store_true",
        help="Shorthand for --skip plot.",
    )
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="Plot resolution in DPI (default: 150; use 300 for print).",
    )
    args = parser.parse_args()

    skip = set(args.skip or [])
    if args.no_plot:
        skip.add("plot")

    fleet    = args.fleet    or ([1, 5]          if args.quick else [1, 5, 10, 20])
    ticks    = args.ticks    or (200              if args.quick else 1800)
    n_cycles = args.n_cycles or (100              if args.quick else 800)

    py = sys.executable
    results: dict[str, bool] = {}

    print("\nV2G Evaluation Suite")
    print(f"  fleet={fleet}  ticks={ticks}  n_cycles={n_cycles}  dpi={args.dpi}")
    print(f"  skipping: {sorted(skip) or 'nothing'}")

    # ── Step 1: Multi-EV (V2G enabled) ────────────────────────────────────────
    if "multi-ev" not in skip:
        results["multi_ev_v2g"] = run_step(
            "Step 1/2 — Multi-EV simulation (V2G enabled)",
            [py, str(_TOOLS / "multi_ev_sim.py"),
             "--fleet", *[str(n) for n in fleet],
             "--ticks", str(ticks)],
        )

        # ── Step 2: Multi-EV (charge-only baseline) ───────────────────────────
        results["multi_ev_nov2g"] = run_step(
            "Step 2/2 — Multi-EV simulation (charge-only baseline)",
            [py, str(_TOOLS / "multi_ev_sim.py"),
             "--fleet", *[str(n) for n in fleet],
             "--ticks", str(ticks),
             "--no-v2g"],
        )
    else:
        print("\n[multi-ev] Skipped.")

    # ── Step 3: Battery degradation ───────────────────────────────────────────
    if "degradation" not in skip:
        results["degradation"] = run_step(
            "Step 3 — Battery SOH degradation analysis",
            [py, str(_TOOLS / "battery_degradation.py"),
             "--n-cycles", str(n_cycles),
             "--no-plot"],   # plot_results.py handles all figures
        )
    else:
        print("\n[degradation] Skipped.")

    # ── Step 4: Reliability analysis (only if live-session CSVs exist) ────────
    if "reliability" not in skip:
        iec104_csvs = sorted(_LOGS.glob("iec104_*.csv"))
        if iec104_csvs:
            results["reliability"] = run_step(
                f"Step 4 — Reliability analysis ({len(iec104_csvs)} IEC 104 CSV(s) found)",
                [py, str(_TOOLS / "analyse_reliability.py"),
                 "--dir", str(_LOGS)],
            )
        else:
            print(
                "\n[reliability] No iec104_*.csv in Logs/ — skipping.\n"
                "  Run tools/reliability_test.sh on the grid Pi first, then re-run."
            )
    else:
        print("\n[reliability] Skipped.")

    # ── Step 5: Plot all results ───────────────────────────────────────────────
    if "plot" not in skip:
        results["plot"] = run_step(
            "Step 5 — Generate all plots",
            [py, str(_TOOLS / "plot_results.py"),
             "all", "--dir", str(_LOGS), "--dpi", str(args.dpi)],
        )
    else:
        print("\n[plot] Skipped.")

    # ── Summary ───────────────────────────────────────────────────────────────
    _banner("Summary")
    all_ok = True
    for step, ok in results.items():
        icon = "✓" if ok else "✗"
        print(f"  {icon}  {step}")
        if not ok:
            all_ok = False

    if not results:
        print("  (all steps skipped)")

    print()
    if all_ok:
        print(f"  All steps passed.  Plots → {_LOGS / 'plots'}/")
    else:
        failed = [s for s, ok in results.items() if not ok]
        print(f"  {len(failed)} step(s) failed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
