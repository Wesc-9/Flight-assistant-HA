"""Pure, defensive normalization of provider travel-time data."""
from __future__ import annotations
from datetime import datetime


def valid_minutes(value):
    """Return a realistic nonnegative duration or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0 or value > 1440:
        return None
    return round(float(value), 1)


def shortest_waze_route(result):
    """Parse Waze get_travel_times response; durations are in minutes."""
    if not isinstance(result, dict) or not isinstance(result.get("routes"), list):
        return None
    options = [
        valid_minutes(route.get("duration"))
        for route in result["routes"]
        if isinstance(route, dict)
    ]
    valid = [duration for duration in options if duration is not None]
    return min(valid) if valid else None


def parse_entur_patterns(payload, target, parse_datetime):
    """Find latest public-transport itinerary arriving before requested margin.

    Ignore walking-only itineraries: a public-transport alternative must contain
    a vehicle leg. Do not return routes that arrive after the airport deadline.
    """
    if not isinstance(payload, dict) or payload.get("errors"):
        return None
    root = payload.get("data")
    if not isinstance(root, dict):
        return None
    trip = root.get("trip")
    if not isinstance(trip, dict):
        return None
    patterns = trip.get("tripPatterns")
    if not isinstance(patterns, list):
        return None
    candidates = []
    for pattern in patterns:
        if not isinstance(pattern, dict):
            continue
        legs = pattern.get("legs")
        if not isinstance(legs, list) or not legs:
            continue
        public_legs = [
            leg for leg in legs if isinstance(leg, dict)
            and leg.get("mode") not in (None, "foot", "walk")
            and str(leg.get("mode")).lower() not in ("foot", "walk", "walking")
        ]
        if not public_legs:
            continue
        first_leg = legs[0] if isinstance(legs[0], dict) else {}
        last_leg = legs[-1] if isinstance(legs[-1], dict) else {}
        start = parse_datetime(first_leg.get("expectedStartTime"))
        end = parse_datetime(last_leg.get("expectedEndTime"))
        if not start or not end or end > target or end <= start:
            continue
        # Ignore multimodal records with impossible transfers or leg times.
        segments = []
        for leg in legs:
            if not isinstance(leg, dict):
                segments = []
                break
            leg_start = parse_datetime(leg.get("expectedStartTime"))
            leg_end = parse_datetime(leg.get("expectedEndTime"))
            if not leg_start or not leg_end or leg_start > leg_end:
                segments = []
                break
            line = leg.get("line") or {}
            if not isinstance(line, dict):
                line = {}
            from_place = leg.get("fromPlace") or {}
            to_place = leg.get("toPlace") or {}
            segments.append({
                "mode": leg.get("mode"),
                "route": line.get("publicCode") or line.get("name"),
                "from": from_place.get("name") if isinstance(from_place, dict) else None,
                "to": to_place.get("name") if isinstance(to_place, dict) else None,
                "start": leg_start.isoformat(),
                "end": leg_end.isoformat(),
            })
        if not segments:
            continue
        candidates.append((start, end, segments))
    if not candidates:
        return None
    start, end, legs = max(candidates, key=lambda t: t[0])
    return {
        "leave_at": start.isoformat(),
        "arrive_at": end.isoformat(),
        "minutes": round((end - start).total_seconds() / 60),
        "legs": legs,
        "source": "Entur",
    }
