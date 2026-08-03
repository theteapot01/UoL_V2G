"""
grid.py
=======
Entry point for the Grid Pi.

Runs the grid operator's three concurrent services in one process: the IEC
104 client that reads charger telemetry and issues HIGHER/LOWER step
commands, the OCPP 2.1 central system (CPMS) that receives MeterValues from
the charger, and the FastAPI web dashboard that visualises both.

Core functions:
    main()  — starts all three services together via asyncio.gather() and
              runs until interrupted (Ctrl-C).

Usage:
    python grid.py
"""

import asyncio

from code_cpms.ocpp_central_system_2 import run_ocpp_server
from code_grid.iec104_panda import run_iec104_client
from code_grid.web_dashboard import run_web_server


async def main():
    await asyncio.gather(run_iec104_client(), run_ocpp_server(), run_web_server())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Closing connections...")
