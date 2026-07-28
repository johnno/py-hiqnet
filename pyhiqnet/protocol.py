"""Low-level HiQnet protocol: message building and parsing.

Implements the Crown DCi 8300N HiQnet dialect discovered via MotionControl
traffic capture (July 2026).  Key differences from the public spec:
  - Header is 24 bytes (byte 1 = 0x19, total_len at bytes 4-5)
  - Address format: NODE(2BE) + VD(1) + SUBNET(1) + OBJ(2BE)
  - Crown uses a proprietary MultiObjectParamSet payload format
"""

from __future__ import annotations

import math
import struct
from typing import Any

from .const import (
    FLAG_INFO,
    HEADER_SIZE,
    MSG_DISCOINFO,
    PROTO_BYTE,
    TYPE_SIZES,
)


# ── Address helpers ──────────────────────────────────────────────────────────

def make_addr(node: int, vd: int = 0, subnet: int = 0, obj: int = 0) -> bytes:
    """Build a 6-byte HiQnet address: NODE(2BE)+VD(1)+SUBNET(1)+OBJ(2BE)."""
    return struct.pack(">HBBH", node, vd, subnet, obj)


def parse_addr(raw: bytes) -> tuple[int, int, int, int]:
    """Parse 6-byte address → (node, vd, subnet, obj)."""
    return struct.unpack(">HBBH", raw)


# ── Message builder ──────────────────────────────────────────────────────────

def build_msg(
    msg_type: int,
    payload: bytes,
    src: bytes,
    dst: bytes,
    flags: int = 0,
    session: int = 0,
    hop: int = 5,
) -> bytes:
    """Build a complete HiQnet message (24-byte header + payload)."""
    total = HEADER_SIZE + len(payload)
    hdr = struct.pack(
        ">BBHH6s6sHHBB",
        0x02,       # version
        PROTO_BYTE,
        flags,
        total,
        src,
        dst,
        msg_type,
        session,
        hop,
        0,          # padding
    )
    assert len(hdr) == HEADER_SIZE
    return hdr + payload


# ── DiscoInfo ────────────────────────────────────────────────────────────────

def make_disco_payload(our_node: int, our_mac: bytes, our_ip: bytes) -> bytes:
    """Build the 48-byte DiscoInfo payload.

    Serial Number BLOCK: UWORD(16) + 10 zeros + 6-byte MAC = 18 bytes.
    This is the correctly-sized payload that the Crown accepts for UDP routing.
    """
    return (
        struct.pack(">H", our_node) +       # node (2)
        bytes([1, 0]) +                      # vd + cost (2)
        struct.pack(">H", 0x0010) +          # Serial Number BLOCK size=16 (2)
        bytes(10) +                          # 10 zeros (10)
        our_mac +                            # MAC (6)  → serial block = 18 bytes
        struct.pack(">I", 0x0000FFFF) +      # Max Message Size (4)
        struct.pack(">H", 10000) +           # Keep Alive Period ms (2)
        bytes([1]) +                         # Network ID = 1 TCP/IP (1)
        our_mac +                            # Net Info: MAC (6)
        bytes([0]) +                         # DHCP = 0 static (1)
        our_ip +                             # IP (4)
        bytes([0xFF, 0xFF, 0xFF, 0x00]) +    # Subnet (4)
        bytes(4)                             # Gateway (4)
    )  # total = 48 bytes


def build_discoinfo(
    our_node: int,
    crown_node: int,
    our_mac: bytes,
    our_ip: bytes,
    info: bool = False,
) -> bytes:
    """Build a DiscoInfo message.  info=True sets FLAG_INFO (it's a response)."""
    pl = make_disco_payload(our_node, our_mac, our_ip)
    return build_msg(
        MSG_DISCOINFO,
        pl,
        make_addr(our_node, 1, 0),
        make_addr(crown_node),
        flags=FLAG_INFO if info else 0,
    )


# ── Stream parser ────────────────────────────────────────────────────────────

def parse_hiqnet_stream(data: bytes) -> list[dict[str, Any]]:
    """Walk a raw byte buffer and extract valid HiQnet messages.

    Uses the total_length field for framing.  Each returned dict has:
      type, flags, src (6 bytes), dst (6 bytes), payload (bytes), is_info (bool)
    """
    messages: list[dict[str, Any]] = []
    pos = 0
    while pos + HEADER_SIZE <= len(data):
        if data[pos] != 0x02 or data[pos + 1] != PROTO_BYTE:
            pos += 1
            continue
        total_len = struct.unpack_from(">H", data, pos + 4)[0]
        if total_len < HEADER_SIZE or pos + total_len > len(data):
            pos += 1
            continue
        raw = data[pos : pos + total_len]
        flags    = struct.unpack_from(">H", raw, 2)[0]
        msg_type = struct.unpack_from(">H", raw, 18)[0]
        messages.append(
            {
                "type":    msg_type,
                "flags":   flags,
                "src":     raw[6:12],
                "dst":     raw[12:18],
                "payload": raw[HEADER_SIZE:],
                "is_info": bool(flags & FLAG_INFO),
            }
        )
        pos += total_len
    return messages


# ── MultiObjectParamSet payload parser ───────────────────────────────────────

def parse_multiobj_payload(
    payload: bytes,
) -> dict[int, dict[int, tuple[int, bytes]]]:
    """Parse the Crown's proprietary MultiObjectParamSet (0x0101) payload.

    Crown format (confirmed from pcap, 2026-07-23):
      UWORD: handle/sequence
      UBYTE: num_objects
      For each object:
        ULONG: obj_dest  (vd.subnet.obj_hi.obj_lo)
        UWORD: num_params
        For each param: UWORD(id) + UBYTE(type) + N bytes(value)

    Returns: {obj_dest_int: {param_id: (data_type, value_bytes)}}
    """
    if len(payload) < 3:
        return {}
    pos = 2  # skip 2-byte handle
    num_objects = payload[pos]
    pos += 1
    objects: dict[int, dict[int, tuple[int, bytes]]] = {}
    for _ in range(num_objects):
        if pos + 6 > len(payload):
            break
        obj_dest   = struct.unpack_from(">I", payload, pos)[0]
        pos += 4
        num_params = struct.unpack_from(">H", payload, pos)[0]
        pos += 2
        params: dict[int, tuple[int, bytes]] = {}
        ok = True
        for _ in range(num_params):
            if pos + 3 > len(payload):
                ok = False
                break
            param_id  = struct.unpack_from(">H", payload, pos)[0]
            data_type = payload[pos + 2]
            pos += 3
            if data_type in (8, 9):  # BLOCK / STRING: length-prefixed
                if pos + 2 > len(payload):
                    ok = False
                    break
                sz = struct.unpack_from(">H", payload, pos)[0]
                pos += 2
            else:
                sz = TYPE_SIZES.get(data_type, None)
                if sz is None:
                    ok = False
                    break
            val = payload[pos : pos + sz] if pos + sz <= len(payload) else b""
            params[param_id] = (data_type, val)
            pos += sz
        objects[obj_dest] = params
        if not ok:
            break
    return objects


# ── Calibration helpers ──────────────────────────────────────────────────────

def ubyte_to_db(raw: int) -> float:
    """Convert Crown UBYTE level (fader or meter) to dB.
    dB = (raw - 200) × 0.5   →   raw=200=0 dB, raw=52≈-74 dB (noise floor).
    """
    return (raw - 200) * 0.5


def power_corrected_db(voltage_db: float, ohms: float, ref_ohms: float = 4.0) -> float:
    """Adjust a dBV reading for load impedance relative to ref_ohms.
    P = V²/Z, so power_dB = voltage_dB − 10·log10(Z/ref).
    """
    return voltage_db - 10 * math.log10(ohms / ref_ohms)


def decode_name(raw: bytes) -> str:
    """Decode a UTF-16BE channel name string, stripping the null terminator."""
    try:
        return raw.decode("utf-16-be").rstrip("\x00")
    except Exception:
        return ""
