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
