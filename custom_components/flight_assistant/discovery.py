"""Match manually entered flight numbers and dates against Avinor departures.

Avinor's XmlFeed requires an airport. Without a user-supplied departure
airport, search a limited list of major Norwegian airports and ask the
user to supply the airport if no unique match exists.
No match, stale data, and multiple matches must never be represented as a
confirmed flight.
"""
from __future__ import annotations
from datetime import date
from zoneinfo import ZoneInfo

from .parsing import parse_datetime

NORWAY_TZ = ZoneInfo("Europe/Oslo")
# Major Avinor airports; NOT an assertion that this covers all airports.
MAJOR_AIRPORTS = (
    "OSL", "BGO", "TRD", "SVG", "TOS", "BOO", "KRS", "EVE",
    "AES", "HAU", "MOL", "KSU", "ALF", "LYR", "KKN",
)

def normalize_codeshare(raw: str) -> str:
    return "".join(raw.upper().split())

def find_candidates(feeds: dict[str, list[dict]], number: str, day: str) -> list[dict]:
    """Find departures on the chosen NORWEGIAN local calendar day."""
    wanted_day = date.fromisoformat(day)
    number = normalize_codeshare(number)
    found = []
    keys = set()
    for origin, flights in feeds.items():
        for item in flights:
            if item.get("direction") != "D":
                continue
            stamp = parse_datetime(item.get("scheduled"))
            if stamp is None or stamp.astimezone(NORWAY_TZ).date() != wanted_day:
                continue
            numbers = [item.get("flight_number") or "", *(item.get("codeshares") or [])]
            if number not in {normalize_codeshare(n) for n in numbers if n}:
                continue
            key = (origin, stamp.isoformat(), item.get("destination"))
            if key in keys:
                continue
            keys.add(key)
            found.append({**item, "origin": origin})
    return sorted(found, key=lambda x: (x["scheduled"], x["origin"]))

def classify_match(feeds: dict[str, list[dict]], number: str, day: str) -> tuple[str, dict | None]:
    """Only an unambiguous single match may become a resolved itinerary."""
    found = find_candidates(feeds, number, day)
    if len(found) == 1:
        return "found", found[0]
    if len(found) > 1:
        return "ambiguous", None
    return "not_found", None
