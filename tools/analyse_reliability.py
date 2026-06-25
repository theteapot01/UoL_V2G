#!/usr/bin/env python3
"""
analyse_reliability.py
======================
Parse IEC 104 CSV logs from reliability stress tests and produce a
delivery-rate comparison table across packet-loss scenarios.

The IEC 104 CSV written by perf_logger.py has columns:
    timestamp_unix, timestamp_iso, cmd, bursts, success,
    transmit_ms, read_ms, pandapower_ms, cycle_ms

A "transmit cycle" is any row where cmd != "HOLD".
Delivery rate = (success == 1 rows) / (transmit cycle rows) × 100.

Usage:
    python tools/analyse_reliability.py \\
        Logs/iec104_loss0.csv Logs/iec104_loss5.csv Logs/iec104_loss20.csv \\
        --labels "0% loss" "5% loss" "20% loss"

    # Auto-discover all iec104 CSVs in Logs/:
    python tools/analyse_reliability.py --dir Logs/

Output:
    - Summary table printed to stdout
    - Logs/reliability_summary.csv  (machine-readable results)
"""

import argparse
import csv
import math
import sys
from pathlib import Path
from typing import NamedTuple, Optional

_ROOT = Path(__file__).resolve().parent.parent


class ScenarioResult(NamedTuple):
    label: str
    total_cycles: int
    transmit_cycles: int
    success_cycles: int
    delivery_rate_pct: float
    mean_bursts: float
    mean_transmit_ms: float
    p95_transmit_ms: float
    mean_pandapower_ms: float
    higher_count: int
    lower_count: int


def _p95(values: list) -> float:
    if not values:
        return 0.0
    sv = sorted(values)
    idx = int(len(sv) * 0.95)
    return sv[min(idx, len(sv) - 1)]


def analyse_csv(path: Path, label: str) -> ScenarioResult:
    total = 0
    transmit = 0
    success = 0
    bursts_list: list[float] = []
    transmit_ms_list: list[float] = []
    pandapower_ms_list: list[float] = []
    higher = 0
    lower = 0

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            cmd = row["cmd"].strip()
            if cmd == "HOLD":
                continue
            transmit += 1
            bursts_list.append(float(row["bursts"]))
            pandapower_ms_list.append(float(row["pandapower_ms"]))
            tx_ms = float(row["transmit_ms"])
            transmit_ms_list.append(tx_ms)
            if int(row["success"]) == 1:
                success += 1
            if cmd == "HIGHER":
                higher += 1
            elif cmd == "LOWER":
                lower += 1

    delivery_rate = (success / transmit * 100.0) if transmit > 0 else 0.0
    mean_bursts = sum(bursts_list) / len(bursts_list) if bursts_list else 0.0
    mean_tx = sum(transmit_ms_list) / len(transmit_ms_list) if transmit_ms_list else 0.0
    p95_tx = _p95(transmit_ms_list)
    mean_pp = sum(pandapower_ms_list) / len(pandapower_ms_list) if pandapower_ms_list else 0.0

    return ScenarioResult(
        label=label,
        total_cycles=total,
        transmit_cycles=transmit,
        success_cycles=success,
        delivery_rate_pct=delivery_rate,
        mean_bursts=mean_bursts,
        mean_transmit_ms=mean_tx,
        p95_transmit_ms=p95_tx,
        mean_pandapower_ms=mean_pp,
        higher_count=higher,
        lower_count=lower,
    )


def print_table(results: list[ScenarioResult]) -> None:
    col_w = 14

    def _h(s: str) -> str:
        return s.center(col_w)

    def _v(v, fmt=".1f") -> str:
        return format(v, fmt).center(col_w)

    headers = [
        "Scenario", "Transmit", "Success", "Delivery%",
        "Bursts(mean)", "Tx ms(mean)", "Tx ms(p95)", "PP ms(mean)",
    ]
    header_line = "".join(h.center(col_w) for h in headers)
    sep = "-" * (col_w * len(headers))

    print()
    print("IEC 104 Command Delivery Reliability — Stress Test Results")
    print(sep)
    print(header_line)
    print(sep)
    for r in results:
        row = "".join([
            r.label[:col_w-1].center(col_w),
            str(r.transmit_cycles).center(col_w),
            str(r.success_cycles).center(col_w),
            f"{r.delivery_rate_pct:.1f}%".center(col_w),
            f"{r.mean_bursts:.2f}".center(col_w),
            f"{r.mean_transmit_ms:.1f}".center(col_w),
            f"{r.p95_transmit_ms:.1f}".center(col_w),
            f"{r.mean_pandapower_ms:.1f}".center(col_w),
        ])
        print(row)
    print(sep)
    print()

    # Delta analysis
    if len(results) > 1:
        baseline = results[0]
        print("Degradation relative to baseline (first scenario):")
        for r in results[1:]:
            dr = baseline.delivery_rate_pct - r.delivery_rate_pct
            dt = r.mean_transmit_ms - baseline.mean_transmit_ms
            print(
                f"  {r.label}: "
                f"delivery rate −{dr:.1f} pp, "
                f"mean latency +{dt:.1f} ms"
            )
        print()


def save_csv(results: list[ScenarioResult], out_path: Path) -> None:
    fields = ScenarioResult._fields
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for r in results:
            writer.writerow([
                r.label, r.total_cycles, r.transmit_cycles, r.success_cycles,
                f"{r.delivery_rate_pct:.2f}", f"{r.mean_bursts:.3f}",
                f"{r.mean_transmit_ms:.3f}", f"{r.p95_transmit_ms:.3f}",
                f"{r.mean_pandapower_ms:.3f}", r.higher_count, r.lower_count,
            ])
    print(f"Results saved to: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyse IEC 104 reliability CSV logs across packet-loss scenarios."
    )
    parser.add_argument(
        "files", nargs="*", metavar="CSV",
        help="IEC 104 CSV files, one per scenario (ordered baseline → worst).",
    )
    parser.add_argument(
        "--labels", nargs="*", metavar="LABEL",
        help="Human-readable label per file (e.g. '0%% loss' '5%% loss'). "
             "Defaults to the file stem.",
    )
    parser.add_argument(
        "--dir", metavar="DIR", default=None,
        help="Auto-discover all iec104_*.csv files in this directory.",
    )
    parser.add_argument(
        "--out", metavar="PATH",
        default=str(_ROOT / "Logs" / "reliability_summary.csv"),
        help="Path for the output CSV summary (default: Logs/reliability_summary.csv).",
    )
    args = parser.parse_args()

    # Resolve file list
    paths: list[Path] = []
    if args.dir:
        dir_path = Path(args.dir)
        paths = sorted(dir_path.glob("iec104_*.csv"))
        if not paths:
            print(f"No iec104_*.csv files found in {dir_path}", file=sys.stderr)
            sys.exit(1)
    elif args.files:
        paths = [Path(f) for f in args.files]
    else:
        parser.print_help()
        sys.exit(1)

    for p in paths:
        if not p.exists():
            print(f"File not found: {p}", file=sys.stderr)
            sys.exit(1)

    # Labels
    if args.labels:
        if len(args.labels) != len(paths):
            print(
                f"--labels count ({len(args.labels)}) must match file count ({len(paths)})",
                file=sys.stderr,
            )
            sys.exit(1)
        labels = args.labels
    else:
        labels = [p.stem for p in paths]

    results = [analyse_csv(p, label) for p, label in zip(paths, labels)]

    print_table(results)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_csv(results, out)


if __name__ == "__main__":
    main()
