"""
iec104_client.py
================
Grid operator client for V2G (Vehicle-to-Grid) communication over IEC 60870-5-104.

This module acts as the controlling station (client) in the IEC 104 protocol, representing
the grid operator's SCADA system. It connects to a charger/EV unit running the corresponding
IEC 104 server and performs two core operations:

  1. Read (poll) live telemetry from the charger:
       - IOA 11 (M_ME_NC_1): current power flow measurement in watts

  2. Transmit step commands to regulate energy flow:
       - IOA 12 (C_RC_TA_1): regulating step command (HIGHER / LOWER) to increase
         or decrease charge/discharge rate, sent with cause-of-transmission ACTIVATION

IOA ... Information Object Address

Protocol details:
  - Transport:      TCP/IP, default port 2404
  - Common address: 47 (identifies the charger station)
  - Init mode:      ALL (sends general interrogation + clock sync on connect)

Intended extensions:
  - Power flow decision logic (grid frequency, renewable surplus detection)
  - Additional monitoring points (SoC, battery temperature, discharge limits)
  - Direct watt setpoint commands (C_SE_NC_1) for finer power control
  - Compensation/tariff tracking when vehicle energy is fed back to grid

Usage:
  python >= 3.7, < 3.13

  python iec104_client.py

  Uncomment the debug line at the bottom to enable verbose IEC 104 protocol logging.
"""

import c104
import random
import time

# --------------------------------------------------------------
#                   Network Settings
# --------------------------------------------------------------
ip_address = "10.42.0.23"  # check charger Pi assigned address and fill in here
port = 2404  # for now leave port as is, if it overlaps with other functionallity then change it

# --------------------------------------------------------------
#		    Points and Commands
# --------------------------------------------------------------
meterValues = 11
chargeCMD = 12
socVal = 13
readTemp = 14

# --------------------------------------------------------------
#                   Functions
# --------------------------------------------------------------
def con_on_unexpected_message(
    connection: c104.Connection, message: c104.IncomingMessage, cause: c104.Umc
) -> None:
    if cause == c104.Umc.MISMATCHED_TYPE_ID:
        station = connection.get_station(message.common_address)
        if station:
            point = station.get_point(message.io_address)
            if point:
                print(
                    "CL] <-in-- CONFLICT | SERVER CA {0} reports IOA {1} type as {2}, but is already registered as {3}".format(
                        message.common_address,
                        message.io_address,
                        message.type,
                        point.type,
                    )
                )
                return
    print(
        "CL] <-in-- REJECTED | {1} from SERVER CA {0}".format(
            message.common_address, cause
        )
    )


def main():
    # client, connection and station preparation
    client = c104.Client()
    connection = client.add_connection(ip=ip_address, port=port, init=c104.Init.ALL)
    connection.on_unexpected_message(callable=con_on_unexpected_message)
    station = connection.add_station(common_address=47)

    # monitoring point preparation
    point_meter = station.add_point(io_address=meterValues, type=c104.Type.M_ME_NC_1)
    point_soc = station.add_point(io_address=socVal, type=c104.Type.M_ME_NC_1)
    point_temp = station.add_point(io_address=readTemp, type=c104.Type.M_ME_NC_1)

    # command point preparation
    command = station.add_point(io_address=chargeCMD, type=c104.Type.C_RC_TA_1)
    command.value = c104.Step.HIGHER

    # start
    client.start()

    while connection.state != c104.ConnectionState.OPEN:
        print(
            "Waiting for connection to {0}:{1}".format(connection.ip, connection.port)
        )
        time.sleep(1)

    print(f"-> AFTER INIT {point_meter.value}")

    print("read")
    print("read")
    print("read")
    # Read the data point from the charger
    if point_meter.read():
        print(f"-> SUCCESSFUL METER READING {point_meter.value}")
    else:
        print("-> FAILURE")

    time.sleep(3)

    print("transmit")
    print("transmit")
    print("transmit")
    # Write to command point with either HIGHER or LOWER for changing the charging levels
    if command.transmit(cause=c104.Cot.ACTIVATION):
        print("-> SUCCESSFUL TRANSMIT")
    else:
        print("-> FAILURE")

    time.sleep(3)

    if point_soc.read():
        print(f"-> SUCCESSFUL SOC READING {point_soc.value}")
    else:
        print("-> FAILURE")

    time.sleep(3)

    if point_temp.read():
        print(f"-> SUCCESSFUL TEMP READING {point_temp.value}")
    else:
        print("-> FAILURE")

    print("exit")
    print("exit")
    print("exit")


if __name__ == "__main__":
    c104.set_debug_mode(
        c104.Debug.Client
        | c104.Debug.Connection
        | c104.Debug.Point
        | c104.Debug.Callback
    )
    main()
