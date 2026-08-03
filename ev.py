"""
ev.py
=====
Entry point for the EV (EVCC) side of the V2G prototype.

Starts the ISO 15118 EVCC with a battery simulation controller in a single
command.  The EVCC connects to the SECC (charger Pi) over ISO 15118 DC and
drives SoC from either a live SimulatedBattery or a CSV replay profile.

All imports from code_iso15118_custom/ are resolved by setting PYTHONPATH
before launching run_evcc.py as a subprocess under the iso15118 Poetry
virtualenv.

Usage:
    python ev.py

Environment variables (all optional):
    EVCC_CONTROLLER        battery (default), battery_csv, sim
    EVCC_PROFILE_PATH      path to CSV battery profile (battery_csv mode)
    EVCC_MAX_STEPS         integer — cap charge loop for bounded tests
    EVCC_INIT_SETPOINT_KW  float (default 17.0) — initial charge power [kW]
    EVCC_TARGET_SOC        float (default 80.0) — SoC at which charging ends

Prerequisites:
    cd code_charger/iso15118 && poetry install          # one-time
    cd iso15118/shared/pki && ./create_certs.sh -v iso-2  # one-time (ISO 15118 PKI)

Core functions:
    main() — sets PYTHONPATH, launches run_evcc.py as a `poetry run` subprocess under code_charger/iso15118, and forwards Ctrl-C for a clean shutdown.
"""

import asyncio
import os
import pathlib
import signal
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
ISO_ROOT     = PROJECT_ROOT / "code_charger" / "iso15118"
EVCC_SCRIPT  = PROJECT_ROOT / "code_iso15118_custom" / "run_evcc.py"


async def main() -> None:
    env = os.environ.copy()
    custom_dir = str(PROJECT_ROOT / "code_iso15118_custom")
    existing_pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{custom_dir}:{existing_pypath}" if existing_pypath else custom_dir

    print(f"Starting EVCC stack via: poetry run python {EVCC_SCRIPT}")
    print(f"  cwd        : {ISO_ROOT}")
    print(f"  PYTHONPATH : {env['PYTHONPATH']}")
    print(f"  EVCC_CONTROLLER : {env.get('EVCC_CONTROLLER', 'battery (default)')}")

    proc = await asyncio.create_subprocess_exec(
        "poetry", "run", "python", str(EVCC_SCRIPT),
        cwd=str(ISO_ROOT),
        env=env,
    )

    try:
        await proc.wait()
    except asyncio.CancelledError:
        proc.send_signal(signal.SIGINT)
        await proc.wait()
        raise

    if proc.returncode and proc.returncode != 0:
        print(f"EVCC process exited with code {proc.returncode}", file=sys.stderr)
        sys.exit(proc.returncode)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Closing connections...")
