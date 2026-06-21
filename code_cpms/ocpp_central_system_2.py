"""
Central System (CPMS) - OCPP 2.1 Implementation
=================================================
This module implements a basic Charging Station Management System (CPMS)
using the OCPP 2.1 protocol over WebSockets.

Project context:
    Part of a V2G (Vehicle-to-Grid) communication protocol research project.
    The CPMS acts as the grid operator's backend, managing connected charge
    points and facilitating bidirectional energy flow between EVs and the grid.

Handled messages (Charge Point → CPMS):
    - BootNotification       : Registers a charge point on startup
    - Authorize              : Validates a driver's ID token (RFID, app token etc.)
    - Heartbeat              : Periodic keepalive from the charge point
    - MeterValues            : Real-time power/energy readings, including V2G discharge

Planned messages:
    - TransactionEvent       : Session lifecycle (started, updated, ended)
    - NotifyChargingLimit    : Charge point reports local grid constraints
    - NotifyEVChargingSchedule : EV communicates its charging plan
    - SetChargingProfile     : CPMS controls charge/discharge rate (core V2G control)
    - StatusNotification     : Connector state changes (Available, Occupied, Faulted)

Token authorization:
    Currently uses a hardcoded whitelist (VALID_TOKENS) for development purposes.

Usage:
    Run this script on the central system (laptop/server):
        $ python3 central_system.py

    The server listens on port 9000 with mutual TLS (Security Profile 3).
    Charge points connect via:
        wss://<host>:9000/<charge_point_id>

    Generate certificates once (run from project root):
        $ ./create_ocpp_certs.sh [CSMS_IP]

    If running across subnets (e.g. university network), use an SSH tunnel:
        $ ssh -L 9000:localhost:9000 <user>@<CPMS_host> -N

Dependencies:
    pip install ocpp websockets
"""

# --------------------------------------------------------------
#                   Imports
# --------------------------------------------------------------
import asyncio
import logging
import ssl
import time
from datetime import datetime, timezone

from code_grid.grid_state import grid_state
from config import Config

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
from ocpp.v21.enums import Action

logging.basicConfig( level=logging.INFO )

# A simple whitelist of valid tokens
VALID_TOKENS = { "RFID-001", "RFID-002", "APP-TOKEN-123" }


# --------------------------------------------------------------
#                   Class for CPMS message setup
# --------------------------------------------------------------

class ChargePoint( cp ):

    # notification from charge point when booting
    @on( Action.boot_notification )
    def on_boot_notification( self, charging_station, reason, **kwargs ):
        return call_result.BootNotification(
            current_time=datetime.now( timezone.utc ).isoformat(),
            interval=10,
            status="Accepted",
            )

    # called when authorize request from charge point comes
    @on( Action.authorize )
    def on_authorize( self, id_token, **kwargs ):
        # id_token is a dict like: {"id_token": "RFID-001", "type": "ISO14443"}
        token_value = id_token.get( "id_token", "" )

        if token_value in VALID_TOKENS:
            status = "Accepted"
            logging.info( "Authorized: %s", token_value )
        else:
            status = (
                "Unknown"  # OCPP uses "Unknown" not "Rejected" for unrecognised tokens
            )
            logging.warning( "Authorization failed: %s", token_value )

        return call_result.Authorize( id_token_info={ "status": status } )

    # periodic heartbeat from charge point to show its online
    @on( Action.heartbeat )
    def on_heartbeat( self ):
        print( "Got a Heartbeat!" )
        return call_result.Heartbeat(
            current_time=datetime.now( timezone.utc ).strftime( "%Y-%m-%dT%H:%M:%S" ) + "Z"
            )

    # Log meter values
    @on( Action.meter_values )
    def on_meter_values( self, evse_id, meter_value, **kwargs ):
        """
        Receives MeterValues from the charge point and logs them.
        Negative Power.Active.Import = EV feeding energy back to grid (V2G).
        """
        _power_w   = 0.0
        _energy_wh = 0.0
        _soc_pct   = 0.0

        for mv in meter_value:
            timestamp = mv.get( "timestamp", "unknown time" )
            for sample in mv.get( "sampled_value", [] ):
                measurand = sample.get( "measurand", "Unknown" )
                value = sample.get( "value", 0 )
                unit_obj = sample.get( "unit_of_measure", { } )
                unit = unit_obj.get( "unit", "" ) if isinstance( unit_obj, dict ) else ""

                # Snapshot for dashboard
                if measurand == "Power.Active.Import":
                    _power_w = float( value )
                elif measurand == "Energy.Active.Import.Register":
                    _energy_wh = float( value )
                elif measurand == "SoC":
                    _soc_pct = float( value )
                elif measurand == "Voltage":
                    grid_state.ocpp.voltage_v = float( value )
                elif measurand == "Current.Import":
                    grid_state.ocpp.current_a = float( value )
                elif measurand == "Power.Import.Offered":
                    grid_state.ocpp.evse_max_charge_kw = float( value ) / 1000.0
                elif measurand == "Power.Export.Offered":
                    grid_state.ocpp.evse_max_discharge_kw = float( value ) / 1000.0

                # Flag V2G discharge events specifically
                if measurand == "Power.Active.Import":
                    if float( value ) < 0:
                        logging.info(
                            "V2G DISCHARGE | EVSE %s | %.1f %s at %s",
                            evse_id,
                            float( value ),
                            unit,
                            timestamp,
                            )
                    else:
                        logging.info(
                            "CHARGING      | EVSE %s | %.1f %s at %s",
                            evse_id,
                            float( value ),
                            unit,
                            timestamp,
                            )
                else:
                    logging.info(
                        "METER VALUE   | EVSE %s | %s: %s %s at %s",
                        evse_id,
                        measurand,
                        value,
                        unit,
                        timestamp,
                        )

        # Persist to shared state so the web dashboard can display OCPP data.
        grid_state.ocpp.power_w    = _power_w
        grid_state.ocpp.energy_wh  = _energy_wh
        grid_state.ocpp.soc_percent = _soc_pct
        grid_state.ocpp.timestamp  = time.time()

        return call_result.MeterValues()

    async def send_preferences(self, prefs) -> None:
        """Push user preferences to the charge point via OCPP SetVariables."""
        await self.call(call.SetVariables(set_variable_data=[
            {"attribute_value": str(prefs.min_soc_pct),    "component": {"name": "UserPreferences"}, "variable": {"name": "MinSoC"}},
            {"attribute_value": str(prefs.max_soc_pct),    "component": {"name": "UserPreferences"}, "variable": {"name": "MaxSoC"}},
            {"attribute_value": str(prefs.target_soc_pct), "component": {"name": "UserPreferences"}, "variable": {"name": "TargetSoC"}},
            {"attribute_value": prefs.departure_time,       "component": {"name": "UserPreferences"}, "variable": {"name": "DepartureTime"}},
        ]))


# --------------------------------------------------------------
#                   Example on connect
# --------------------------------------------------------------

async def on_connect( websocket ):
    """For every new charge point that connects, create a ChargePoint
    instance and start listening for messages.
    """
    try:
        requested_protocols = websocket.request.headers["Sec-WebSocket-Protocol"]
    except KeyError:
        logging.error( "Client hasn't requested any Subprotocol. Closing Connection" )
        return await websocket.close()
    if websocket.subprotocol:
        logging.info( "Protocols Matched: %s", websocket.subprotocol )
    else:
        logging.warning(
            "Protocols Mismatched | Expected Subprotocols: %s,"
            " but client supports %s | Closing connection",
            websocket.available_subprotocols,
            requested_protocols,
            )
        return await websocket.close()

    charge_point_id = websocket.request.path.strip( "/" )
    charge_point = ChargePoint( charge_point_id, websocket )

    grid_state.connected_charge_point = charge_point
    try:
        await charge_point.start()
    finally:
        grid_state.connected_charge_point = None


def _build_server_ssl_context() -> ssl.SSLContext:
    """
    Security Profile 3: TLS server that requires a valid client certificate.
    The CSMS presents its own cert; the charge point must present a cert
    signed by the same CA before the WebSocket handshake proceeds.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(Config.OCPP_CSMS_CERT, Config.OCPP_CSMS_KEY)
    ctx.load_verify_locations(Config.OCPP_CA_CERT)
    ctx.verify_mode = ssl.CERT_REQUIRED   # reject connections without a client cert
    return ctx


async def run_ocpp_server():
    ssl_ctx = _build_server_ssl_context()
    server = await websockets.serve(
        on_connect, "0.0.0.0", 9000,
        subprotocols=["ocpp2.1"],
        ssl=ssl_ctx,
        )

    logging.info("CSMS listening on wss://0.0.0.0:9000 (Security Profile 3 — mTLS)")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run( run_ocpp_server() )
