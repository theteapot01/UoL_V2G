import asyncio
import logging
import random
from datetime import datetime, timezone

try:
    import websockets
except ModuleNotFoundError:
    print("This example relies on the 'websockets' package.")
    print("Please install it by running: ")
    print()
    print(" $ pip install websockets")
    import sys
    sys.exit(1)

from ocpp.v21 import ChargePoint as cp
from ocpp.v21 import call
from ocpp.v21.datatypes import ChargingStationType

logging.basicConfig(level=logging.INFO)


def simulate_power_watt():
    """
    Simulates realistic V2G power flow in watts.
    Positive = drawing from grid (charging).
    Negative = feeding back to grid (V2G discharge).
    Randomly shifts between the two to demonstrate bidirectional flow.
    """
    mode = random.choice(["charging", "charging", "discharging"])  # bias toward charging
    if mode == "charging":
        return round(random.uniform(2000, 7400), 2)   # up to ~7.4kW (typical AC charging)
    else:
        return round(random.uniform(-3600, -500), 2)  # V2G discharge back to grid


class ChargePoint(cp):

    async def send_heartbeat(self, interval):
        request = call.Heartbeat()
        while True:
            await self.call(request)
            await asyncio.sleep(interval)

    async def send_meter_values(self, evse_id: int = 1, interval: int = 10):
        """
        Periodically sends MeterValues to the central system.
        Reports active power (W) and cumulative energy (Wh).
        A negative Power.Active.Import value signals V2G discharge.
        """
        cumulative_energy_wh = 0.0

        while True:
            power_w = simulate_power_watt()

            # Accumulate energy — only add when charging (positive power)
            if power_w > 0:
                cumulative_energy_wh += (power_w * interval) / 3600

            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"

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
                                "unit_of_measure": {"unit": "W"},
                                "context": "Sample.Periodic",
                            },
                            {
                                # Cumulative energy imported from grid this session
                                "value": round(cumulative_energy_wh, 3),
                                "measurand": "Energy.Active.Import.Register",
                                "unit_of_measure": {"unit": "Wh"},
                                "context": "Sample.Periodic",
                            },
                        ],
                    }
                ],
            )

            await self.call(request)

            direction = "⬆ V2G discharge" if power_w < 0 else "⬇ Charging"
            logging.info(
                "MeterValues sent | EVSE %s | Power: %.1f W (%s) | Energy: %.2f Wh",
                evse_id, power_w, direction, cumulative_energy_wh
            )

            await asyncio.sleep(interval)

    async def send_boot_notification(self):
        request = call.BootNotification(
            charging_station=ChargingStationType(
                model="Wallbox XYZ", vendor_name="anewone"
            ),
            reason="PowerUp",
        )
        response = await self.call(request)

        if response.status == "Accepted":
            print("Connected to central system.")
            # Start heartbeat and meter values concurrently
            await asyncio.gather(
                self.send_heartbeat(response.interval),
                self.send_meter_values(evse_id=1, interval=10),
            )


async def main():
    async with websockets.connect(
        "ws://localhost:9000/CP_1", subprotocols=["ocpp2.1"]
    ) as ws:
        charge_point = ChargePoint("CP_1", ws)
        await asyncio.gather(
            charge_point.start(), charge_point.send_boot_notification()
        )


if __name__ == "__main__":
    asyncio.run(main())
