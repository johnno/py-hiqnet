"""HiQnet / Crown DCi constants."""

HIQNET_PORT  = 3804
HEADER_SIZE  = 24
PROTO_BYTE   = 0x19   # fixed byte at header offset 1

# HiQnet message type IDs
MSG_DISCOINFO     = 0x0000
MSG_MULTISET      = 0x0101   # MultiObjectParamSet (Crown→client, subscription push)
MSG_MULTIGET      = 0x0103
MSG_SUBSCRIBE_ALL = 0x010D
MSG_SUBSCRIBE_SV  = 0x0113

# HiQnet header flags
FLAG_INFO       = 0x0004   # informational / response

# HiQnet data types
TYPE_UBYTE   = 0x01
TYPE_FLOAT32 = 0x06

TYPE_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 8, 10: 8, 11: 8, 12: 4}

# Crown DCi 8300N object addresses (obj_dest ULONG in MultiObjectParamSet payload)
#   Format: vd(1) . subnet(1) . obj_hi(1) . obj_lo(1)
FADER_OBJ_BASE = 0x000F1600   # [15.22.X]  +channel(1-8) = 0x000F1601..08
METER_OBJ_BASE = 0x00101700   # [16.23.X]  +channel(1-8) = 0x00101701..08

# Crown DCi 8300N parameter IDs within the above objects
FADER_SV_ID = 3    # [15.22.X] svID=3 = output fader level (UBYTE)
MUTE_SV_ID  = 6    # [15.22.X] svID=6 = mute enable         (UBYTE, 0=unmuted)
METER_SV_ID = 3    # [16.23.X] pid=3  = Voltage RMS Level Meter (UBYTE)
NAME_PID    = 25   # [16.23.X] pid=25 = channel name (STRING UTF-16BE)

# Calibration: UBYTE → dB  (same encoding for fader and meter)
#   dB = (raw - 200) × 0.5
#   raw=0   → -100 dB (minimum)
#   raw=200 →   0 dB  (unity / reference)
#   raw=212 →  +6 dB  (Crown DCi maximum)
#   raw=52  → -74 dB  (hardware noise floor — "silence")
CAL_ZERO_RAW       = 200
CAL_STEP_DB        = 0.5
METER_NOISE_FLOOR  = (52 - CAL_ZERO_RAW) * CAL_STEP_DB   # -74.0 dB

# Subscription sub-type
SUB_TYPE_ALL        = 0x00   # all params including sensor/meter
SUB_TYPE_NON_SENSOR = 0x01   # non-sensor params only (faders, mutes)
