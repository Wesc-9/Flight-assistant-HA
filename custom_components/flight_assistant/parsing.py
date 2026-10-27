"""Pure flight parsing and planning rules; no Home Assistant dependencies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import xml.etree.ElementTree as ET

FLIGHT_RE = re.compile(r"^(?:SK|DY|D8|WF)\s?\\d{1,5}[A-Z]?$", re.IGNORECASE)
IATA_RE = re.compile(r"^[A-Z]{3}$")
STATUS_CODES = {"A": "Arrived", "C": "Cancelled", "D": "Departed",
                "E": "New time", "N": "New information"}

def parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string as an aware datetime, or return None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None

def normalize_flight(value: str) -> str:
    number = re.sub(r"\s+", "", value).upper()
    if not FLIGHT_RE.fullmatch(number):
        raise ValueError("Use a SAS/Norwegian/Widerøe flight number, e.g. SK345 or DY701.")
    return number

def normalize_airport(value: str) -> str:
    code = value.strip().upper()
    if not IATA_RE.fullmatch(code):
        raise ValueError("Airport must be a three-letter IATA code.")
    return code

def parse_avinor(xml_text: str) -> list[dict]:
    """Parse the documented Avinor XmlFeed flight fields."""
    if len(xml_text) > 5_000_000 or "<!DOCTYPE" in xml_text.upper():
        raise ValueError("Unexpected flight feed")
    root = ET.fromstring(xml_text)
    flights = []
    for node in root.findall(".//flight"):
        status = node.find("status")
        flight_id = (node.findtext("flight_id") or node.findtext("flightId") or "").strip()
        schedule = node.findtext("schedule_time")
        if not flight_id or not parse_datetime(schedule):
            continue
        codeshares = []
        for key in ("codeshareFlightNumbers", "codeshare_flight_numbers"):
            value = node.findtext(key)
            if value:
                codeshares.extend(re.split(r"[,;\s]+", value.upper()))
        flights.append({
            "flight_number": re.sub(r"\s+", "", flight_id).upper(),
            "scheduled": schedule,
            "expected": status.get("time") if status is not None else None,
            "status_code": status.get("code") if status is not None else None,
            "gate": node.findtext("gate"),
            "check_in": node.findtext("check_in"),
            "destination": node.findtext("airport"),
            "direction": node.findtext("arr_dep"),
            "codeshares": codeshares,
        })
    return flights

def match_departure(flights: list[dict], number: str, scheduled: datetime) -> dict | None:
    """Match by number and closeness; never use a different day's flight."""
    hits = []
    for item in flights:
        numbers = [item["flight_number"], *item["codeshares"]]
        flight_time = parse_datetime(item["scheduled"])
        if (number in numbers and item["direction"] == "D" and flight_time
                and abs((flight_time - scheduled).total_seconds()) <= 12 * 3600):
            hits.append((abs((flight_time - scheduled).total_seconds()), item))
    return min(hits, key=lambda it: it[0])[1] if hits else None

def airport_arrival_target(scheduled: datetime, margin_minutes: int) -> datetime:
    """Keep original check-in target when the flight is delayed."""
    return scheduled - timedelta(minutes=margin_minutes)

def recommended_drive_departure(
    scheduled: datetime, margin_minutes: int, drive_minutes: float, extra_minutes: int
) -> datetime:
    return airport_arrival_target(scheduled, margin_minutes) - timedelta(
        minutes=drive_minutes + extra_minutes
    )
