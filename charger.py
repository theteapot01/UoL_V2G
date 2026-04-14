import asyncio

from code_grid.iecc104_server import run_iec104_server
from code_cpms.ocpp_charge_point_2 import run_ocpp_client


async def main():
    asyncio.gather(run_iec104_server(), run_ocpp_client())


if __name__ == "__main__":
    asyncio.run(mains())
