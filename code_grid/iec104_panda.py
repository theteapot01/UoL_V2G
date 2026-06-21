import asyncio
import datetime
import os
import time

import c104
import pandapower as pp

from code_grid.grid_state import grid_state
from config import Config


def _build_tls() -> c104.TransportSecurity:
    """
    IEC 62351-3: TLS for the IEC 104 controlling station (client side).
    Uses cert pinning (only_known=True) — pins the charger Pi's server cert
    explicitly rather than relying on CA-chain validation, which has known
    mbedTLS/OpenSSL compatibility quirks.
    """
    tls = c104.TransportSecurity(validate=True, only_known=True)
    tls.set_ca_certificate(cert=Config.IEC104_CA_CERT)
    tls.set_certificate(cert=Config.IEC104_CLIENT_CERT, key=Config.IEC104_CLIENT_KEY)
    tls.add_allowed_remote_certificate(cert=Config.IEC104_SERVER_CERT)
    return tls

os.environ["PYTHONUNBUFFERED"] = "1"

net = pp.create_empty_network()

# --------------------------------------------------------------
#                   Net and Bus Setup
# --------------------------------------------------------------

b1 = pp.create_bus( net, vn_kv=Config.V_PRIMARY, name="Bus 1" )
b2 = pp.create_bus( net, vn_kv=Config.V_SECONDARY, name="Bus 2" )
b3 = pp.create_bus( net, vn_kv=Config.V_SECONDARY, name="Bus 3" )

pp.create_ext_grid( net, bus=b1, vm_pu=0.98, name="Grid Connection" )
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

# ── Control thresholds ────────────────────────────────────────────────────
# Values are % of rated equipment capacity for the 0.4 MVA transformer and
# NA2XS2Y line configured above.  Adjust here to retune the controller.
TRAFO_STRESS_PCT     = 80.0   # trafo above this → grid stressed, immediate HIGHER
LINE_STRESS_PCT      = 90.0   # line above this  → grid stressed, immediate HIGHER
VOLTAGE_MIN_PU       = 0.95   # bus 2 below this → grid stressed, immediate HIGHER
TRAFO_TARGET_PCT     = 70.0   # desired trafo operating point (centre of dead zone)
TRAFO_HYSTERESIS_PCT =  3.0   # ± half-width of dead zone around target
# Resulting trafo bands:
#   trafo < TARGET − HYSTERESIS  (< 67 %)  → contributes to LOWER
#   trafo > TARGET + HYSTERESIS  (> 73 %)  → contributes to HIGHER
#   67 – 73 %                               → contributes to HOLD
LINE_TARGET_PCT      = 80.0   # desired line operating point (centre of dead zone)
LINE_HYSTERESIS_PCT  =  5.0   # ± half-width of dead zone around target
# Resulting line bands:
#   line < LINE_TARGET − LINE_HYSTERESIS (< 75 %)  → contributes to LOWER
#   line > LINE_TARGET + LINE_HYSTERESIS (> 85 %)  → contributes to HIGHER
#   75 – 85 %                                        → contributes to HOLD
# LOWER is only sent when BOTH trafo and line are below their lower thresholds.
# HIGHER is sent when EITHER trafo or line exceeds its upper threshold.
# This causes the power to ramp up until the binding constraint (trafo or line)
# enters its dead zone, then hold there rather than oscillate.


# --------------------------------------------------------------
#                   Functions
# --------------------------------------------------------------

def _minutes_to_departure(departure_str: str):
    """Return minutes until the user's departure time, or None if not set."""
    if not departure_str:
        return None
    try:
        h, m = map(int, departure_str.split(":"))
        now = datetime.datetime.now()
        dep = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if dep <= now:
            dep += datetime.timedelta(days=1)
        return (dep - now).total_seconds() / 60.0
    except (ValueError, AttributeError):
        return None


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
    client = c104.Client(transport_security=_build_tls())
    connection = client.add_connection(
        ip=Config.IP_ADDRESS, port=Config.PORT_TLS, init=c104.Init.ALL
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
    point_voltage = station.add_point( io_address=Config.EV_VOLTAGE, type=c104.Type.M_ME_NC_1 )
    point_current = station.add_point( io_address=Config.EV_CURRENT, type=c104.Type.M_ME_NC_1 )
    point_loop_ms = station.add_point( io_address=Config.ISO_LOOP_MS, type=c104.Type.M_ME_NC_1 )

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
    _pending_cmd   = "LOWER"
    _pending_src   = "auto"
    _soc_valid     = False   # True once a trusted (>0) SoC reading has been received
    _prev_auto_cmd = "LOWER" # last auto-logic decision (for debounce)
    _auto_streak   = 0       # consecutive cycles with the same auto decision

    while connection.state == c104.ConnectionState.OPEN:
        now = time.time()

        # ── 1 s read cycle: meter → pandapower → stage command ──────────────
        if now - last_read >= 1:
            last_read = now
            t_cycle = time.time()

            t0 = time.time()
            read_ok = await loop.run_in_executor( None, point_meter.read )
            grid_state.iec104_read_ms = (time.time() - t0) * 1000

            if read_ok:
                grid_state.iec104.power_kw  = point_meter.value
                grid_state.iec104.timestamp = time.time()

                t_pp = time.time()
                net.load.at[0, "p_mw"] = point_meter.value / 1000
                pp.runpp( net )
                grid_state.pandapower_ms = (time.time() - t_pp) * 1000

                vm_pu_b2      = net.res_bus.at[b2, "vm_pu"]
                trafo_loading = net.res_trafo.at[0, "loading_percent"]
                line_loading  = net.res_line.at[0, "loading_percent"]

                voltages.append( vm_pu_b2 )
                trafo_loadings.append( trafo_loading )

                idle = abs(point_meter.value) < 0.5
                grid_state.charger_idle           = idle
                grid_state.grid.bus2_voltage_pu   = vm_pu_b2
                grid_state.grid.trafo_loading_pct = 0.0 if idle else trafo_loading
                grid_state.grid.line_loading_pct  = 0.0 if idle else line_loading

                soc = point_soc.value  # snapshot once; used in decision and print

                # Manual override takes precedence over auto logic
                if not grid_state.auto_control and grid_state.manual_override:
                    step = (
                        c104.Step.HIGHER
                        if grid_state.manual_override == "HIGHER"
                        else c104.Step.LOWER
                    )
                    command.value  = step
                    _pending_cmd   = grid_state.manual_override
                    _pending_src   = "manual"
                    _auto_streak   = 0
                    _prev_auto_cmd = _pending_cmd
                else:
                    # Auto: reduce charge when grid is stressed or battery is full;
                    # increase charge when there is spare capacity and battery needs it.
                    # SoC conditions are gated on _soc_valid to avoid acting on stale
                    # startup values; grid-health conditions always apply immediately.
                    prefs = grid_state.prefs
                    mins  = _minutes_to_departure(prefs.departure_time)
                    departure_priority = (
                        mins is not None
                        and mins < 60
                        and _soc_valid
                        and soc < prefs.target_soc_pct
                    )

                    _high      = TRAFO_TARGET_PCT + TRAFO_HYSTERESIS_PCT
                    _low       = TRAFO_TARGET_PCT - TRAFO_HYSTERESIS_PCT
                    _line_high = LINE_TARGET_PCT  + LINE_HYSTERESIS_PCT
                    _line_low  = LINE_TARGET_PCT  - LINE_HYSTERESIS_PCT

                    if departure_priority:
                        auto_cmd = "LOWER"
                    elif (trafo_loading > TRAFO_STRESS_PCT
                          or line_loading > LINE_STRESS_PCT
                          or vm_pu_b2 < VOLTAGE_MIN_PU):
                        auto_cmd = "HIGHER"
                    elif _soc_valid and soc >= prefs.max_soc_pct:
                        # Battery at user max — ramp charge to idle; hold there.
                        # V2G is only triggered by the grid-stress emergency above,
                        # not automatically whenever the battery is full.
                        auto_cmd = "HIGHER" if point_meter.value > 1.0 else "HOLD"
                    elif _soc_valid and soc < prefs.min_soc_pct:
                        auto_cmd = "LOWER"
                    elif trafo_loading > _high or line_loading > _line_high:
                        # Either constraint approaching capacity — reduce power.
                        auto_cmd = "HIGHER"
                    elif trafo_loading < _low and line_loading < _line_low:
                        # Both constraints have spare capacity — increase power.
                        auto_cmd = "LOWER"
                    else:
                        # At least one constraint is in its dead zone — hold.
                        auto_cmd = "HOLD"

                    # HOLD applies immediately (safe default).
                    # HIGHER/LOWER require 2 consecutive cycles before staging so
                    # a single stale reading cannot flip the command.
                    if auto_cmd == "HOLD":
                        _pending_cmd   = "HOLD"
                        _auto_streak   = 0
                        _prev_auto_cmd = "HOLD"
                    else:
                        if auto_cmd == _prev_auto_cmd:
                            _auto_streak += 1
                        else:
                            _auto_streak   = 1
                            _prev_auto_cmd = auto_cmd

                        if _auto_streak >= 2 or auto_cmd == _pending_cmd:
                            command.value = c104.Step.HIGHER if auto_cmd == "HIGHER" else c104.Step.LOWER
                            _pending_cmd  = auto_cmd
                    _pending_src = "auto"

                grid_state.cycle_ms = (time.time() - t_cycle) * 1000

                print(
                    f"Load: {point_meter.value:.2f} kW | "
                    f"Bus 2: {vm_pu_b2:.4f} pu | "
                    f"Trafo: {trafo_loading:.1f}% | "
                    f"Line: {line_loading:.1f}% | "
                    f"SoC: {soc:.1f}%{'✓' if _soc_valid else '?'} | "
                    f"Cmd: {_pending_cmd} ({_pending_src}) | "
                    f"Cycle: {grid_state.cycle_ms:.0f} ms"
                )
            else:
                print( "-> IEC104 READ FAILURE" )

        # ── 4 s transmit cycle: send command, refresh SoC + temp ────────────
        if now - last_transmit >= 4:
            last_transmit = now

            if _pending_cmd == "HOLD":
                print( "-> HOLD (setpoint stable, no command sent)" )
            else:
                t_tx = time.time()
                if await loop.run_in_executor(
                        None, lambda: command.transmit( cause=c104.Cot.ACTIVATION )
                        ):
                    grid_state.transmit_ms = (time.time() - t_tx) * 1000
                    grid_state.log_command( _pending_cmd, _pending_src )
                    print(
                        f"-> TRANSMIT OK  cmd={_pending_cmd} src={_pending_src} "
                        f"tx={grid_state.transmit_ms:.0f} ms"
                    )
                else:
                    print( "-> TRANSMIT FAILURE" )

            if await loop.run_in_executor( None, point_soc.read ):
                grid_state.iec104.soc_percent = point_soc.value
                if not _soc_valid and point_soc.value > 0:
                    _soc_valid = True
                print( f"-> SOC {point_soc.value:.1f}%" )
            else:
                print( "-> SOC READ FAILURE" )

            if await loop.run_in_executor( None, point_temp.read ):
                grid_state.iec104.temp_c = point_temp.value
            else:
                print( "-> TEMP READ FAILURE" )

            if await loop.run_in_executor( None, point_voltage.read ):
                grid_state.iec104.voltage_v = point_voltage.value
                grid_state.iec104.iso_timestamp = time.time()
            else:
                print( "-> VOLTAGE READ FAILURE" )

            if await loop.run_in_executor( None, point_current.read ):
                grid_state.iec104.current_a = point_current.value
            else:
                print( "-> CURRENT READ FAILURE" )

            if await loop.run_in_executor( None, point_loop_ms.read ):
                grid_state.iec104.iso_loop_ms = point_loop_ms.value
            else:
                print( "-> ISO LOOP MS READ FAILURE" )

        await asyncio.sleep( 0.1 )


if __name__ == "__main__":
    # c104.set_debug_mode(
    #     c104.Debug.Client
    #     | c104.Debug.Connection
    #     | c104.Debug.Point
    #     | c104.Debug.Callback
    #     )
    asyncio.run( run_iec104_client() )
