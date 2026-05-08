"""Search Czech Railways (ČD / České dráhy) connections from the command line.

Hits the same endpoints the cd.cz/spojeni-a-jizdenka page does:
  1. Resolve station names via the public autocomplete service
  2. POST the search form, parse the embedded `var model = {…}` JSON

No login, no partner credentials, no scraping HTML — the model JSON has
everything we need. Public train info only; this won't give you ticket
prices for international trains (those need the cd.cz reseller flow,
which isn't in scope here).
"""
from __future__ import annotations

from .core import (
    ConnectionRow,
    StationChoice,
    TrainLeg,
    resolve_station,
    search_connections,
)

__all__ = [
    "ConnectionRow",
    "StationChoice",
    "TrainLeg",
    "resolve_station",
    "search_connections",
]
