from dataclasses import dataclass


@dataclass( frozen=True )
class Config:
    # --------------------------------------------------------------
    # 		       Points and Commands for IEC104
    # --------------------------------------------------------------
    METER_VALUES = 11
    SOC_VAL = 13
    READ_TEMP = 14
    CHARGE_CMD = 12
    EV_VOLTAGE = 15
    EV_CURRENT = 16
    ISO_LOOP_MS = 17
    # --------------------------------------------------------------
    #                 Network Settings IEC104
    # --------------------------------------------------------------
    IP_ADDRESS = "10.42.0.23"  # check charger Pi address and fill in here
    PORT = 2404              # IEC 104 standard port (plaintext)
    PORT_TLS = 19998         # IEC 62351-3 registered port for IEC 104 over TLS
    COMMON_ADDRESS = 47  # common address of IEC104 server/client

    OCPP_SERVER = "10.42.0.1:9000"  # IP:port of the grid Pi CSMS

    # --------------------------------------------------------------
    #      IEC 62351-3 — TLS for IEC 60870-5-104
    # --------------------------------------------------------------
    # Run create_iec104_certs.sh once to populate certs/iec104/.
    # Copy the full certs/iec104/ directory to both Pis.
    # Server = charger Pi (controlled station); client = grid Pi (controlling station).
    IEC104_CA_CERT     = "certs/iec104/ca.crt"     # trusted by both Pis
    IEC104_SERVER_CERT = "certs/iec104/server.crt"  # charger Pi server identity
    IEC104_SERVER_KEY  = "certs/iec104/server.key"  # charger Pi server private key
    IEC104_CLIENT_CERT = "certs/iec104/client.crt"  # grid Pi client identity
    IEC104_CLIENT_KEY  = "certs/iec104/client.key"  # grid Pi client private key

    # --------------------------------------------------------------
    #      OCPP Security Profile 3 — mutual TLS (mTLS)
    # --------------------------------------------------------------
    # Run create_ocpp_certs.sh once to populate certs/ocpp/.
    # Copy the full certs/ocpp/ directory to both Pis.
    OCPP_CA_CERT   = "certs/ocpp/ca.crt"    # trusted by both sides
    OCPP_CSMS_CERT = "certs/ocpp/csms.crt"  # grid Pi server identity
    OCPP_CSMS_KEY  = "certs/ocpp/csms.key"  # grid Pi server private key
    OCPP_CP_CERT   = "certs/ocpp/cp.crt"    # charger Pi client identity
    OCPP_CP_KEY    = "certs/ocpp/cp.key"    # charger Pi client private key

    # --------------------------------------------------------------
    #      Voltage Setup for PandaPower power flow simulation
    # --------------------------------------------------------------
    # primary Voltage in kV
    V_PRIMARY = 10.0
    # secondary Voltage in kV
    V_SECONDARY = 0.4
    # trafo type according to PandaPower
    TRAFO_TYPE = "0.4 MVA 10/0.4 kV"
    # line type according to PandaPower
    LINE_TYPE = "NA2XS2Y 1x185 RM/25 6/10 kV"  # "NAYY 4x120 SE"
    LINE_LENGTH = 0.5  # in km
    # NA2XS2Y 1x185 RM/25 6/10 kV
    LOAD_MVAR = 0.05
    LOAD_MW = 0.1
