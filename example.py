"""Example: connect to a Crown DCi 8300N and print live channel state."""

import asyncio
import sys
from pyhiqnet.crown import CrownAmpClient

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.127"


def on_update(channels):
    for ch in sorted(channels.values(), key=lambda c: c.number):
        sig = "▶" if ch.signal_present else "·"
        print(
            f"  {sig} Ch {ch.number:1d} {ch.name:<20}  "
            f"fader={ch.fader_db:+6.1f} dB  "
            f"{'MUTED' if ch.muted else '     '}  "
            f"meter={ch.meter_db:+6.1f} dB"
        )
    print()


async def main():
    client = CrownAmpClient(HOST)
    client.add_listener(on_update)

    print(f"Connecting to {HOST}…")
    await client.async_connect()
    print("Connected — press Ctrl-C to stop\n")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await client.async_disconnect()


asyncio.run(main())
