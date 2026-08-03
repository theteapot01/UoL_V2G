"""
charger.py
==========
Entry point for the charger Pi.

Starts the full V2G stack in a single command:
  - ISO 15118 SECC  (TelemetryEVSEController — EV ↔ EVSE charge-loop)
  - IEC 60870-5-104 server  (exposes telemetry, receives grid step commands)
  - OCPP 2.1 client  (forwards MeterValues to the CSMS on the grid Pi)

All three services are managed by run_secc.py under the iso15118 Poetry
environment.  This script sets the required working directory and PYTHONPATH,
then launches run_secc.py as a subprocess so the Poetry virtualenv resolves
correctly.

Usage:
    python charger.py

Prerequisites:
    cd code_charger/iso15118 && poetry install          # one-time
    cd iso15118/shared/pki && ./create_certs.sh -v iso-2  # one-time (ISO 15118 PKI)
    ./create_ocpp_certs.sh                              # one-time (OCPP mTLS PKI)

Core functions:
    main() — sets PYTHONPATH, launches run_secc.py as a `poetry run` subprocess under code_charger/iso15118, and forwards Ctrl-C for a clean shutdown.
"""

import asyncio
import os
import pathlib
import signal
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
ISO_ROOT     = PROJECT_ROOT / "code_charger" / "iso15118"
SECC_SCRIPT  = PROJECT_ROOT / "code_iso15118_custom" / "run_secc.py"


async def main() -> None:
    env = os.environ.copy()
    # run_secc.py imports from code_iso15118_custom/ (telemetry_evse_controller etc.)
    secc_dir = str(PROJECT_ROOT / "code_iso15118_custom")
    existing_pypath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{secc_dir}:{existing_pypath}" if existing_pypath else secc_dir

    print(f"Starting SECC stack via: poetry run python {SECC_SCRIPT}")
    print(f"  cwd        : {ISO_ROOT}")
    print(f"  PYTHONPATH : {env['PYTHONPATH']}")

    proc = await asyncio.create_subprocess_exec(
        "poetry", "run", "python", str(SECC_SCRIPT),
        cwd=str(ISO_ROOT),
        env=env,
    )

    try:
        await proc.wait()
    except asyncio.CancelledError:
        # Forward Ctrl-C to the child so run_secc.py shuts down cleanly.
        proc.send_signal(signal.SIGINT)
        await proc.wait()
        raise

    if proc.returncode and proc.returncode != 0:
        print(f"SECC process exited with code {proc.returncode}", file=sys.stderr)
        sys.exit(proc.returncode)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Closing connections...")
