from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # --------------------------------------------------------------
    # 		    Points and Commands
    # --------------------------------------------------------------
    METER_VALUES = 11
    SOC_VAL = 13
    READ_TEMP = 14
    CHARGE_CMD = 12
    # --------------------------------------------------------------
    #                   Network Settings
    # --------------------------------------------------------------
    IP_ADDRESS = "10.42.0.23"  # check Pi self-assigned address and fill in here
    PORT = 2404  # for now leave port as is, if it overlaps with other functionality then change it
    COMMON_ADDRESS = 47

    # --------------------------------------------------------------
    #                   Voltage Setup
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