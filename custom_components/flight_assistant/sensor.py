"""Next flight and per-flight sensors."""
from __future__ import annotations

from datetime import timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from .planning import time_progress_percent
from .parsing import parse_datetime

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data["flight_assistant"][entry.entry_id]
    active: dict[str, FlightSensor] = {}

    def add_new():
        additions = []
        for uid in coordinator.flights:
            if uid not in active:
                flight_sensor = FlightSensor(coordinator, uid)
                countdown = FlightCountdownSensor(coordinator, uid)
                active[uid] = (flight_sensor, countdown)
                additions.extend([flight_sensor, countdown])
        for uid in list(active):
            if uid not in coordinator.flights:
                for sensor in active.pop(uid):
                    hass.async_create_task(sensor.async_remove())
        if additions:
            async_add_entities(additions)

    async_add_entities([NextFlightSensor(coordinator)])
    add_new()
    for booking in entry.get_subentries_of_type("booking"):
        async_add_entities(
            [PendingBookingSensor(coordinator, booking), BookingCountdownSensor(coordinator, booking)],
            config_subentry_id=booking.subentry_id
        )
    entry.async_on_unload(coordinator.async_add_listener(add_new))

class FlightSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:airplane"

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{uid}"
        record = coordinator.flights.get(uid, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            name=f"{record.get('flight_number', 'Flight')} {record.get('origin', '')} travel",
            manufacturer="Flight Assistant",
            model="Flight itinerary",
        )

    @property
    def name(self):
        item = self.coordinator.flights.get(self.uid, {})
        return f"Flight Assistant {item.get('flight_number', 'Flight')} {item.get('origin', '')}"

    @property
    def native_value(self):
        item = self.coordinator.flights.get(self.uid)
        return item.get("status") if item else None

    @property
    def extra_state_attributes(self):
        item = self.coordinator.flights.get(self.uid, {})
        return {**item, **self.coordinator.transport.get(self.uid, {}),
                "attribution": "Flydata fra Avinor – https://www.avinor.no"}

class FlightCountdownSensor(FlightSensor):
    """Countdown in minutes, linked to the same HA flight device."""
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator, uid):
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{uid}_countdown"

    @property
    def name(self):
        return f"{super().name} minutes to departure"

    @property
    def native_value(self):
        record = self.coordinator.flights.get(self.uid, {})
        depart = parse_datetime(record.get("scheduled"))
        return round(max(0, (depart - dt_util.utcnow()).total_seconds()) / 60) if depart else None

    @property
    def extra_state_attributes(self):
        return {}

class NextFlightSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Flight Assistant Next Flight"
    _attr_icon = "mdi:airplane-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_next_flight"

    @property
    def _next(self):
        now = dt_util.utcnow()
        future = [
            (parse_datetime(flight.get("scheduled")), flight)
            for flight in self.coordinator.flights.values()
        ]
        for item in self.coordinator.booking_records.values():
            for record in item.get("segments", []):
                if record.get("status") != "found":
                    continue
                flight = record.get("flight")
                if flight:
                    future.append((parse_datetime(flight.get("scheduled")), flight))
        future = [
            (when, flight) for when, flight in future
            if when and when >= now - timedelta(hours=6)
        ]
        return min(future, key=lambda x: x[0])[1] if future else None

    @property
    def native_value(self):
        flight = self._next
        return flight.get("flight_number") if flight else "No upcoming flights"

    @property
    def extra_state_attributes(self):
        flight = self._next
        if not flight:
            return {"count": len(self.coordinator.flights)}
        arrival = parse_datetime(flight.get("arrival_scheduled"))
        departure = parse_datetime(flight.get("scheduled"))
        airborne = flight.get("status") == "Departed"
        return {
            **flight, **self.coordinator.transport.get(flight.get("id"), {}),
            "count": len(self.coordinator.flights),
            "minutes_to_departure": (round((departure - dt_util.utcnow()).total_seconds() / 60)
                                     if departure else None),
            "airborne": airborne,
            "estimated_journey_progress_percent": (
                time_progress_percent(departure, arrival, dt_util.utcnow())
                if airborne and departure and arrival else None
            ),
            "aircraft_live_position_available": False,
            "attribution": "Flydata fra Avinor – https://www.avinor.no",
        }


class PendingBookingSensor(CoordinatorEntity, SensorEntity):
    """One per named booking; no airline data has been retrieved yet."""
    _attr_icon = "mdi:ticket-confirmation-outline"
    _attr_has_entity_name = True
    _attr_name = "Bookingstatus"

    def __init__(self, coordinator, booking):
        super().__init__(coordinator)
        self._booking = booking
        self._attr_unique_id = f"flight_assistant_booking_{booking.subentry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"booking_{booking.subentry_id}")},
            name=booking.title,
            manufacturer="Flight Assistant",
            model="Airline reservation - pending lookup",
        )

    @property
    def native_value(self):
        data = self.coordinator.booking_records.get(self._booking.subentry_id, {})
        if data.get("status") == "resolved":
            return "Flyreise funnet"
        if data.get("status") == "partial":
            return "Delvis funnet"
        return "Venter på flyoppslag"

    @property
    def extra_state_attributes(self):
        """Never publish passenger PNR, surname or API key."""
        from .display import minutes_text
        d = self._booking.data
        record = self.coordinator.booking_records.get(self._booking.subentry_id, {})
        resolved = record.get("segments") or []
        return {
            "trip_name": self._booking.title,
            "airline": "SAS" if d.get("airline") == "sas" else "Norwegian",
            "booking_verified": False,
            "flight_details_available": any(x.get("status") == "found" for x in resolved),
            "flight_tracking_available": False,
            "transport_mode": d.get("transport_mode", "both"),
            "checked_bag": d.get("checked_bag", False),
            "airport_margin": minutes_text(d.get("airport_margin_minutes", 90)),
            "origin_address": d.get("resolved_origin_address", "Home"),
            "segments": resolved,
            "travel_suggestions": record.get("transport", {}).get("suggestions", []),
            "drive_leave_at": record.get("transport", {}).get("drive_leave_at"),
            "public_transport": record.get("transport", {}).get("public_transport"),
            "data_source": "AeroDataBox/Avinor" if resolved else None,
            "note": record.get("note", "Avventer gyldige flydata."),
        }


class BookingCountdownSensor(CoordinatorEntity, SensorEntity):
    """Countdown for the first resolved leg of a named booking."""
    _attr_icon = "mdi:timer-outline"
    _attr_has_entity_name = True
    _attr_name = "Minutter til neste avgang"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator, booking):
        super().__init__(coordinator)
        self._booking = booking
        self._attr_unique_id = f"flight_assistant_booking_{booking.subentry_id}_countdown"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"booking_{booking.subentry_id}")},
            name=booking.title, manufacturer="Flight Assistant",
            model="Travel itinerary",
        )

    @property
    def native_value(self):
        record = self.coordinator.booking_records.get(self._booking.subentry_id, {})
        now = dt_util.utcnow()
        upcoming = [
            parse_datetime(seg.get("scheduled"))
            for seg in record.get("segments", [])
            if seg.get("status") == "found"
        ]
        upcoming = [d for d in upcoming if d and d >= now]
        return round((min(upcoming) - now).total_seconds() / 60) if upcoming else None
