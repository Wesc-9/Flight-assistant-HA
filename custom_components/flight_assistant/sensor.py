"""Next flight and per-flight sensors."""
from __future__ import annotations

from datetime import timedelta
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from .planning import time_progress_percent
from .parsing import parse_datetime
from .trip_setup import airline_display

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
            [
                PendingBookingSensor(coordinator, booking),
                BookingCountdownSensor(coordinator, booking),
                *(BookingTransportSensor(coordinator, booking, kind)
                  for kind in ("drive_minutes", "public_minutes",
                               "drive_leave_at", "public_leave_at")),
            ],
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
        for booking_id, item in self.coordinator.booking_records.items():
            for record in item.get("segments", []):
                if record.get("status") != "found":
                    continue
                flight = record.get("flight")
                if flight:
                    future.append((parse_datetime(flight.get("scheduled")), {
                        **flight, "_booking_id": booking_id,
                    }))
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
        transport = (
            self.coordinator.booking_records.get(
                flight.get("_booking_id"), {}
            ).get("transport", {})
            if flight.get("_booking_id")
            else self.coordinator.transport.get(flight.get("id"), {})
        )
        return {
            **flight, **transport,
            "count": len(self.coordinator.flights),
            "minutes_to_departure": (round((departure - dt_util.utcnow()).total_seconds() / 60)
                                     if departure else None),
            "airborne": airborne,
            "estimated_journey_progress_percent": (
                time_progress_percent(departure, arrival, dt_util.utcnow())
                if airborne and departure and arrival else None
            ),
            "aircraft_live_position_available": False,
            "attribution": (
                "AeroDataBox (RapidAPI), Avinor" if flight.get("source") == "AeroDataBox (RapidAPI)"
                else "Flydata fra Avinor – https://www.avinor.no"
            ),
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
        if self._booking.data.get("registration_mode") == "pnr_pending":
            return "Venter på PNR-tilgang"
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
            "airline": airline_display(d.get("airline", "")),
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
            "data_source": sorted({
                x["flight"].get("source")
                for x in resolved if x.get("status") == "found" and x.get("flight")
                and x["flight"].get("source")
            }),
            "attribution": "Flight schedules: AeroDataBox (RapidAPI); Norwegian operational data: Avinor.",
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
            parse_datetime((seg.get("flight") or {}).get("scheduled"))
            for seg in record.get("segments", [])
            if seg.get("status") == "found"
        ]
        upcoming = [d for d in upcoming if d and d >= now]
        return round((min(upcoming) - now).total_seconds() / 60) if upcoming else None



class BookingTransportSensor(CoordinatorEntity, SensorEntity):
    """Four dashboard-friendly values for each named flight booking."""

    _attr_has_entity_name = True
    _KINDS = {
        "drive_minutes": ("Kjøretid til flyplassen", "mdi:car-clock"),
        "public_minutes": ("Kollektiv reisetid", "mdi:train-car"),
        "drive_leave_at": ("Anbefalt avreise med bil", "mdi:car-arrow-right"),
        "public_leave_at": ("Anbefalt avreise med kollektiv", "mdi:bus-clock"),
    }

    def __init__(self, coordinator, booking, kind):
        super().__init__(coordinator)
        self._booking = booking
        self.kind = kind
        self._attr_unique_id = (
            f"flight_assistant_booking_{booking.subentry_id}_{kind}"
        )
        self._attr_name, self._attr_icon = self._KINDS[kind]
        if kind.endswith("_minutes"):
            self._attr_native_unit_of_measurement = "min"
        else:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"booking_{booking.subentry_id}")},
            name=booking.title,
            manufacturer="Flight Assistant",
            model="Travel itinerary",
        )

    @property
    def _travel(self):
        return self.coordinator.booking_records.get(
            self._booking.subentry_id, {}
        ).get("transport", {})

    @property
    def native_value(self):
        travel = self._travel
        public = travel.get("public_transport") or {}
        if self.kind == "drive_minutes":
            value = travel.get("drive_minutes")
            return round(value) if value is not None else None
        if self.kind == "public_minutes":
            return public.get("minutes")
        if self.kind == "drive_leave_at":
            return parse_datetime(travel.get("drive_leave_at"))
        if self.kind == "public_leave_at":
            return parse_datetime(public.get("leave_at"))
        return None

    @property
    def extra_state_attributes(self):
        """Expose timings and statuses, not booking credentials."""
        t = self._travel
        attributes = {
            "airport_arrival_target": t.get("arrive_airport_by"),
            "updated_at": t.get("updated_at"),
            "transport_status": t.get("transport_status"),
            "note": t.get("reason"),
        }
        if self.kind.startswith("drive"):
            attributes.update({
                "source": t.get("driving_source"),
                "status": t.get("drive_status"),
                "duration": t.get("drive_duration_text"),
            })
        else:
            public = t.get("public_transport") or {}
            attributes.update({
                "source": public.get("source"),
                "status": t.get("public_transport_status"),
                "duration": t.get("transit_duration_text"),
                "arrive_at": public.get("arrive_at"),
                "legs": public.get("legs", []),
            })
        return attributes
