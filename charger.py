import asyncio

from code_cpms.ocpp_charge_point_2 import run_ocpp_client
from code_grid.iecc104_server import run_iec104_server


async def main():
    await asyncio.gather( run_iec104_server(), run_ocpp_client() )


if __name__ == "__main__":
    try:
        asyncio.run( main() )
    except KeyboardInterrupt:
        print( "Closing connections..." )
