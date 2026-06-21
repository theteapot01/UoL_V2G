import asyncio
import logging
import random
import ssl
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

from ocpp.routing import on
from ocpp.v21 import ChargePoint as cp
from ocpp.v21 import call, call_result
from ocpp.v21.datatypes import ChargingStationType
from ocpp.v21.enums import Action

from config import Config

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


_PREF_HANDLERS = {
    "MinSoC":        lambda v: setattr(state, "pref_min_soc_pct",    float(v)),
    "MaxSoC":        lambda v: setattr(state, "pref_max_soc_pct",    float(v)),
    "TargetSoC":     lambda v: setattr(state, "pref_target_soc_pct", float(v)),
    "DepartureTime": lambda v: setattr(state, "pref_departure_time",  str(v)),
}


class ChargePoint( cp ):

    @on(Action.set_variables)
    async def on_set_variables(self, set_variable_data, **kwargs):
        results = []
        for item in set_variable_data:
            component = item.get("component", {}).get("name", "")
            variable  = item.get("variable",  {}).get("name", "")
            value     = item.get("attribute_value", "")

            if component == "UserPreferences" and variable in _PREF_HANDLERS:
                try:
                    _PREF_HANDLERS[variable](value)
                    status = "Accepted"
                    logging.info("Preference set: %s = %s", variable, value)
                except (ValueError, TypeError) as exc:
                    status = "Rejected"
                    logging.warning("Bad preference value %s=%r: %s", variable, value, exc)
            else:
                status = "UnknownComponent" if component != "UserPreferences" else "UnknownVariable"

            results.append({
                "attribute_status": status,
                "component": {"name": component},
                "variable":  {"name": variable},
            })
        return call_result.SetVariables(set_variable_result=results)

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
                            {
                                # EV target voltage from ISO 15118 DC_ChargeLoopReq
                                "value": round( telemetry.voltage_v, 1 ),
                                "measurand": "Voltage",
                                "unit_of_measure": { "unit": "V" },
                                "context": "Sample.Periodic",
                                },
                            {
                                # EV target current from ISO 15118 DC_ChargeLoopReq
                                "value": round( telemetry.current_a, 2 ),
                                "measurand": "Current.Import",
                                "unit_of_measure": { "unit": "A" },
                                "context": "Sample.Periodic",
                                },
                            {
                                # EVSE max charge power offered to EV in DC_ChargeLoopRes [W]
                                "value": round( state.iso_evse_max_charge_w, 1 ),
                                "measurand": "Power.Import.Offered",
                                "unit_of_measure": { "unit": "W" },
                                "context": "Sample.Periodic",
                                },
                            {
                                # EVSE max discharge power offered to grid in DC_ChargeLoopRes [W]
                                "value": round( state.iso_evse_max_discharge_w, 1 ),
                                "measurand": "Power.Export.Offered",
                                "unit_of_measure": { "unit": "W" },
                                "context": "Sample.Periodic",
                                },
                            ],
                        }
                    ],
                )

            await self.call( request )

            direction = "⬆ V2G discharge" if power_w < 0 else "⬇ Charging"
            logging.info(
                "MeterValues sent | EVSE %s | Power: %.1f W (%s) | SoC: %.1f%% | SoH: %.1f%% | Energy: %.2f Wh",
                evse_id, power_w, direction, telemetry.soc_percent, telemetry.soh_percent, cumulative_energy_wh
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


def _build_client_ssl_context() -> ssl.SSLContext:
    """
    Security Profile 3: TLS client that presents the charge point certificate.
    The CSMS server is verified against the shared CA; hostname/IP checking
    uses the SAN embedded in the CSMS cert by create_ocpp_certs.sh.
    """
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(Config.OCPP_CA_CERT)           # trust our CA
    ctx.load_cert_chain(Config.OCPP_CP_CERT, Config.OCPP_CP_KEY)  # client identity
    return ctx


async def run_ocpp_client():
    ssl_ctx = _build_client_ssl_context()
    while True:
        try:
            url = f"wss://{Config.OCPP_SERVER}/CP_1"
            print(f"Connecting to: {url}")
            async with websockets.connect(
                    url,
                    subprotocols=["ocpp2.1"],
                    ssl=ssl_ctx,
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
