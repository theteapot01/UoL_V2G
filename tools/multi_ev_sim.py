#!/usr/bin/env python3
"""
multi_ev_sim.py
===============
Multi-EV fleet scalability simulation for the V2G prototype.

Simulates N EVs simultaneously using the same pandapower CIGRE network and
auto-control thresholds as the live prototype (iec104_panda.py).  Each EV is
a SimulatedBattery; the SECC acts as aggregator and presents the combined load
to the IEC 104 / grid control layer.  Step commands are distributed
proportionally across the fleet (total setpoint change = STEP_KW = 5 kW
regardless of fleet size) to model the SECC-as-aggregator architecture.

Architecture under test
-----------------------
  Grid Pi (IEC 104 client)
    └─ pandapower load-flow on aggregate EV power
    └─ auto-control → HIGHER / LOWER command
  Charger Pi (SECC as aggregator)
    └─ distributes step command across N EVs proportionally
    └─ each EV: SimulatedBattery (coulomb-counting SoC + throughput SOH)
  IOA 11 telemetry: aggregate power (kW)
  IOA 13 telemetry: mean SoC (%)

Initial conditions
------------------
  - Each EV starts at a different SoC, spread from 20% to 60%
  - All EVs start with an initial charge setpoint of 17 kW (mirrors
    EVCC_INIT_SETPOINT_KW default in run_evcc.py)
  - Target SoC: 80% for all EVs (EVCC_TARGET_SOC default)

Simulation time step
--------------------
  dt_s = 4.0 s — one IEC 104 transmit cycle.  The battery integrates 4
  seconds of actual power per tick.  One tick corresponds to one command
  dispatch decision.  n_ticks=1800 → 7200 s = 2 hours simulated time.

Usage:
    python tools/multi_ev_sim.py                         # default fleet sizes
    python tools/multi_ev_sim.py --fleet 1 5 10 20 50   # custom fleet sizes
    python tools/multi_ev_sim.py --ticks 1800 --dt 4    # simulation parameters

Output:
    Logs/multi_ev_{N}ev_{SESSION}.csv  — per-tick data for each fleet size
    Summary table printed to stdout on completion
"""

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import List, Optional

# ── path setup ────────────────────────────────────────────────────────────────
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "code_iso15118_custom"))

try:
    import pandapower as pp
except ImportError:
    print("ERROR: pandapower not installed. Run: pip install pandapower", file=sys.stderr)
    sys.exit(1)

from simulated_battery import SimulatedBattery

# ── Grid / network constants (mirrored from config.py and iec104_panda.py) ────

V_PRIMARY         = 10.0
V_SECONDARY       = 0.4
TRAFO_TYPE        = "0.4 MVA 10/0.4 kV"
LINE_TYPE         = "NA2XS2Y 1x185 RM/25 6/10 kV"
LINE_LENGTH_KM    = 0.5
BASE_LOAD_MW      = 0.1    # static background load at bus 3
BASE_LOAD_MVAR    = 0.05

TRAFO_STRESS_PCT      = 80.0
LINE_STRESS_PCT       = 90.0
VOLTAGE_MIN_PU        = 0.95
TRAFO_TARGET_PCT      = 70.0
TRAFO_HYSTERESIS_PCT  =  3.0   # dead zone: ±3% around 70%
LINE_TARGET_PCT       = 80.0
LINE_HYSTERESIS_PCT   =  5.0   # dead zone: ±5% around 80%
SOC_APPROACH_BAND_PCT =  5.0
STEP_KW               =  5.0   # total aggregate step per command cycle

MIN_SOC_PCT      = 20.0   # floor: refuse V2G below this
MAX_SOC_PCT      = 80.0   # ceiling: ramp down charge above this
TARGET_SOC_PCT   = 80.0   # session end condition
INIT_SETPOINT_KW = 17.0   # initial charge setpoint per EV

_LOG_DIR = _root / "Logs"
_SESSION = time.strftime("%Y%m%d_%H%M%S")


# ── Pandapower network (rebuilt here to avoid importing iec104_panda.py
#    which depends on c104 — an embedded-only library) ────────────────────────

def _build_network():
    """Return (net, b1, b2, b3, load_idx) for a fresh CIGRE 3-bus network."""
    net = pp.create_empty_network()
    b1 = pp.create_bus(net, vn_kv=V_PRIMARY,   name="Bus 1")
    b2 = pp.create_bus(net, vn_kv=V_SECONDARY, name="Bus 2")
    b3 = pp.create_bus(net, vn_kv=V_SECONDARY, name="Bus 3")
    pp.create_ext_grid(net, bus=b1, vm_pu=0.98, name="Grid Connection")
    load_idx = pp.create_load(
        net, bus=b3, p_mw=BASE_LOAD_MW, q_mvar=BASE_LOAD_MVAR, name="EV Fleet"
    )
    pp.create_transformer(
        net, hv_bus=b1, lv_bus=b2, std_type=TRAFO_TYPE, name="Trafo"
    )
    pp.create_line(
        net, from_bus=b2, to_bus=b3,
        length_km=LINE_LENGTH_KM, std_type=LINE_TYPE, name="line1"
    )
    return net, b1, b2, b3, load_idx


# ── Auto-control decision (mirrors iec104_panda.py auto mode) ─────────────────

def _auto_decision(
    trafo_pct: float,
    line_pct: float,
    vm_pu_b2: float,
    min_soc: float,
    max_soc: float,
    total_power_kw: float,
) -> str:
    """Return 'HIGHER', 'LOWER', or 'HOLD' based on grid state and fleet SoC.

    Uses min_soc (most depleted EV) for the charge-floor guard and max_soc
    (most charged EV) for the charge-ceiling guard.  The approach-band
    pre-emptive ramp-down from the live controller is omitted here: in the
    discrete 4-second fast-forward simulation it creates an HIGHER/LOWER
    oscillation that prevents the battery from crossing the ceiling.  The
    live system avoids this because the EVSE-limit acts as a ceiling, not a
    direct setpoint, so the actual power tapers continuously.
    """
    _trafo_high = TRAFO_TARGET_PCT + TRAFO_HYSTERESIS_PCT
    _trafo_low  = TRAFO_TARGET_PCT - TRAFO_HYSTERESIS_PCT
    _line_high  = LINE_TARGET_PCT  + LINE_HYSTERESIS_PCT
    _line_low   = LINE_TARGET_PCT  - LINE_HYSTERESIS_PCT

    # 1. Grid emergency — always trumps everything
    if trafo_pct > TRAFO_STRESS_PCT or line_pct > LINE_STRESS_PCT or vm_pu_b2 < VOLTAGE_MIN_PU:
        return "HIGHER"

    # 2. Most-charged EV at ceiling → ramp down to avoid overcharge
    if max_soc >= MAX_SOC_PCT:
        return "HIGHER" if total_power_kw > 1.0 else "HOLD"

    # 3. Most-depleted EV below floor → charge unconditionally
    if min_soc < MIN_SOC_PCT:
        return "LOWER"

    # 4. Trafo / line capacity bands
    if trafo_pct > _trafo_high or line_pct > _line_high:
        return "HIGHER"
    if trafo_pct < _trafo_low and line_pct < _line_low:
        return "LOWER"

    return "HOLD"


def _adaptive_bursts(cmd: str, trafo_pct: float, line_pct: float, mean_soc: float) -> int:
    """Number of step commands to send in one cycle (mirrors iec104_panda.py)."""
    if cmd != "HIGHER":
        return 1
    if trafo_pct > TRAFO_STRESS_PCT or line_pct > LINE_STRESS_PCT:
        return 4
    if mean_soc >= MAX_SOC_PCT:
        return 3
    approaching = (
        trafo_pct > TRAFO_TARGET_PCT + TRAFO_HYSTERESIS_PCT
        or line_pct > LINE_TARGET_PCT + LINE_HYSTERESIS_PCT
    )
    approaching_ceiling = mean_soc >= MAX_SOC_PCT - SOC_APPROACH_BAND_PCT
    if approaching or approaching_ceiling:
        return 2
    return 1


# ── Fleet factory ─────────────────────────────────────────────────────────────

def _make_fleet(n: int) -> List[SimulatedBattery]:
    """Create N batteries with initial SoC spread uniformly from 20% to 60%."""
    batteries = []
    for i in range(n):
        soc_init = 20.0 + (40.0 * i / max(n - 1, 1)) if n > 1 else 40.0
        bat = SimulatedBattery(
            soc_init=soc_init,
            target_soc=TARGET_SOC_PCT,
            max_charge_kw=300.0,
            max_discharge_kw=20.0,
            default_step_kw=5.0,
        )
        bat.set_power_setpoint(INIT_SETPOINT_KW)
        batteries.append(bat)
    return batteries


# ── Scenario runner ───────────────────────────────────────────────────────────

def run_scenario(n_evs: int, n_ticks: int, dt_s: float) -> dict:
    """
    Simulate one fleet-size scenario.

    Returns a summary dict; also writes a per-tick CSV to Logs/.
    """
    net, b1, b2, b3, load_idx = _build_network()
    fleet = _make_fleet(n_evs)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _LOG_DIR / f"multi_ev_{n_evs}ev_{_SESSION}.csv"

    ev_soc_cols = [f"ev{i}_soc_pct" for i in range(n_evs)]
    headers = [
        "tick", "sim_min",
        *ev_soc_cols,
        "mean_soc_pct", "total_power_kw",
        "bus2_voltage_pu", "trafo_loading_pct", "line_loading_pct",
        "cmd", "bursts", "cumulative_higher", "cumulative_lower",
        "grid_stress",
    ]

    with open(out_path, "w", newline="") as f:
        csv.writer(f).writerow(headers)

    # Tracking variables
    higher_count = 0
    lower_count  = 0
    stress_count = 0
    time_to_target: List[Optional[float]] = [None] * n_evs
    peak_trafo   = 0.0
    min_voltage  = 1.0

    # 2-cycle debounce (mirrors iec104_panda.py streak logic)
    _pending_cmd   = "LOWER"
    _prev_auto_cmd = "LOWER"
    _auto_streak   = 0

    # Per-EV step size: distribute total STEP_KW across fleet
    step_per_ev = STEP_KW / n_evs

    sim_min = 0.0
    final_tick = 0

    for tick in range(n_ticks):
        sim_min = tick * dt_s / 60.0
        final_tick = tick

        # Advance each battery
        for bat in fleet:
            bat.tick(dt_s=dt_s)

        # Fleet state
        soc_values   = [bat.soc_percent for bat in fleet]
        mean_soc     = sum(soc_values) / n_evs
        min_soc      = min(soc_values)
        max_soc      = max(soc_values)
        total_power  = sum(bat.power_kw for bat in fleet)

        # Track when each EV first reaches its target SoC
        for i, soc in enumerate(soc_values):
            if time_to_target[i] is None and soc >= TARGET_SOC_PCT:
                time_to_target[i] = sim_min

        # Pandapower load-flow
        net.load.at[load_idx, "p_mw"] = total_power / 1000.0
        try:
            pp.runpp(net, verbose=False, numba=False)
            vm_pu    = float(net.res_bus.at[b2, "vm_pu"])
            trafo_pc = float(net.res_trafo.at[0, "loading_percent"])
            line_pc  = float(net.res_line.at[0, "loading_percent"])
        except Exception:
            vm_pu    = 1.0
            trafo_pc = 0.0
            line_pc  = 0.0

        peak_trafo  = max(peak_trafo, trafo_pc)
        min_voltage = min(min_voltage, vm_pu)

        grid_stress = int(
            trafo_pc > TRAFO_STRESS_PCT
            or line_pc > LINE_STRESS_PCT
            or vm_pu < VOLTAGE_MIN_PU
        )
        stress_count += grid_stress

        # Auto-control decision with 2-cycle streak confirmation
        auto_cmd = _auto_decision(trafo_pc, line_pc, vm_pu, min_soc, max_soc, total_power)

        if auto_cmd == "HOLD":
            cmd          = "HOLD"
            _auto_streak = 0
            _prev_auto_cmd = "HOLD"
            bursts = 0
        else:
            if auto_cmd == _prev_auto_cmd:
                _auto_streak += 1
            else:
                _auto_streak   = 1
                _prev_auto_cmd = auto_cmd
            if _auto_streak >= 2 or auto_cmd == _pending_cmd:
                _pending_cmd = auto_cmd
            cmd    = _pending_cmd
            bursts = _adaptive_bursts(cmd, trafo_pc, line_pc, mean_soc)
            # Prevent burst overshoot across zero: when aggregate power is
            # within one step of zero, a multi-burst HIGHER would push every
            # EV into V2G discharge.  Cap to 1 burst (mirrors the guard in
            # iec104_panda.py).
            if cmd == "HIGHER" and 0.0 < total_power <= STEP_KW:
                bursts = 1

        # Distribute step to all EVs proportionally
        if cmd == "HIGHER":
            for _ in range(bursts):
                for bat in fleet:
                    bat.apply_step(higher=True, step_kw=step_per_ev)
            higher_count += bursts
        elif cmd == "LOWER":
            for bat in fleet:
                bat.apply_step(higher=False, step_kw=step_per_ev)
            lower_count += 1

        # Write row
        row = [
            tick, f"{sim_min:.2f}",
            *[f"{s:.2f}" for s in soc_values],
            f"{mean_soc:.2f}", f"{total_power:.3f}",
            f"{vm_pu:.4f}", f"{trafo_pc:.2f}", f"{line_pc:.2f}",
            cmd, bursts, higher_count, lower_count,
            grid_stress,
        ]
        with open(out_path, "a", newline="") as f:
            csv.writer(f).writerow(row)

        # Early exit if all EVs have reached their target
        if all(t is not None for t in time_to_target):
            break

    # Summary
    valid_ttt = [t for t in time_to_target if t is not None]
    final_socs = [bat.soc_percent for bat in fleet]

    return {
        "n_evs":                   n_evs,
        "sim_duration_min":        round(sim_min, 1),
        "evs_reached_target":      len(valid_ttt),
        "mean_time_to_target_min": round(sum(valid_ttt) / len(valid_ttt), 1) if valid_ttt else None,
        "min_time_to_target_min":  round(min(valid_ttt), 1) if valid_ttt else None,
        "max_time_to_target_min":  round(max(valid_ttt), 1) if valid_ttt else None,
        "mean_final_soc_pct":      round(sum(final_socs) / n_evs, 1),
        "peak_trafo_loading_pct":  round(peak_trafo, 1),
        "min_bus2_voltage_pu":     round(min_voltage, 4),
        "total_higher_cmds":       higher_count,
        "total_lower_cmds":        lower_count,
        "grid_stress_ticks":       stress_count,
        "out_csv":                 str(out_path),
    }


# ── Summary table ─────────────────────────────────────────────────────────────

def print_summary(results: list) -> None:
    cols = [
        ("Fleet", "n_evs", 6),
        ("Dur(min)", "sim_duration_min", 10),
        ("Reached", "evs_reached_target", 9),
        ("TTT mean", "mean_time_to_target_min", 10),
        ("TTT min", "min_time_to_target_min", 9),
        ("TTT max", "max_time_to_target_min", 9),
        ("SoC mean", "mean_final_soc_pct", 10),
        ("Trafo pk%", "peak_trafo_loading_pct", 11),
        ("Vbus2 min", "min_bus2_voltage_pu", 11),
        ("HIGHER", "total_higher_cmds", 8),
        ("LOWER", "total_lower_cmds", 7),
        ("Stress", "grid_stress_ticks", 7),
    ]

    header = "".join(label.center(w) for label, _, w in cols)
    sep    = "-" * sum(w for _, _, w in cols)

    print()
    print("Multi-EV Fleet Scalability Simulation — Summary")
    print(f"  Network: 3-bus CIGRE, 0.4 MVA trafo, {LINE_LENGTH_KM} km line")
    print(f"  Initial SoC: 20–60% (spread), Target: {TARGET_SOC_PCT}%")
    print(f"  Step: {STEP_KW} kW aggregate ({STEP_KW}/N per EV), Initial setpoint: {INIT_SETPOINT_KW} kW/EV")
    print()
    print(sep)
    print(header)
    print(sep)
    for r in results:
        row = "".join(
            str(r.get(key, "N/A")).center(w) for _, key, w in cols
        )
        print(row)
    print(sep)
    print()
    print("Column notes:")
    print("  Dur(min)  — simulated session duration before all EVs reached target (or timeout)")
    print("  Reached   — number of EVs that reached target SoC within the run")
    print("  TTT       — time-to-target SoC (mean/min/max across fleet, minutes)")
    print("  Trafo pk% — peak transformer loading during the session")
    print("  Vbus2 min — minimum per-unit voltage at bus 2 during the session")
    print("  Stress    — number of ticks with a grid-emergency condition active")
    print()


def save_summary_csv(results: list, out_path: Path) -> None:
    if not results:
        return
    keys = list(results[0].keys())
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(keys)
        for r in results:
            w.writerow([r[k] for k in keys])
    print(f"Summary saved to: {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate multi-EV V2G fleet under IEC 104 grid control.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--fleet", nargs="+", type=int, default=[1, 5, 10, 20],
        metavar="N",
        help="Fleet size(s) to simulate.",
    )
    parser.add_argument(
        "--ticks", type=int, default=1800,
        help="Maximum simulation ticks per scenario (1 tick = --dt seconds).",
    )
    parser.add_argument(
        "--dt", type=float, default=4.0,
        help="Seconds of simulated time per tick (= one IEC 104 transmit cycle).",
    )
    args = parser.parse_args()

    total_sim_h = args.ticks * args.dt / 3600.0
    print(f"\nMulti-EV scalability simulation")
    print(f"  Fleet sizes : {args.fleet}")
    print(f"  Max ticks   : {args.ticks} × {args.dt}s = {total_sim_h:.1f} h simulated")
    print(f"  Output dir  : {_LOG_DIR}/")

    results = []
    for n in args.fleet:
        print(f"\n→ Running fleet size N={n} ...", end="", flush=True)
        t0 = time.time()
        r = run_scenario(n, args.ticks, args.dt)
        elapsed = time.time() - t0
        print(f" done in {elapsed:.1f}s  (CSV: {r['out_csv']})")
        results.append(r)

    print_summary(results)

    summary_path = _LOG_DIR / f"multi_ev_summary_{_SESSION}.csv"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    save_summary_csv(results, summary_path)


if __name__ == "__main__":
    main()
