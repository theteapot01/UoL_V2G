import asyncio
import logging
import random
from datetime import datetime, timezone
from charger_state import state

try:
    import websockets
except ModuleNotFoundError:
    print( "This example relies on the 'websockets' package." )
    print( "Please install it by running: " )
    print()
    print( " $ pip install websockets" )
    import sys

    sys.exit( 1 )

from ocpp.v21 import ChargePoint as cp
from ocpp.v21 import call
from ocpp.v21.datatypes import ChargingStationType

from config import Config
import csv

logging.basicConfig( level=logging.INFO )


def simulate_power_watt():
    """
    Simulates realistic V2G power flow in watts.
    Positive = drawing from grid (charging).
    Negative = feeding back to grid (V2G discharge).
    Randomly shifts between the two to demonstrate bidirectional flow.
    """
    mode = random.choice( ["charging", "charging", "discharging"] )  # bias toward charging
    if mode == "charging":
        return round( random.uniform( 2000, 20000 ), 2 )  # up to ~7.4kW (typical AC charging)
    else:
        return round( random.uniform( -20000, -500 ), 2 )  # V2G discharge back to grid


class ChargePoint( cp ):

    async def send_heartbeat( self, interval ):
        request = call.Heartbeat()
        while True:
            await self.call( request )
            await asyncio.sleep( interval )

    async def send_meter_values( self, evse_id: int = 1, interval: int = 10 ):
        """
        Periodically sends MeterValues to the central system.
        Reports active power (W) and cumulative energy (Wh) using data from SharedState.
        """
        cumulative_energy_wh = 0.0

        while True:
            telemetry = state.latest
            power_w = telemetry.power_kw * 1000.0  # Telemetry has kW
            # Accumulate energy - only add when charging (positive power)
            if power_w > 0:
                cumulative_energy_wh += (power_w * interval) / 3600

            timestamp = datetime.now( timezone.utc ).strftime( "%Y-%m-%dT%H:%M:%S" ) + "Z"

            request = call.MeterValues(
                evse_id=evse_id,
                meter_value=[
                    {
                        "timestamp": timestamp,
                        "sampled_value": [
                            {
                                # Active power — negative means discharging to grid (V2G)
                                "value": power_w,
                                "measurand": "Power.Active.Import",
                                "unit_of_measure": { "unit": "W" },
                                "context": "Sample.Periodic",
                                },
                            {
                                # Cumulative energy imported from grid this session
                                "value": round( cumulative_energy_wh, 3 ),
                                "measurand": "Energy.Active.Import.Register",
                                "unit_of_measure": { "unit": "Wh" },
                                "context": "Sample.Periodic",
                                },
                            {
                                # State of Charge
                                "value": round( telemetry.soc_percent, 1 ),
                                "measurand": "SoC",
                                "unit_of_measure": { "unit": "Percent" },
                                "context": "Sample.Periodic",
                                },
                            ],
                        }
                    ],
                )

            await self.call( request )

            direction = "⬆ V2G discharge" if power_w < 0 else "⬇ Charging"
            logging.info(
                "MeterValues sent | EVSE %s | Power: %.1f W (%s) | SoC: %.1f%% | Energy: %.2f Wh",
                evse_id, power_w, direction, telemetry.soc_percent, cumulative_energy_wh
                )

            await asyncio.sleep( interval )

    async def send_boot_notification( self ):
        request = call.BootNotification(
            charging_station=ChargingStationType(
                model="MSc Embedded", vendor_name="UoL"
                ),
            reason="PowerUp",
            )
        response = await self.call( request )

        if response.status == "Accepted":
            print( "Connected to central system." )
            # Start heartbeat and meter values concurrently
            await asyncio.gather(
                self.send_heartbeat( response.interval ),
                self.send_meter_values(evse_id=1, interval=10),
                # instead need to have extra function checking if EV is connected
                )


async def run_ocpp_client():
    while True:
        try:
            url = f"ws://{Config.OCPP_SERVER}/CP_1"
            print(f"Connecting to: {url}")
            async with websockets.connect(
                    url, subprotocols=["ocpp2.1"]
                    ) as ws:
                charge_point = ChargePoint( "CP_1", ws )
                await asyncio.gather(
                    charge_point.start(), charge_point.send_boot_notification()
                    )
        except OSError:
            logging.info( "OCPP server not available yet, retrying in 3s..." )
            await asyncio.sleep( 3 )


if __name__ == "__main__":
    asyncio.run( run_ocpp_client() )
