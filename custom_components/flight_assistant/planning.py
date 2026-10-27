"""Pure rules for configurable airport arrival windows.

These are *planning suggestions*, not airline bag-drop or boarding deadlines.
"""
from __future__ import annotations
from datetime import datetime, timedelta

DEFAULT_MARGINS = {"domestic": 90, "international": 120}
# Conservative soft thresholds; never claim airline-approved latest check-in.
LATE_OPTION_FLOOR = {
    ("domestic", True): 60,
    ("domestic", False): 45,
    ("international", True): 90,
    ("international", False): 75,
}

def default_margin(journey_type: str) -> int:
    if journey_type not in DEFAULT_MARGINS:
        raise ValueError("journey_type must be domestic or international")
    return DEFAULT_MARGINS[journey_type]

def arrival_windows(
    scheduled: datetime,
    margin_minutes: int,
    journey_type: str,
    checked_bag: bool,
) -> list[dict]:
    """Three options: early, chosen target, later only above soft minimum."""
    if scheduled.tzinfo is None:
        raise ValueError("Scheduled time must include timezone")
    if margin_minutes < 45 or margin_minutes > 240 or margin_minutes % 15:
        raise ValueError("Airport margin must be 45–240 min in 15-minute increments")
    floor = LATE_OPTION_FLOOR[(journey_type, checked_bag)]
    candidates = [
        (1, "good_time", margin_minutes + 30),
        (2, "recommended", margin_minutes),
        (3, "later", margin_minutes - 30),
    ]
    return [
        {
            "number": number,
            "kind": kind,
            "margin_minutes": margin,
            "airport_arrival_target": (scheduled - timedelta(minutes=margin)).isoformat(),
            "airline_deadlines_verified": False,
        }
        for number, kind, margin in candidates
        if margin >= floor and margin >= 45
    ]

def driving_leave_at(arrival_target: datetime, road_minutes: float, parking_buffer: int) -> str:
    if road_minutes < 0 or parking_buffer < 0:
        raise ValueError("Durations must be positive")
    return (arrival_target - timedelta(minutes=road_minutes + parking_buffer)).isoformat()

def time_progress_percent(departure: datetime, arrival: datetime, now: datetime) -> int | None:
    """Schedule-based elapsed time; not an aircraft GPS location."""
    elapsed = (arrival - departure).total_seconds()
    if elapsed <= 0:
        return None
    return round(min(100, max(0, (now - departure).total_seconds() / elapsed * 100)))
