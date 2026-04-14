"""
iecc104_server.py
=================
EV charger server for V2G (Vehicle-to-Grid) communication over IEC 60870-5-104.

This module acts as the controlled station (server) in the IEC 104 protocol, representing
the physical EV charger. It exposes real-time telemetry to the grid operator's SCADA client
and handles incoming commands to regulate the direction and magnitude of power flow.

IOA ... Information Object Address

Data points exposed:
  - IOA 11 (M_ME_NC_1): measured float value representing current power flow (watts).
    Auto-reported to all connected clients every 1000 ms. Value is refreshed via
    before_auto_transmit() and before_read() callbacks (currently using random simulation;
    replace with real BMS/charger hardware reads in production).

  - IOA 12 (C_RC_TA_1): timestamped regulating step command receiver.
    Handles HIGHER (increase discharge / reduce charge) and LOWER (decrease discharge /
    increase charge) instructions sent by the grid client with cause ACTIVATION.

Callbacks:
  - on_step_command:      triggered when the grid sends a charge/discharge regulation command
  - before_auto_transmit: updates the power measurement before each periodic report
  - before_read:          updates the power measurement before responding to a client poll

Protocol details:
  - Transport:      TCP/IP, default port 2404
  - Common address: 47 (identifies this charger station on the network)

Intended extensions:
  - Real BMS integration (SoC, temperature, min/max discharge limits)
  - Enforcement of battery protection limits within on_step_command (refuse commands
    that would discharge below a configured SoC floor, e.g. 20%)
  - Additional monitoring points (SoC %, battery temp, compensation energy counter)
  - Support for direct setpoint commands (C_SE_NC_1)

Usage:
  python >= 3.7, < 3.13

  python iecc104_server.py

  Start this before the client. Uncomment the debug line at the bottom for verbose logging.
"""

import os

os.environ["PYTHONUNBUFFERED"] = "1"

import c104
import random
import asyncio

from config import Config


# ------------------------------------------------------------
# 			        Functions
# ------------------------------------------------------------


def on_step_command(
        point: c104.Point, previous_info: c104.Information, message: c104.IncomingMessage
        ) -> c104.ResponseState:
    """handle incoming regulating step command"""
    print(
        "{0} STEP COMMAND on IOA: {1}, message: {2}, previous: {3}, current: {4}".format(
            point.type, point.io_address, message, previous_info, point.info
            )
        )

    if point.value == c104.Step.LOWER:
        # do something
        print( "GOING LOWER WITH CHARGING" )
        return c104.ResponseState.SUCCESS

    if point.value == c104.Step.HIGHER:
        # do something
        print( "GOING HIGHER WITH CHARGING" )
        return c104.ResponseState.SUCCESS

    return c104.ResponseState.FAILURE


def before_auto_transmit( point: c104.Point ) -> None:
    """update point value before transmission"""
    if point.io_address == Config.METER_VALUES:
        point.value = random.uniform( 0, 20 )
    elif point.io_address == Config.SOC_VAL:
        point.value = random.uniform( 0, 100 )
    elif point.io_address == Config.READ_TEMP:
        point.value = random.uniform( 20, 60 )
    print(
        "{0} BEFORE AUTOMATIC REPORT on IOA: {1} VALUE: {2}".format(
            point.type, point.io_address, point.value
            )
        )


def before_read( point: c104.Point ) -> None:
    """update point value before transmission"""
    # replace the random value with the actual meter values
    if point.io_address == Config.METER_VALUES:
        point.value = random.uniform( 0, 20 )
        print(
            "{0} BEFORE READ or INTERROGATION on IOA: {1} VALUE: {2}".format(
                point.type, point.io_address, point.value
                )
            )
    elif point.io_address == Config.SOC_VAL:
        point.value = random.uniform( 0, 100 )
    elif point.io_address == Config.READ_TEMP:
        point.value = random.uniform( 20, 60 )


async def run_iec104_server():
    # server and station preparation
    server = c104.Server()
    station = server.add_station( common_address=47 )

    # create monitoring point to read data from
    point_meter = station.add_point(
        io_address=Config.METER_VALUES, type=c104.Type.M_ME_NC_1, report_ms=2000
        )
    #    point_meter.on_before_auto_transmit(callable=before_auto_transmit)
    point_meter.on_before_read( callable=before_read )

    # create SoC monitoring point
    point_soc = station.add_point(
        io_address=Config.SOC_VAL, type=c104.Type.M_ME_NC_1, report_ms=1000
        )
    point_soc.on_before_auto_transmit( callable=before_auto_transmit )
    point_soc.on_before_read( callable=before_read )

    # create Temp monitoring point
    point_temp = station.add_point(
        io_address=Config.READ_TEMP, type=c104.Type.M_ME_NC_1, report_ms=1000
        )
    point_temp.on_before_auto_transmit( callable=before_auto_transmit )
    point_temp.on_before_read( callable=before_read )

    # create command point to write commands to
    command = station.add_point( io_address=Config.CHARGE_CMD, type=c104.Type.C_RC_TA_1 )
    command.on_receive( callable=on_step_command )

    # start
    server.start()

    while not server.has_active_connections:
        print( "Waiting for connection" )
        await asyncio.sleep( 1 )

    await asyncio.sleep( 1 )

    c = 0
    while server.has_open_connections and c < 30:
        # c += 1
        print( "Keep alive until disconnected" )
        await asyncio.sleep( 1 )


if __name__ == "__main__":
    # c104.set_debug_mode(c104.Debug.Server | c104.Debug.Point | c104.Debug.Callback)
    main()
