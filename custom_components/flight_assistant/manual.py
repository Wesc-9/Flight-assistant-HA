"""Validate flight segments entered by the traveler.

Manually entered information is a claim from the user, NOT verified airline data.
Home Assistant's configured timezone is used only when the selector emits a
naive date/time; it may differ from the airport's local timezone.
"""
from __future__ import annotations
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .parsing import normalize_airport, normalize_flight, parse_datetime


def interpret_datetime(value: str, hass_timezone: str) -> datetime:
    """Convert HA datetime selector output to an offset-aware instant."""
    if not value or not isinstance(value, str):
        raise ValueError("Departure/arrival date and time is required")
    value = value.strip()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid ISO date and time") from exc
    if stamp.tzinfo is None:
        try:
            zone = ZoneInfo(hass_timezone)
        except (KeyError, ValueError) as exc:
            raise ValueError("Home Assistant timezone not available") from exc
        stamp = stamp.replace(tzinfo=zone)
    return stamp.astimezone(timezone.utc)


def manual_flight(data: dict, hass_timezone: str) -> dict:
    """Return sanitized JSON-friendly segment, without PNR or passenger details."""
    number = normalize_flight(data.get("flight_number", ""))
    origin = normalize_airport(data.get("origin", ""))
    destination = normalize_airport(data.get("destination", ""))
    if origin == destination:
        raise ValueError("Origin and destination must differ")
    departure = interpret_datetime(data.get("scheduled"), hass_timezone)
    arrival = interpret_datetime(data.get("arrival_scheduled"), hass_timezone)
    if arrival <= departure or (arrival - departure).total_seconds() > 48 * 3600:
        raise ValueError("Arrival must be after departure and within 48 hours")
    return {
        "flight_number": number,
        "origin": origin,
        "destination": destination,
        "scheduled": departure.isoformat(),
        "arrival_scheduled": arrival.isoformat(),
        "source": "manual",
        "airline_verified": False,
    }
