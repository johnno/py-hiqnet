"""pyhiqnet — Python library for Crown DCi amplifiers via HiQnet."""

from .crown import CrownAmpClient, CrownChannel
from .protocol import ubyte_to_db, power_corrected_db

__all__ = ["CrownAmpClient", "CrownChannel", "ubyte_to_db", "power_corrected_db"]
__version__ = "0.1.0"
