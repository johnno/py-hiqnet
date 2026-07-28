"""Async Crown DCi amplifier client.

Manages the HiQnet TCP + UDP connection to a Crown DCi amplifier and
maintains live channel state (fader level, mute, RMS meter).

Typical usage::

    from pyhiqnet.crown import CrownAmpClient

    async def on_update(channels):
        for ch in channels.values():
            print(ch)

    client = CrownAmpClient("192.168.1.127")
    client.add_listener(on_update)
    await client.async_connect()
    # … run your event loop …
    await client.async_disconnect()
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .const import (
    CAL_STEP_DB,
    CAL_ZERO_RAW,
    FADER_OBJ_BASE,
    FADER_SV_ID,
    HIQNET_PORT,
    METER_NOISE_FLOOR,
    METER_OBJ_BASE,
    METER_SV_ID,
    MSG_DISCOINFO,
    MSG_MULTISET,
    MSG_MULTIGET,
    MSG_SUBSCRIBE_ALL,
    MSG_SUBSCRIBE_SV,
    MUTE_SV_ID,
    NAME_PID,
    SUB_TYPE_ALL,
    TYPE_UBYTE,
)
from .protocol import (
    build_discoinfo,
    build_msg,
    decode_name,
    make_addr,
    parse_hiqnet_stream,
    parse_multiobj_payload,
    ubyte_to_db,
)

_LOGGER = logging.getLogger(__name__)

# Our HiQnet node ID (arbitrary; must not clash with any real device on network)
DEFAULT_OUR_NODE = 0xF54C
CROWN_DEFAULT_PORT = HIQNET_PORT
UDP_PORT = 3804


def _get_our_ip(host: str) -> bytes:
    """Determine the local IP used to reach *host*."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 3804))
        return socket.inet_aton(s.getsockname()[0])
    finally:
        s.close()


def _get_our_mac() -> bytes:
    """Return the primary network interface MAC address as 6 bytes."""
    mac_int = uuid.getnode()
    return mac_int.to_bytes(6, "big")


# ── Channel state ────────────────────────────────────────────────────────────

@dataclass
class CrownChannel:
    """Current state of one Crown DCi output channel."""

    number: int
    name: str = ""
    fader_raw: int = 200       # UBYTE 0-212, default = 0 dB
    muted: bool = False
    meter_raw: int = 52        # UBYTE, default = noise floor

    @property
    def fader_db(self) -> float:
        """Output fader level in dB.  Range ≈ -100..+6 dB."""
        return ubyte_to_db(self.fader_raw)

    @property
    def volume_level(self) -> float:
        """Fader position as 0.0–1.0 (maps -100 dB → 0.0, 0 dB → 1.0)."""
        return max(0.0, min(1.0, (self.fader_db + 100.0) / 100.0))

    @property
    def meter_db(self) -> float:
        """RMS output level in dB. Noise floor ≈ -74 dB."""
        return ubyte_to_db(self.meter_raw)

    @property
    def signal_present(self) -> bool:
        """True when audio signal is above the hardware noise floor."""
        return self.meter_db > METER_NOISE_FLOOR + 3  # 3 dB margin

    def __repr__(self) -> str:
        return (
            f"CrownChannel({self.number} {self.name!r}: "
            f"fader={self.fader_db:+.1f} dB, "
            f"{'MUTED' if self.muted else 'unmuted'}, "
            f"meter={self.meter_db:+.1f} dB)"
        )


# ── UDP DatagramProtocol ─────────────────────────────────────────────────────

class _UDPProtocol(asyncio.DatagramProtocol):
    """Receive Crown sensor pushes and DiscoInfo keepalives via UDP."""

    def __init__(self, client: "CrownAmpClient") -> None:
        self._client = client
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self.transport = transport
        _LOGGER.debug("UDP protocol connected")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < 20 or data[0] != 0x02 or data[1] != 0x19:
            return
        msg_type = struct.unpack_from(">H", data, 18)[0]
        src_node  = struct.unpack_from(">H", data, 6)[0]

        if msg_type == MSG_DISCOINFO and src_node == self._client.crown_node:
            # Respond with DiscoInfo(I) so Crown keeps us in its routing table
            if self.transport:
                self.transport.sendto(self._client._disco_i, (addr[0], UDP_PORT))
                _LOGGER.debug("Sent DiscoInfo(I) reply to %s", addr[0])

        elif msg_type == MSG_MULTISET:
            tl = struct.unpack_from(">H", data, 4)[0]
            payload = data[20:tl]
            self._client._process_multiobj(parse_multiobj_payload(payload))

    def error_received(self, exc: Exception) -> None:
        _LOGGER.warning("UDP error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.debug("UDP connection lost: %s", exc)


# ── Main client ──────────────────────────────────────────────────────────────

UpdateCallback = Callable[[dict[int, CrownChannel]], None]


class CrownAmpClient:
    """Async client for a Crown DCi amplifier via HiQnet.

    Manages:
      • TCP connection for subscription + fader/mute change notifications
      • UDP socket for live meter sensor pushes (~150 ms period)
      • Periodic DiscoInfo keepalives to maintain the HiQnet session

    Listeners receive the full :class:`CrownChannel` dict on any state change.
    """

    def __init__(
        self,
        host: str,
        port: int = CROWN_DEFAULT_PORT,
        crown_node: int = 0x206C,
        our_node: int = DEFAULT_OUR_NODE,
        our_mac: bytes | None = None,
        our_ip: bytes | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.crown_node = crown_node
        self._our_node = our_node
        self._our_mac = our_mac or _get_our_mac()
        self._our_ip  = our_ip  or _get_our_ip(host)

        self._channels: dict[int, CrownChannel] = {
            ch: CrownChannel(number=ch) for ch in range(1, 9)
        }
        self._listeners: list[UpdateCallback] = []
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._tasks: list[asyncio.Task] = []  # type: ignore[type-arg]

        # Pre-build the two DiscoInfo variants
        self._disco_q = build_discoinfo(our_node, crown_node, self._our_mac, self._our_ip, info=False)
        self._disco_i = build_discoinfo(our_node, crown_node, self._our_mac, self._our_ip, info=True)

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def channels(self) -> dict[int, CrownChannel]:
        """Read-only view of current channel states (channels 1-8)."""
        return dict(self._channels)

    def add_listener(self, callback: UpdateCallback) -> None:
        """Register *callback(channels)* to be called on any state change."""
        self._listeners.append(callback)

    def remove_listener(self, callback: UpdateCallback) -> None:
        self._listeners.discard(callback) if hasattr(self._listeners, "discard") else None
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    async def async_connect(self) -> None:
        """Connect to the Crown amp, subscribe, and start background tasks."""
        _LOGGER.info("Connecting to Crown at %s:%d", self.host, self.port)
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=10
        )
        await self._subscribe()
        await self._read_initial_state()
        await self._start_udp()
        self._tasks = [
            asyncio.ensure_future(self._keepalive_loop()),
            asyncio.ensure_future(self._tcp_reader_loop()),
        ]
        _LOGGER.info("Crown connection established")

    async def async_disconnect(self) -> None:
        """Close all connections and cancel background tasks."""
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self._udp_transport:
            self._udp_transport.close()
            self._udp_transport = None
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
        _LOGGER.info("Crown disconnected")

    # ── Connection internals ─────────────────────────────────────────────────

    async def _subscribe(self) -> None:
        assert self._writer is not None
        cb = make_addr(self._our_node)
        dst = make_addr(self.crown_node)
        for msg in [
            self._disco_q,
            build_msg(MSG_MULTIGET, bytes([0x1A, 0, 0]), make_addr(self._our_node), dst),
            build_msg(MSG_SUBSCRIBE_ALL, bytes([0x1B, 0, 4, 0, 0, 0, 1, 0, 4, 0, 0x17]),
                      make_addr(self._our_node), dst),
            build_msg(MSG_SUBSCRIBE_SV, bytes([0x1C]) + cb + bytes([SUB_TYPE_ALL, 0, 50, 0, 1]),
                      make_addr(self._our_node), dst),
            build_msg(MSG_SUBSCRIBE_SV, bytes([0x1D]) + cb + bytes([SUB_TYPE_ALL, 0, 50, 0, 1]),
                      make_addr(self._our_node), dst),
            build_msg(MSG_SUBSCRIBE_SV, bytes([0x1E]) + cb + bytes([SUB_TYPE_ALL, 0, 50, 0, 1]),
                      make_addr(self._our_node), dst),
            build_msg(MSG_SUBSCRIBE_SV, bytes([0x1F]) + cb + bytes([SUB_TYPE_ALL, 0, 50, 0, 1]),
                      make_addr(self._our_node), dst),
        ]:
            self._writer.write(msg)
            await self._writer.drain()
            await asyncio.sleep(0.03)

    async def _read_initial_state(self) -> None:
        assert self._reader is not None
        buf = b""
        deadline = asyncio.get_event_loop().time() + 6.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                chunk = await asyncio.wait_for(self._reader.read(65536), timeout=1.0)
                if not chunk:
                    break
                buf += chunk
            except (asyncio.TimeoutError, TimeoutError):
                pass

        for msg in parse_hiqnet_stream(buf):
            if msg["type"] == MSG_MULTISET:
                src_node = struct.unpack_from(">H", msg["src"], 0)[0]
                dst_node = struct.unpack_from(">H", msg["dst"], 0)[0]
                if src_node == self.crown_node and dst_node == self._our_node:
                    self._process_multiobj(parse_multiobj_payload(msg["payload"]))

        _LOGGER.debug("Initial state read: %s", list(self._channels.values()))

    async def _start_udp(self) -> None:
        loop = asyncio.get_running_loop()
        # reuse_address was removed in Python 3.12; reuse_port not on all platforms
        for kwargs in [
            {"local_addr": ("0.0.0.0", UDP_PORT), "reuse_port": True},
            {"local_addr": ("0.0.0.0", UDP_PORT)},
        ]:
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _UDPProtocol(self), **kwargs
                )
                self._udp_transport = transport
                # Announce ourselves so the Crown adds a UDP route to our node
                for _ in range(3):
                    transport.sendto(self._disco_i, (self.host, UDP_PORT))
                    await asyncio.sleep(0.3)
                _LOGGER.info("UDP bound on port %d — meter data active", UDP_PORT)
                return
            except OSError as exc:
                _LOGGER.debug("UDP bind attempt failed (%s), retrying…", exc)
        _LOGGER.warning("UDP bind failed — meter data unavailable")

    async def _keepalive_loop(self) -> None:
        while True:
            await asyncio.sleep(5.0)
            if self._writer and not self._writer.is_closing():
                try:
                    self._writer.write(self._disco_q)
                    await self._writer.drain()
                except Exception as exc:
                    _LOGGER.warning("Keepalive failed: %s", exc)
                    break

    async def _tcp_reader_loop(self) -> None:
        """Read fader/mute change notifications pushed over TCP."""
        assert self._reader is not None
        while True:
            try:
                chunk = await asyncio.wait_for(self._reader.read(65536), timeout=2.0)
                if not chunk:
                    _LOGGER.warning("TCP connection closed by Crown")
                    break
                for msg in parse_hiqnet_stream(chunk):
                    if msg["type"] == MSG_MULTISET:
                        src_node = struct.unpack_from(">H", msg["src"], 0)[0]
                        dst_node = struct.unpack_from(">H", msg["dst"], 0)[0]
                        if src_node == self.crown_node and dst_node == self._our_node:
                            self._process_multiobj(parse_multiobj_payload(msg["payload"]))
            except (asyncio.TimeoutError, TimeoutError):
                pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _LOGGER.error("TCP reader error: %s", exc)
                break

    # ── State update ─────────────────────────────────────────────────────────

    def _process_multiobj(
        self, objects: dict[int, dict[int, tuple[int, bytes]]]
    ) -> None:
        """Apply parsed MultiObjectParamSet objects to channel state."""
        changed = False
        for ch in range(1, 9):
            fobj = FADER_OBJ_BASE + ch
            if fobj in objects:
                p = objects[fobj]
                if FADER_SV_ID in p:
                    dtype, val = p[FADER_SV_ID]
                    if dtype == TYPE_UBYTE and len(val) == 1:
                        self._channels[ch].fader_raw = val[0]
                        changed = True
                if MUTE_SV_ID in p:
                    dtype, val = p[MUTE_SV_ID]
                    if dtype == TYPE_UBYTE and len(val) == 1:
                        self._channels[ch].muted = bool(val[0])
                        changed = True

            mobj = METER_OBJ_BASE + ch
            if mobj in objects:
                p = objects[mobj]
                if NAME_PID in p:
                    _, val = p[NAME_PID]
                    name = decode_name(val)
                    if name:
                        self._channels[ch].name = name
                if METER_SV_ID in p:
                    dtype, val = p[METER_SV_ID]
                    if dtype == TYPE_UBYTE and len(val) == 1:
                        self._channels[ch].meter_raw = val[0]
                        changed = True

        if changed:
            self._fire_listeners()

    def _fire_listeners(self) -> None:
        snapshot = dict(self._channels)
        for cb in self._listeners:
            try:
                cb(snapshot)
            except Exception as exc:
                _LOGGER.error("Listener error: %s", exc)
