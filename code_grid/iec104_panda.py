import asyncio
import os
import time

import c104
import pandapower as pp

from config import Config

os.environ["PYTHONUNBUFFERED"] = "1"

net = pp.create_empty_network()

# --------------------------------------------------------------
#                   Net and Bus Setup
# --------------------------------------------------------------

b1 = pp.create_bus( net, vn_kv=Config.V_PRIMARY, name="Bus 1" )
b2 = pp.create_bus( net, vn_kv=Config.V_SECONDARY, name="Bus 2" )
b3 = pp.create_bus( net, vn_kv=Config.V_SECONDARY, name="Bus 3" )

pp.create_ext_grid( net, bus=b1, vm_pu=1.02, name="Grid Connection" )
pp.create_load( net, bus=b3, p_mw=Config.LOAD_MW, q_mvar=Config.LOAD_MVAR, name="Load" )

pp.create_transformer( net, hv_bus=b1, lv_bus=b2, std_type=Config.TRAFO_TYPE, name="Trafo" )

pp.create_line(
    net,
    from_bus=b2,
    to_bus=b3,
    length_km=Config.LINE_LENGTH,
    std_type=Config.LINE_TYPE,
    name="line1",
    )

# Load values for testing the calculation
load_values = [-0.010, -0.015, -0.020, 0.05, 0.010, 0.025, 0.030, 0.035]

voltages = []
trafo_loadings = []


# --------------------------------------------------------------
#                   Functions
# --------------------------------------------------------------
def con_on_unexpected_message(
        connection: c104.Connection, message: c104.IncomingMessage, cause: c104.Umc
        ) -> None:
    if cause == c104.Umc.MISMATCHED_TYPE_ID:
        station = connection.get_station( message.common_address )
        if station:
            point = station.get_point( message.io_address )
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


async def run_iec104_client():
    # client, connection and station preparation
    client = c104.Client()
    connection = client.add_connection(
        ip=Config.IP_ADDRESS, port=Config.PORT, init=c104.Init.ALL
        )
    connection.on_unexpected_message( callable=con_on_unexpected_message )
    station = connection.add_station( common_address=Config.COMMON_ADDRESS )

    # monitoring point preparation
    point_meter = station.add_point(
        io_address=Config.METER_VALUES, type=c104.Type.M_ME_NC_1
        )
    point_soc = station.add_point( io_address=Config.SOC_VAL, type=c104.Type.M_ME_NC_1 )
    point_temp = station.add_point(
        io_address=Config.READ_TEMP, type=c104.Type.M_ME_NC_1
        )

    # command point preparation
    command = station.add_point( io_address=Config.CHARGE_CMD, type=c104.Type.C_RC_TA_1 )
    command.value = c104.Step.HIGHER

    # start
    client.start()

    loop = asyncio.get_event_loop()

    while connection.state != c104.ConnectionState.OPEN:
        print(
            "Waiting for connection to {0}:{1}".format( connection.ip, connection.port )
            )
        await asyncio.sleep( 1 )

    print( f"-> AFTER INIT {point_meter.value}" )

    last_read = 0
    last_transmit = 0

    while connection.state == c104.ConnectionState.OPEN:
        now = time.time()
        # Read the data point from the charger
        if now - last_read >= 1:
            last_read = now
            # if point_meter.read() and point_meter.value !=0:
            if await loop.run_in_executor( None, point_meter.read ):
                net.load.at[0, "p_mw"] = point_meter.value / 1000
                pp.runpp( net )

                vm_pu_b2 = net.res_bus.at[b2, "vm_pu"]
                trafo_loading = net.res_trafo.at[0, "loading_percent"]
                line_loading = net.res_line.at[0, "loading_percent"]

                voltages.append( vm_pu_b2 )
                trafo_loadings.append( trafo_loading )

                print(
                    f"Load: {point_meter.value:.2f} kW | Bus 2 Voltage: {vm_pu_b2:.4f} pu | Trafo Loading: {trafo_loading:.1f}% | Line {line_loading:.1f}%"
                    )
                print( f"-> SUCCESSFUL METER READING {point_meter.value}" )
            else:
                print( "-> FAILURE" )

        if now - last_transmit >= 4:
            last_transmit = now
            # Write to command point with either HIGHER or LOWER for changing the charging levels
            if await loop.run_in_executor(
                    None, lambda: command.transmit( cause=c104.Cot.ACTIVATION )
                    ):
                print( "-> SUCCESSFUL TRANSMIT" )
            else:
                print( "-> FAILURE" )

            if await loop.run_in_executor( None, point_soc.read ):
                print( f"-> SUCCESSFUL SOC READING {point_soc.value}" )
            else:
                print( "-> FAILURE" )

            if await loop.run_in_executor( None, point_temp.read ):
                print( f"-> SUCCESSFUL TEMP READING {point_temp.value}" )
            else:
                print( "-> FAILURE" )

        await asyncio.sleep( 0.1 )


if __name__ == "__main__":
    c104.set_debug_mode(
        c104.Debug.Client
        | c104.Debug.Connection
        | c104.Debug.Point
        | c104.Debug.Callback
        )
    asyncio.run( run_iec104_client() )
