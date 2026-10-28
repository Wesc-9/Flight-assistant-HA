"""Flight Assistant persistent state, live updates and transport coordination."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from .avinor import fetch_departures
from .const import (
    DOMAIN, STORAGE_VERSION, UPDATE_INTERVAL_SECONDS,
    AIRPORTS, DEFAULT_MARGIN_MINUTES, DEFAULT_EXTRA_DRIVE_MINUTES,
)
from .parsing import match_departure, parse_datetime, airport_arrival_target, recommended_drive_departure
from .transport import get_waze_minutes, get_entur_journey
from .planning import arrival_windows, driving_leave_at
from .display import minutes_text
from .discovery import classify_match, MAJOR_AIRPORTS
from .aerodatabox import fetch_number_date, AeroDataBoxError
import asyncio
from homeassistant.helpers import device_registry as dr

_LOGGER = logging.getLogger(__name__)

class FlightCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.flights: dict[str, dict] = {}
        self.booking_records: dict[str, dict] = {}
        self._bookings_dirty = False
        self._search_attempts: dict[tuple[str, int, str], datetime] = {}
        self.transport: dict[str, dict] = {}
        self._drive_cache: dict[str, tuple] = {}
        self._entur_cache: dict[str, tuple] = {}
        self._alerts: set[str] = set()

    def setting(self, name, default=None):
        return self.entry.options.get(name, self.entry.data.get(name, default))

    async def async_load_flights(self):
        saved = await self.store.async_load() or {}
        self.flights = saved.get("flights", {})
        self.booking_records = saved.get("booking_records", {})
        # Purge results older than AeroDataBox Basic's 7-day retention period.
        # Refresh is gated separately to avoid accidental quota exhaustion.
        now = dt_util.utcnow()
        for booking_id, record in list(self.booking_records.items()):
            if not any(s.subentry_id == booking_id
                       for s in self.entry.get_subentries_of_type("booking")):
                del self.booking_records[booking_id]
                continue
            for segment in record.get("segments", []):
                stamp = parse_datetime(segment.get("fetched_at"))
                if not stamp or now - stamp > timedelta(days=6):
                    segment["status"] = "pending"
                    segment.pop("flight", None)
                    segment.pop("fetched_at", None)
            record["status"] = "pending"

    async def async_save_all(self):
        # Live route estimates are transient; recompute on restart.
        # Do not persist frequently changing Entur/Waze data to disk.
        persisted_bookings = {
            uid: {key: value for key, value in record.items()
                  if key != "transport"}
            for uid, record in self.booking_records.items()
        }
        await self.store.async_save({
            "flights": self.flights, "booking_records": persisted_bookings,
        })

    async def async_add_flight(self, flight_number, origin, scheduled, destination=None,
                               preferences=None, arrival_scheduled=None):
        uid = uuid4().hex
        self.flights[uid] = {
            "id": uid, "flight_number": flight_number, "origin": origin,
            "scheduled": scheduled.isoformat(), "destination": destination,
            "status": "Planned", "flight_data": "Not yet verified",
            "gate": None, "check_in": None, "expected": None,
            "preferences": preferences or {},
            "arrival_scheduled": arrival_scheduled,
        }
        await self.async_save_all()
        await self.async_request_refresh()
        return uid

    async def async_remove_flight(self, uid):
        if uid not in self.flights:
            return False
        del self.flights[uid]
        self.transport.pop(uid, None)
        for key in list(self._entur_cache):
            if key == uid or (isinstance(key, tuple) and key[0] == uid):
                self._entur_cache.pop(key, None)
        self._drive_cache.pop(uid, None)
        await self.async_save_all()
        await self.async_request_refresh()
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, uid)}, connections=set())
        if device is not None:
            registry.async_remove_device(device.id)
        return True

    async def _async_update_data(self):
        now = dt_util.utcnow()
        airports = {
            item["origin"] for item in self.flights.values()
            if (stamp := parse_datetime(item.get("scheduled")))
            and -timedelta(hours=6) <= stamp - now <= timedelta(hours=24)
        }
        feeds = {}
        for airport in airports:
            try:
                feeds[airport] = await fetch_departures(self.hass, airport)
            except RuntimeError as exc:
                _LOGGER.warning("%s", exc)

        dirty = False
        for uid, flight in self.flights.items():
            scheduled = parse_datetime(flight.get("scheduled"))
            if not scheduled:
                continue
            if flight["origin"] in feeds:
                found = match_departure(
                    feeds[flight["origin"]], flight["flight_number"], scheduled
                )
                if found:
                    updates = {
                        "flight_data": "Avinor",
                        "status": {
                            "A": "Arrived", "C": "Cancelled",
                            "D": "Departed", "E": "New time",
                            "N": "New information",
                        }.get(found["status_code"], "Scheduled"),
                        "gate": found["gate"],
                        "check_in": found["check_in"],
                        "expected": found["expected"] if parse_datetime(found["expected"]) else None,
                        "destination": found["destination"] or flight.get("destination"),
                    }
                    changes = {
                        key: (flight.get(key), val)
                        for key, val in updates.items() if flight.get(key) != val
                    }
                    if changes:
                        flight.update(updates)
                        dirty = True
                        # No passenger or booking details ever enter HA events.
                        self.hass.bus.async_fire(f"{DOMAIN}_changed", {
                            "flight_id": uid, "flight_number": flight["flight_number"],
                            "changes": list(changes),
                        })
            if flight.get("status") != "Cancelled" and timedelta(hours=0) <= scheduled - now <= timedelta(hours=36):
                await self._refresh_transport(uid, flight, scheduled, now)
            else:
                self.transport.pop(uid, None)

        if dirty:
            await self.async_save_all()
        await self._async_update_bookings(now)
        return dict(self.flights)

    async def _async_update_bookings(self, now):
        """Resolve user-submitted flight numbers, minimizing API quota use.

        Each segment is independently resolved. The original flight number/date
        remain authoritative user input when any provider is unavailable.
        """
        subentries = self.entry.get_subentries_of_type("booking")
        active = {s.subentry_id for s in subentries}
        changed = False
        for removed in set(self.booking_records) - active:
            del self.booking_records[removed]
            changed = True
        api_key = self.setting("aerodatabox_api_key", "")
        # Strict upper bound on paid-tier-style calls per 3-min update (2 units each).
        # A successful lookup is cached for 6 days and never requested every poll.
        calls_this_cycle = 0
        for booking in subentries:
            raw_segments = booking.data.get("segments", [])
            if not raw_segments:
                continue  # PNR-only: no flight dates to resolve.
            current = self.booking_records.setdefault(
                booking.subentry_id, {"status": "pending", "segments": []}
            )
            old_by_slot = {
                i: item for i, item in enumerate(current.get("segments") or [])
            }
            results = []
            for i, segment in enumerate(raw_segments):
                number = segment.get("flight_number")
                travel_date = segment.get("flight_date")
                origin = segment.get("departure_airport")
                if not number or not travel_date:
                    continue
                try:
                    distance = (datetime.fromisoformat(travel_date).date() - now.date()).days
                except ValueError:
                    continue
                prev = old_by_slot.get(i, {})
                if prev.get("flight_number") != number or prev.get("flight_date") != travel_date:
                    prev = {}
                cached = dict(prev)
                cached.update({"flight_number": number, "flight_date": travel_date,
                               "departure_airport": origin})
                fetched = parse_datetime(cached.get("fetched_at"))
                if fetched and now - fetched >= timedelta(days=6):
                    cached.pop("flight", None)
                    cached.pop("fetched_at", None)
                    cached["status"] = "pending"
                if distance > 365:
                    cached.update(status="pending", reason="outside_365_day_window")
                elif distance < -1:
                    cached.setdefault("reason", "past_flight")
                elif (cached.get("status") == "found" and cached.get("flight")
                      and fetched and now - fetched < timedelta(days=6)):
                    pass  # Still fresh; no additional billed lookup.
                else:
                    attempt_key = (booking.subentry_id, i,
                                   "aero" if api_key else "avinor")
                    last_attempt = self._search_attempts.get(attempt_key)
                    saved_attempt = parse_datetime(cached.get("checked_at"))
                    same_provider = cached.get("last_provider") == (
                        "aero" if api_key else "avinor"
                    )
                    cadence = timedelta(days=6) if api_key else timedelta(hours=12)
                    if ((last_attempt and now - last_attempt < cadence)
                            or (same_provider and saved_attempt
                                and now - saved_attempt < cadence)):
                        cached.setdefault("status", "pending")
                    elif api_key and calls_this_cycle < 2:
                        self._search_attempts[attempt_key] = now
                        cached["checked_at"] = now.isoformat()
                        cached["last_provider"] = "aero"
                        calls_this_cycle += 1
                        try:
                            outcome, flight = await fetch_number_date(
                                self.hass, api_key, number, travel_date, origin
                            )
                            cached["status"] = outcome
                            cached["reason"] = None if flight else outcome
                            if flight:
                                cached.update(flight=flight, fetched_at=now.isoformat())
                            # Respect the API request rate ceiling of ~1/s.
                            await asyncio.sleep(1.1)
                        except AeroDataBoxError as exc:
                            cached.update(status="pending", reason=str(exc))
                    elif not api_key and 0 <= distance <= 13:
                        self._search_attempts[attempt_key] = now
                        cached["checked_at"] = now.isoformat()
                        cached["last_provider"] = "avinor"
                        # Known origin: one airport. No origin: five major airports
                        # to avoid scraping the complete national timetable.
                        airports = [origin] if origin else list(MAJOR_AIRPORTS[:5])
                        data = {}
                        for airport in airports:
                            try:
                                data[airport] = await fetch_departures(
                                    self.hass, airport, hours_ahead=min(336, (distance+2)*24)
                                )
                            except RuntimeError:
                                continue
                        outcome, match = classify_match(data, number, travel_date)
                        cached["status"] = outcome
                        cached["reason"] = outcome if not match else None
                        if match:
                            cached["flight"] = {
                                "flight_number": number,
                                "origin": match["origin"],
                                "destination": match.get("destination"),
                                "scheduled": match["scheduled"],
                                "arrival_scheduled": None,
                                "gate": match.get("gate"),
                                "check_in": match.get("check_in"),
                                "status": "Scheduled",
                                "source": "Avinor",
                                "airline_verified": False,
                            }
                            cached["fetched_at"] = now.isoformat()
                    else:
                        cached.setdefault("status", "pending")
                        cached["reason"] = (
                            "aerodatabox_key_not_set" if not api_key else "lookup_deferred"
                        )
                # Avinor is the preferred local status/gate/check-in source
                # near departure. AeroDataBox schedule and flight number remain
                # separate from these short-range operational updates.
                flight = cached.get("flight")
                depart = parse_datetime((flight or {}).get("scheduled"))
                if (cached.get("status") == "found" and depart
                        and flight.get("origin") and
                        -timedelta(hours=3) <= depart - now <= timedelta(hours=24)):
                    try:
                        avinor_flights = await fetch_departures(
                            self.hass, flight["origin"], hours_ahead=26
                        )
                        matched = match_departure(
                            avinor_flights, number, depart
                        )
                        if matched:
                            flight.update({
                                "gate": matched.get("gate") or flight.get("gate"),
                                "check_in": matched.get("check_in") or flight.get("check_in"),
                                "expected": matched.get("expected"),
                                "status": {
                                    "A": "Arrived", "C": "Cancelled",
                                    "D": "Departed", "E": "New time",
                                }.get(matched.get("status_code"), flight.get("status")),
                                "operational_data_source": "Avinor",
                            })
                    except RuntimeError:
                        pass
                results.append(cached)

            statuses = [r.get("status") for r in results]
            if statuses and all(s == "found" for s in statuses):
                status = "resolved"
            elif any(s == "found" for s in statuses):
                status = "partial"
            else:
                status = "pending"
            # Only store flat, allowlisted safe flight fields and lookup status.
            new_record = {"status": status, "segments": results,
                          "note": (
                              "Finner flydata fra gratis datakilder."
                              if status != "resolved" else
                              "Ruteinformasjon hentet fra tredjepart, ikke bekreftet PNR."
                          )}
            # Departure suggestions only for the first upcoming leg.
            upcoming = [
                row["flight"] for row in results
                if row.get("status") == "found"
                and (flight := row.get("flight"))
                and parse_datetime(flight.get("scheduled"))
                and parse_datetime(flight["scheduled"]) >= now
            ]
            if upcoming:
                first = min(upcoming, key=lambda f: f["scheduled"])
                scheduled_time = parse_datetime(first["scheduled"])
                if (scheduled_time and -timedelta(hours=2) <= scheduled_time - now
                        <= timedelta(days=4)):
                    prefs = booking.data
                    preferences = {
                        "journey_type": prefs.get("journey_type", "domestic"),
                        "checked_bag": prefs.get("checked_bag", False),
                        "airport_margin_minutes": prefs.get("airport_margin_minutes", 90),
                        "transport_mode": prefs.get("transport_mode", "both"),
                        "departure_address": (
                            prefs.get("resolved_origin_address")
                            if prefs.get("origin_address") else None
                        ),
                        "departure_latitude": prefs.get("departure_latitude"),
                        "departure_longitude": prefs.get("departure_longitude"),
                    }
                    await self._refresh_transport(
                        "booking_" + booking.subentry_id,
                        {**first, "preferences": preferences},
                        scheduled_time, now
                    )
                    new_record["transport"] = self.transport.get(
                        "booking_" + booking.subentry_id, {}
                    )
                elif scheduled_time:
                    new_record["transport"] = {
                        "transport_status": "outside_planning_window",
                        "reason": "Kollektivtider beregnes nærmere reisen; kjøretid basert på trafikk vises først samme dag.",
                        "suggestions": [],
                        "drive_minutes": None, "public_transport": None,
                        "drive_leave_at": None,
                    }
            if new_record != current:
                prior_persistent = {
                    k: v for k, v in current.items() if k != "transport"
                }
                new_persistent = {
                    k: v for k, v in new_record.items() if k != "transport"
                }
                self.booking_records[booking.subentry_id] = new_record
                if prior_persistent != new_persistent:
                    changed = True
        if changed:
            await self.async_save_all()
        self.async_update_listeners()

    async def _refresh_transport(self, uid, flight, scheduled, now):
        """Calculate three airport arrival windows, each with available transport."""
        airport = flight["origin"]
        coords = AIRPORTS.get(airport)
        if not coords:
            self.transport[uid] = {"reason": "airport_location_not_supported"}
            return

        prefs = flight.get("preferences") or {}
        journey_type = prefs.get("journey_type", "international")
        if journey_type not in ("domestic", "international"):
            journey_type = "international"
        margin = int(prefs.get(
            "airport_margin_minutes",
            self.setting("airport_margin_minutes", DEFAULT_MARGIN_MINUTES)
        ))
        # Older saved flights may use non-step-specific margins.
        margin = max(45, min(240, round(margin / 15) * 15))
        checked_bag = bool(prefs.get("checked_bag", False))
        mode = prefs.get("transport_mode", "both")
        drive_enabled = mode in ("both", "drive", "taxi")
        transit_enabled = mode in ("both", "transit", "bus", "train")
        address = prefs.get("departure_address")
        manual_lat = prefs.get("departure_latitude")
        manual_lon = prefs.get("departure_longitude")
        has_manual_coords = manual_lat is not None and manual_lon is not None
        origin_coords = ((manual_lat, manual_lon) if has_manual_coords else (
            self.setting("home_latitude"), self.setting("home_longitude")))
        if any(x is None for x in origin_coords):
            self.transport[uid] = {"reason": "origin_coordinates_missing"}
            return
        # Address-only origins can be looked up in Waze, but not Entur.
        # Never silently route transit from HA's home coordinates for an address override.
        transit_coords = None if address and not has_manual_coords else origin_coords
        driving_origin = address if address else origin_coords
        buffer = self.setting("drive_buffer_minutes", DEFAULT_EXTRA_DRIVE_MINUTES)
        windows = arrival_windows(scheduled, margin, journey_type, checked_bag)
        data = self.transport.setdefault(uid, {})
        data.update({
            "airport": airport, "origin_address": address,
            "transport_mode": mode,
            "updated_at": now.isoformat(),
            "transport_status": "calculating",
            "arrive_airport_by": windows[1]["airport_arrival_target"],
            "airport_arrival_margin_minutes": margin,
            "airline_check_in_cutoff_verified": False,
            "reason": None,
        })

        drive_minutes = None
        if drive_enabled and scheduled - now <= timedelta(hours=12):
            cached = self._drive_cache.get(uid)
            if cached and now - cached[0] < timedelta(minutes=10):
                drive_minutes = cached[1]
            else:
                drive_minutes = await get_waze_minutes(
                    self.hass, driving_origin, coords,
                    vehicle_type="taxi" if mode == "taxi" else "car",
                )
                self._drive_cache[uid] = (now, drive_minutes)
        data["drive_minutes"] = drive_minutes
        data["drive_duration_text"] = minutes_text(drive_minutes) if drive_minutes is not None else None
        if not drive_enabled:
            data["drive_status"] = "not_requested"
        elif scheduled - now > timedelta(hours=12):
            data["drive_status"] = "too_early_for_live_traffic"
        elif not self.hass.services.has_service("waze_travel_time", "get_travel_times"):
            data["drive_status"] = "install_waze_travel_time"
        else:
            data["drive_status"] = "available" if drive_minutes is not None else "route_unavailable"
        data["driving_source"] = (
            "Waze taxi estimate (pickup wait not included)" if mode == "taxi"
            else "Waze (live traffic)"
        ) if drive_minutes is not None else None

        # Match each arrival window against a REAL Entur itinerary.
        async def transit_for(window):
            target = parse_datetime(window["airport_arrival_target"])
            if not transit_enabled or transit_coords is None or not target:
                return None
            if not timedelta(0) <= target - now <= timedelta(days=3):
                return None
            cache_key = (uid, window["airport_arrival_target"])
            cached = self._entur_cache.get(cache_key)
            # Farther in advance, 3-hour cache is enough; refresh more
            # frequently only near airport departure for live changes.
            cache_age = (timedelta(minutes=15)
                         if target - now <= timedelta(hours=12)
                         else timedelta(hours=3))
            if cached and now - cached[0] < cache_age:
                return cached[1]
            result = await get_entur_journey(
                self.hass, transit_coords, coords, target,
                preferred_mode=mode if mode in ("bus", "train") else None,
            )
            self._entur_cache[cache_key] = (now, result)
            return result

        routes = await asyncio.gather(*(transit_for(w) for w in windows))
        suggestions = []
        for window, transit in zip(windows, routes):
            target = parse_datetime(window["airport_arrival_target"])
            drive = ({
                "leave_at": driving_leave_at(target, drive_minutes, buffer),
                "travel_minutes": drive_minutes,
                "parking_buffer_minutes": buffer,
                "source": "Waze (live traffic, not a future forecast)",
            } if drive_enabled and drive_minutes is not None else None)
            suggestions.append({
                **window, "drive": drive,
                "public_transport": transit,
                "transport_available": drive is not None or transit is not None,
                "airport_margin_text": minutes_text(window["margin_minutes"]),
                "drive_leave_at": (drive or {}).get("leave_at"),
                "public_leave_at": (transit or {}).get("leave_at"),
                "public_duration_text": (
                    minutes_text(transit["minutes"]) if transit else None
                ),
            })
        data["suggestions"] = suggestions
        # Legacy attributes and existing dashboard templates use recommendation #2.
        standard = next((v for v in suggestions if v["number"] == 2), None)
        data["drive_leave_at"] = (standard.get("drive") or {}).get("leave_at") if standard else None
        data["public_transport"] = standard.get("public_transport") if standard else None
        data["transit_duration_minutes"] = (data["public_transport"] or {}).get("minutes")
        data["transit_duration_text"] = (
            minutes_text(data["transit_duration_minutes"])
            if data["transit_duration_minutes"] is not None else None
        )
        if not transit_enabled:
            data["public_transport_status"] = "not_requested"
        elif transit_coords is None:
            data["public_transport_status"] = "address_coordinates_required"
        elif arrival - now > timedelta(days=3):
            data["public_transport_status"] = "too_early_for_timetable"
        elif data["public_transport"]:
            data["public_transport_status"] = "available"
        else:
            data["public_transport_status"] = "no_route_available"
        data["transport_status"] = (
            "mode_not_supported_yet" if mode in ("walk", "bike")
            else "available" if data["drive_minutes"] is not None
            or data["public_transport"] is not None else "waiting_for_transport"
        )
        if mode in ("walk", "bike"):
            data["reason"] = (
                "Gange og sykling kan velges, men ruteberegning for disse "
                "transportmåtene er ennå ikke lagt til."
            )
        elif mode == "taxi":
            data["reason"] = (
                "Taxi bruker Waze-kjøretid. Venting på taxi og påstigning er ikke inkludert."
            )
        if address and not has_manual_coords and transit_enabled:
            data["reason"] = "Transit needs address coordinates; Waze can use the address text."
        elif drive_enabled and drive_minutes is None and transit_enabled and all(not x for x in routes):
            data["reason"] = "Foreløpig ingen bekreftede kjøretider eller kollektivavganger."

        # Fire departure reminder events for suggestion #2 only.
        for transport_mode, departure in (
            ("drive", data.get("drive_leave_at")),
            ("public_transport", (data.get("public_transport") or {}).get("leave_at")),
        ):
            when = parse_datetime(departure)
            if when is None:
                continue
            minutes_left = (when - now).total_seconds() / 60
            phase = "soon" if 0 < minutes_left <= 20 else (
                "now" if -3 <= minutes_left <= 0 else None)
            token = f"{uid}:{transport_mode}:{phase}"
            if phase and token not in self._alerts:
                self._alerts.add(token)
                self.hass.bus.async_fire(f"{DOMAIN}_leave_reminder", {
                    "flight_id": uid, "flight_number": flight["flight_number"],
                    "mode": transport_mode, "phase": phase, "leave_at": departure,
                })
