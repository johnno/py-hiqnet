"""
py-hiqnet live monitor — runs the same display as crown-poc/monitor_crown.py
but uses the pyhiqnet library directly.

Usage:
    python test_monitor.py [host]   # default 192.168.1.127
    q / Ctrl-C to quit

This is the reference test script: if it works here, the HA integration
should work too (same library, same code paths).
"""

import asyncio
import copy
import curses
import math
import sys
import threading
import time

from pyhiqnet.const import METER_NOISE_FLOOR
from pyhiqnet.crown import CrownAmpClient, CrownChannel

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.127"

# Per-channel impedance — edit to match your wiring
CHANNEL_OHMS = {
    1: 4,   # Main Bar A   — 2×8Ω ‖
    2: 4,   # Main Bar B   — 2×8Ω ‖
    3: 4,   # Back Bar     — 2×8Ω ‖
    4: 12,  # Snug         — 2×6Ω series
    5: 12,  # Yard         — 2×6Ω series
    6: 4,   # Terrace      — 2×8Ω ‖
    7: 4,   # Nest         — 2×8Ω ‖
    8: 4,   # Terrace Annex— 2×8Ω ‖
}

BAR_W = 22
_lock = threading.Lock()
_stop = threading.Event()
_snap: dict[int, CrownChannel] = {}
_status = ["Connecting…"]
_log: list[str] = []


def _bar(db: float) -> str:
    signal = max(0.0, db - METER_NOISE_FLOOR)
    span   = -METER_NOISE_FLOOR
    filled = max(0, min(BAR_W, int(signal / span * BAR_W)))
    return "|" + "█" * filled + "░" * (BAR_W - filled) + "|"


def _pwr(db: float, ohms: float) -> float:
    return db - 10 * math.log10(ohms / 4)


# ── network thread ────────────────────────────────────────────────────────────

def _net_thread():
    async def _run():
        client = CrownAmpClient(HOST)

        def _on_update(channels):
            with _lock:
                _snap.update(channels)
                _log.append(f"Update: {len(channels)} channels")

        client.add_listener(_on_update)

        try:
            with _lock:
                _status.append(f"TCP connecting to {HOST}:3804 …")
            await client.async_connect()
            with _lock:
                _status.append("Connected — TCP+UDP active")
        except Exception as exc:
            with _lock:
                _status.append(f"Connection failed: {exc}")
            return

        while not _stop.is_set():
            await asyncio.sleep(0.5)

        await client.async_disconnect()

    asyncio.run(_run())


# ── curses display ────────────────────────────────────────────────────────────

def _curses_main(stdscr):
    curses.curs_set(0)
    curses.start_color(); curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN,  -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED,    -1)
    curses.init_pair(4, curses.COLOR_CYAN,   -1)
    curses.init_pair(5, curses.COLOR_WHITE,  -1)
    stdscr.nodelay(True)
    show_power = False

    while True:
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            _stop.set(); break
        if key in (ord("p"), ord("P")):
            show_power = not show_power

        with _lock:
            channels = copy.deepcopy(_snap)
            status   = _status[-1] if _status else ""

        h, w = stdscr.getmaxyx()
        stdscr.erase()

        title = f"py-hiqnet live monitor — {HOST}"
        stdscr.addstr(0, 0, title, curses.A_BOLD)
        stdscr.addstr(1, 0, "─" * min(w - 1, 80))

        if show_power:
            hdr = f"  {'#':<3} {'Name':<20}  {'Fader':>9}  {'Mute':>5}  Pwr-equiv dB (4Ω ref)  bar"
        else:
            hdr = f"  {'#':<3} {'Name':<20}  {'Fader':>9}  {'Mute':>5}  Voltage dBV            bar"
        stdscr.addstr(2, 0, hdr[:w - 1], curses.A_BOLD | curses.color_pair(4))
        stdscr.addstr(3, 0, "─" * min(w - 1, 80))

        for ch_num in range(1, 9):
            row = 4 + ch_num - 1
            if row >= h - 3:
                break
            ch = channels.get(ch_num)
            if ch is None:
                stdscr.addstr(row, 0, f"  {ch_num:<3} (waiting…)")
                continue

            ohms = CHANNEL_OHMS.get(ch_num, 4)
            name = ch.name or f"Ch {ch_num}"

            fader_s = f"{ch.fader_db:+6.1f} dB"
            mute_s  = "MUTED" if ch.muted else "  ok "
            ma      = (curses.color_pair(3) | curses.A_BOLD) if ch.muted else curses.color_pair(1)

            pwr_db     = _pwr(ch.meter_db, ohms)
            display_db = pwr_db if show_power else ch.meter_db
            stale      = "?" if (time.monotonic() - 0) > 999 else " "  # always fresh in library
            meter_s    = f" {display_db:>+6.1f} dB {_bar(display_db)}"
            va         = (curses.color_pair(3) if pwr_db >= -6
                          else curses.color_pair(2) if pwr_db >= -20
                          else curses.color_pair(1))

            prefix = f"  {ch_num:<3} {name:<20}  {fader_s}  "
            stdscr.addstr(row, 0, prefix[:w - 1])
            col = len(prefix)
            if col < w - 1:
                stdscr.addstr(row, col, mute_s[:w - col - 1], ma)
                col += len(mute_s) + 1
            if col < w - 1:
                stdscr.addstr(row, col, meter_s[:w - col - 1], va)

        stdscr.addstr(h - 2, 0, "─" * min(w - 1, 80))
        mode = "Pwr-equiv (4Ω ref)" if show_power else "Raw voltage dBV"
        stdscr.addstr(h - 1, 0, f"  {status}"[:w - 28], curses.A_DIM)
        stdscr.addstr(h - 1, w - 27, f"  p={mode}  q=quit"[:26], curses.A_DIM)

        stdscr.refresh()
        time.sleep(0.1)


def main():
    t = threading.Thread(target=_net_thread, daemon=True)
    t.start()
    time.sleep(0.3)
    try:
        curses.wrapper(_curses_main)
    except KeyboardInterrupt:
        _stop.set()
    finally:
        _stop.set()
        t.join(timeout=3)


if __name__ == "__main__":
    main()
