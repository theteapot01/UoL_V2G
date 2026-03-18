import asyncio
import logging
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

from ocpp.routing import on
from ocpp.v21 import ChargePoint as cp
from ocpp.v21 import call_result
from ocpp.v21.enums import Action

logging.basicConfig(level=logging.INFO)


class ChargePoint(cp):

    @on(Action.boot_notification)
    def on_boot_notification(self, charging_station, reason, **kwargs):
        return call_result.BootNotification(
            current_time=datetime.now(timezone.utc).isoformat(),
            interval=10,
            status="Accepted",
        )

    @on(Action.heartbeat)
    def on_heartbeat(self):
        print("Got a Heartbeat!")
        return call_result.Heartbeat(
            current_time=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        )

    @on(Action.meter_values)
    def on_meter_values(self, evse_id, meter_value, **kwargs):
        """
        Receives MeterValues from the charge point and logs them.
        Negative Power.Active.Import = EV feeding energy back to grid (V2G).
        """
        for mv in meter_value:
            timestamp = mv.get("timestamp", "unknown time")
            for sample in mv.get("sampled_value", []):
                measurand = sample.get("measurand", "Unknown")
                value = sample.get("value", 0)
                unit_obj = sample.get("unit_of_measure", {})
                unit = unit_obj.get("unit", "") if isinstance(unit_obj, dict) else ""

                # Flag V2G discharge events specifically
                if measurand == "Power.Active.Import":
                    if float(value) < 0:
                        logging.info(
                            "⚡ V2G DISCHARGE | EVSE %s | %.1f %s at %s",
                            evse_id, float(value), unit, timestamp
                        )
                    else:
                        logging.info(
                            "🔋 CHARGING      | EVSE %s | %.1f %s at %s",
                            evse_id, float(value), unit, timestamp
                        )
                else:
                    logging.info(
                        "📊 METER VALUE   | EVSE %s | %s: %s %s at %s",
                        evse_id, measurand, value, unit, timestamp
                    )

        return call_result.MeterValues()


async def on_connect(websocket):
    """For every new charge point that connects, create a ChargePoint
    instance and start listening for messages.
    """
    try:
        requested_protocols = websocket.request.headers["Sec-WebSocket-Protocol"]
    except KeyError:
        logging.error("Client hasn't requested any Subprotocol. Closing Connection")
        return await websocket.close()
    if websocket.subprotocol:
        logging.info("Protocols Matched: %s", websocket.subprotocol)
    else:
        logging.warning(
            "Protocols Mismatched | Expected Subprotocols: %s,"
            " but client supports %s | Closing connection",
            websocket.available_subprotocols,
            requested_protocols,
        )
        return await websocket.close()

    charge_point_id = websocket.request.path.strip("/")
    charge_point = ChargePoint(charge_point_id, websocket)

    await charge_point.start()


async def main():
    server = await websockets.serve(
        on_connect, "0.0.0.0", 9000, subprotocols=["ocpp2.1"]
    )

    logging.info("Server Started listening to new connections...")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())