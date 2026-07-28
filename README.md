# py-hiqnet

Async Python library for Crown DCi amplifiers via HiQnet.

## Installation

```bash
pip install git+https://github.com/johnno/py-hiqnet.git
```

## Usage

```python
from pyhiqnet.crown import CrownAmpClient

async def on_update(channels):
    for ch in channels.values():
        print(ch)

client = CrownAmpClient("192.168.1.127")
client.add_listener(on_update)
await client.async_connect()
```

## Channel state

Each `CrownChannel` exposes:
- `fader_db` — output fader level in dB (-100..+6)
- `volume_level` — fader as 0.0–1.0
- `muted` — bool
- `meter_db` — RMS output level in dB (noise floor ≈ -74 dB)
- `signal_present` — True when audio signal above noise floor
- `name` — zone name from the Crown

## Protocol notes

- HiQnet port 3804 TCP + UDP
- TCP: subscription session, fader/mute change notifications
- UDP: live sensor pushes (~150 ms), RMS meter values
- Uses Crown DCi proprietary MultiObjectParamSet (0x0101) format
