"""
iecc104_server.py
=================
EV charger server for V2G (Vehicle-to-Grid) communication over IEC 60870-5-104.

This module acts as the controlled station (server) in the IEC 104 protocol,
representing the physical EV charger. It exposes real-time telemetry to the
grid operator's SCADA client and handles incoming commands to regulate the
direction and magnitude of power flow.

Architecture
------------
The SECC does NOT own a battery model. All telemetry (SoC, power, temperature)
is forwarded from the EV via ISO 15118 and stored in ``state.latest`` by the
TelemetryEVSEController. The IEC 104 callbacks simply read from that snapshot.

Grid commands (HIGHER / LOWER step commands on IOA 12) adjust
``state.grid_power_setpoint_kw``. The TelemetryEVSEController translates that
setpoint into EVSE power limits inside ``DC_ChargeLoopRes``, which the EV then
respects when choosing its target current.

IOA map
-------
  IOA 11 (M_ME_NC_1) : power [kW]          — from state.latest.power_kw
  IOA 12 (C_RC_TA_1) : regulating step command (HIGHER / LOWER)
  IOA 13 (M_ME_NC_1) : SoC [%]             — from state.latest.soc_percent
  IOA 14 (M_ME_NC_1) : temperature [°C]    — from state.latest.temperature_c
  IOA 15 (M_ME_NC_1) : EV voltage [V]      — from state.latest.voltage_v
  IOA 16 (M_ME_NC_1) : EV current [A]      — from state.latest.current_a
  IOA 17 (M_ME_NC_1) : loop time [ms]      — from state.iso_loop_ms
"""

import datetime
import os
import time

os.environ["PYTHONUNBUFFERED"] = "1"

import c104
import asyncio
from charger_state import state
from config import Config


def _build_tls() -> c104.TransportSecurity:
    """
    IEC 62351-3: TLS for the IEC 104 controlled station (server side).
    Mutual TLS: server presents its cert; validates the grid Pi client cert
    against the shared CA (IEC 62351-3).
    """
    tls = c104.TransportSecurity(validate=True, only_known=False)
    tls.set_certificate(cert=Config.IEC104_SERVER_CERT, key=Config.IEC104_SERVER_KEY)
    tls.set_ca_certificate(cert=Config.IEC104_CA_CERT)
    return tls


# ------------------------------------------------------------
#                        Helpers
# ------------------------------------------------------------

def _departure_blocks_v2g() -> bool:
    """
    Return True when the user's departure window is approaching and SoC is
    below their target, so a HIGHER (discharge / V2G) command should be
    suppressed to allow the battery to charge up before departure.

    The threshold matches the grid-side logic in iec104_panda.py: within 60
    minutes of departure AND SoC below pref_target_soc_pct.
    """
    departure = state.pref_departure_time
    if not departure:
        return False
    try:
        now = datetime.datetime.now()
        h, m = map(int, departure.split(":"))
        dep = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if dep <= now:
            dep += datetime.timedelta(days=1)
        minutes = (dep - now).total_seconds() / 60.0
        return minutes < 60 and state.latest.soc_percent < state.pref_target_soc_pct
    except (ValueError, AttributeError):
        return False


# ------------------------------------------------------------
#                        Callbacks
# ------------------------------------------------------------

def on_step_command(
        point: c104.Point, previous_info: c104.Information,
        message: c104.IncomingMessage
        ) -> c104.ResponseState:
    """
    Handle an incoming regulating step command from the grid.

    HIGHER → decrease setpoint (more discharge / less charge)
    LOWER  → increase setpoint (more charge / less discharge)

    The setpoint is stored in shared state; the TelemetryEVSEController reads
    it and relays it to the EV as EVSE power limits in the next
    DC_ChargeLoopRes.
    """
    print(
        "STEP COMMAND on IOA: {0}, message: {1}, previous: {2}, current: {3}"
        .format(point.io_address, message, previous_info, point.info)
    )

    if not state.ev_connected:
        print("No EV connected — ignoring step command")
        return c104.ResponseState.SUCCESS

    state.command_received = True

    if point.value == c104.Step.HIGHER:
        if state.latest.soc_percent <= state.pref_min_soc_pct:
            print(
                f"SoC floor ({state.pref_min_soc_pct:.0f}%) — ignoring HIGHER "
                f"(SoC={state.latest.soc_percent:.1f}%)"
            )
            return c104.ResponseState.SUCCESS
        if _departure_blocks_v2g():
            print(
                f"Departure window ({state.pref_departure_time}) < 60 min, "
                f"SoC {state.latest.soc_percent:.1f}% < target "
                f"{state.pref_target_soc_pct:.0f}% — ignoring HIGHER"
            )
            return c104.ResponseState.SUCCESS
        state.grid_power_setpoint_kw -= state.step_kw
        cmd_str = "HIGHER"
    elif point.value == c104.Step.LOWER:
        state.grid_power_setpoint_kw += state.step_kw
        cmd_str = "LOWER"
    else:
        print(f"Unknown step value: {point.value}")
        return c104.ResponseState.FAILURE

    # Clamp to configured limits
    state.grid_power_setpoint_kw = max(
        -state.max_discharge_kw,
        min(state.max_charge_kw, state.grid_power_setpoint_kw)
    )

    # Stamp the command time so TelemetryEVSEController can measure
    # how long it takes for the next DC_ChargeLoopRes to carry the new limits.
    state.last_command_t = time.monotonic()
    state.last_command_str = cmd_str

    direction = "DISCHARGE" if state.grid_power_setpoint_kw < 0 else "CHARGE"
    print(
        f"Grid setpoint now: {state.grid_power_setpoint_kw:+.1f} kW ({direction})"
    )
    return c104.ResponseState.SUCCESS


def _update_point(point: c104.Point) -> None:
    """
    Populate an IEC 104 monitoring point from the latest EV telemetry.

    Single code path — all data comes from state.latest, which is written
    by TelemetryEVSEController each time it receives a charge-loop request
    from the EV.
    """
    telemetry = state.latest
    if point.io_address == Config.METER_VALUES:
        point.value = telemetry.power_kw
    elif point.io_address == Config.SOC_VAL:
        point.value = telemetry.soc_percent
    elif point.io_address == Config.READ_TEMP:
        point.value = telemetry.temperature_c
    elif point.io_address == Config.EV_VOLTAGE:
        point.value = telemetry.voltage_v
    elif point.io_address == Config.EV_CURRENT:
        point.value = telemetry.current_a
    elif point.io_address == Config.ISO_LOOP_MS:
        point.value = state.iso_loop_ms


def before_auto_transmit(point: c104.Point) -> None:
    """Update point value before periodic automatic report."""
    _update_point(point)
    print(
        "BEFORE AUTOMATIC REPORT on IOA: {0} VALUE: {1}"
        .format(point.io_address, point.value)
    )


def before_read(point: c104.Point) -> None:
    """Update point value before responding to a client interrogation/read."""
    _update_point(point)
    print(
        "BEFORE READ or INTERROGATION on IOA: {0} VALUE: {1}"
        .format(point.io_address, point.value)
    )


# ------------------------------------------------------------
#                     Server entry point
# ------------------------------------------------------------

async def run_iec104_server():
    c104.set_debug_mode(c104.Debug.Server | c104.Debug.Connection)
    tls = _build_tls()
    print(f"IEC104 TLS object created: {tls}")
    server = c104.Server(
        ip="0.0.0.0",
        port=Config.PORT_TLS,
        transport_security=tls,
    )
    station = server.add_station(common_address=Config.COMMON_ADDRESS)

    # monitoring point: power [kW]
    point_meter = station.add_point(
        io_address=Config.METER_VALUES, type=c104.Type.M_ME_NC_1, report_ms=2000
    )
    point_meter.on_before_auto_transmit(callable=before_auto_transmit)
    point_meter.on_before_read(callable=before_read)

    # monitoring point: SoC [%]
    point_soc = station.add_point(
        io_address=Config.SOC_VAL, type=c104.Type.M_ME_NC_1, report_ms=1000
    )
    point_soc.on_before_auto_transmit(callable=before_auto_transmit)
    point_soc.on_before_read(callable=before_read)

    # monitoring point: temperature [°C]
    point_temp = station.add_point(
        io_address=Config.READ_TEMP, type=c104.Type.M_ME_NC_1, report_ms=1000
    )
    point_temp.on_before_auto_transmit(callable=before_auto_transmit)
    point_temp.on_before_read(callable=before_read)

    # monitoring point: EV voltage [V]
    point_voltage = station.add_point(
        io_address=Config.EV_VOLTAGE, type=c104.Type.M_ME_NC_1, report_ms=2000
    )
    point_voltage.on_before_auto_transmit(callable=before_auto_transmit)
    point_voltage.on_before_read(callable=before_read)

    # monitoring point: EV current [A]
    point_current = station.add_point(
        io_address=Config.EV_CURRENT, type=c104.Type.M_ME_NC_1, report_ms=2000
    )
    point_current.on_before_auto_transmit(callable=before_auto_transmit)
    point_current.on_before_read(callable=before_read)

    # monitoring point: ISO 15118 charge-loop processing time [ms]
    point_loop_ms = station.add_point(
        io_address=Config.ISO_LOOP_MS, type=c104.Type.M_ME_NC_1, report_ms=2000
    )
    point_loop_ms.on_before_auto_transmit(callable=before_auto_transmit)
    point_loop_ms.on_before_read(callable=before_read)

    # command point: regulating step (HIGHER / LOWER)
    command = station.add_point(
        io_address=Config.CHARGE_CMD, type=c104.Type.C_RC_TA_1
    )
    command.on_receive(callable=on_step_command)

    # start
    server.start()
    print(f"IEC104 server started on port {Config.PORT_TLS} (TLS)")

    while not server.has_active_connections:
        print("Waiting for connection")
        await asyncio.sleep(1)

    await asyncio.sleep(1)

    while server.has_open_connections:
        print("Keep alive until disconnected")
        await asyncio.sleep(1)


if __name__ == "__main__":
    # c104.set_debug_mode(c104.Debug.Server | c104.Debug.Point | c104.Debug.Callback)
    asyncio.run(run_iec104_server())
