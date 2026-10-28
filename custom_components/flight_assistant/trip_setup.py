"""Pure flight itinerary registration validation."""
from __future__ import annotations

from datetime import date

from .parsing import normalize_flight, normalize_airport

AIRLINES = {
    "SK": "sas",
    "DY": "norwegian",
    "D8": "norwegian",
    "WF": "wideroe",
}


def airline_from_number(flight_number: str) -> str:
    """Determine airline from the flight's two-character IATA prefix."""
    flight = normalize_flight(flight_number)
    return AIRLINES[flight[:2]]


def airline_display(airline: str) -> str:
    return {
        "sas": "SAS",
        "norwegian": "Norwegian",
        "wideroe": "Widerøe",
    }.get(airline, "Ukjent")


def make_segment(number: str, when: str, departure_airport: str | None) -> dict:
    flight = normalize_flight(number)
    day = date.fromisoformat(when).isoformat()
    airport = (departure_airport or "").strip()
    return {
        "flight_number": flight,
        "flight_date": day,
        "departure_airport": normalize_airport(airport) if airport else None,
    }
