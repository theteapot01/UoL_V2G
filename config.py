from dataclasses import dataclass


@dataclass(frozen=True)
class config:
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
    IP_ADDRESS = "10.42.0.23"  # check Pi self assigned address and fill in here
    PORT = 2404  # for now leave port as is, if it overlaps with other functionallity then change it
    COMMON_ADDRESS = 47