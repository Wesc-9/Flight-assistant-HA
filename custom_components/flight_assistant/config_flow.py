"""Flight Assistant config and additional bookings as Home Assistant subentries.

Important: booking references are not queried until an approved airline integration
is available. They must never be put in entity attributes, service responses or logs.
"""
from __future__ import annotations
import re
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .address import AddressError, lookup
from .const import DOMAIN, DEFAULT_EXTRA_DRIVE_MINUTES
from .display import minutes_text
from .parsing import normalize_flight, normalize_airport
from datetime import date

def text_field(password=False):
    return selector.TextSelector(selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD if password else selector.TextSelectorType.TEXT
    ))

def slider(low, high, step):
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=low, max=high, step=step,
        mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="min",
    ))

def select(items):
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[selector.SelectOptionDict(value=k, label=v) for k, v in items],
        mode=selector.SelectSelectorMode.DROPDOWN,
    ))

MODES = [("both", "Sammenlign bil og kollektiv"),
         ("drive", "Bil"), ("transit", "Kollektiv"),
         ("walk", "Gå (rute kommer senere)"),
         ("bike", "Sykkel (rute kommer senere)")]
TYPES = [("domestic", "Innenlands"), ("international", "Utenlands")]
AIRLINES = [("sas", "SAS"), ("norwegian", "Norwegian")]
REGISTRATION = [("manual", "Flynummer og dato (anbefalt)"),
                ("pnr_pending", "PNR – lagre til oppslag støttes")]


def global_schema(values, include_provider=False):
    fields = {
        vol.Optional("home_address", default=values.get("home_address", "")): text_field(),
        vol.Required("airport_margin_minutes",
                     default=values.get("airport_margin_minutes", 90)): slider(45, 240, 15),
        vol.Required("drive_buffer_minutes",
                     default=values.get("drive_buffer_minutes", DEFAULT_EXTRA_DRIVE_MINUTES)):
            slider(0, 120, 5),
    }
    if include_provider:
        # Blank means keep stored key; do not echo a saved secret into the form.
        fields[vol.Optional("aerodatabox_api_key")] = text_field(password=True)
        fields[vol.Optional("clear_aerodatabox_key", default=False)] = selector.BooleanSelector()
    return vol.Schema(fields)

def booking_identity_schema(values):
    return vol.Schema({
        vol.Required("trip_name", default=values.get("trip_name", "")): text_field(),
        vol.Required("registration_mode",
                     default=values.get("registration_mode", "manual")): select(REGISTRATION),
        vol.Required("airline", default=values.get("airline", "sas")): select(AIRLINES),
        vol.Optional("booking_reference", default=values.get("booking_reference", "")):
            text_field(password=True),
        vol.Optional("surname", default=values.get("surname", "")):
            text_field(password=True),
        vol.Required("journey_type", default=values.get("journey_type", "domestic")):
            select(TYPES),
    })


def flight_schema(values):
    return vol.Schema({
        vol.Required("flight_number", default=values.get("flight_number", "")):
            text_field(),
        vol.Required("flight_date", default=values.get("flight_date", date.today().isoformat())):
            selector.DateSelector(),
        vol.Optional("departure_airport", default=values.get("departure_airport", "")):
            text_field(),
    })


def more_flights_schema():
    return vol.Schema({
        vol.Required("add_another_flight", default=False): selector.BooleanSelector(),
    })


def trip_travel_schema(values, margin):
    return vol.Schema({
        vol.Required("airport_margin_minutes",
                     default=values.get("airport_margin_minutes", margin)):
            slider(45, 240, 15),
        vol.Required("checked_bag", default=values.get("checked_bag", False)):
            selector.BooleanSelector(),
        vol.Required("transport_mode", default=values.get("transport_mode", "both")):
            select(MODES),
        vol.Optional("origin_address", default=values.get("origin_address", "")):
            text_field(),
    })

def match_schema(matches):
    return vol.Schema({
        vol.Required("address_match", default="0"):
            select([(str(i), item["label"]) for i, item in enumerate(matches)])
    })

class AddressSteps:
    """Shared address verification methods for main and options flows."""
    def _setup_address(self):
        self._pending = None
        self._matches = []

    async def _resolve_address(self, data, field):
        entered = (data.get(field) or "").strip()
        data[field] = entered
        if not entered:
            data["home_latitude"] = self.hass.config.latitude
            data["home_longitude"] = self.hass.config.longitude
            data["resolved_home_address"] = ""
            return data, False
        self._pending = data
        self._matches = await lookup(self.hass, entered)
        return data, True

    def _selected_address(self, choice, field="home_address"):
        selected = self._matches[int(choice["address_match"])]
        data = dict(self._pending)
        data["resolved_" + field] = selected["label"]
        if field == "home_address":
            data["home_latitude"] = selected["latitude"]
            data["home_longitude"] = selected["longitude"]
        else:
            data["departure_latitude"] = selected["latitude"]
            data["departure_longitude"] = selected["longitude"]
        return data

    def _show_address_choice(self, step):
        return self.async_show_form(
            step_id=step, data_schema=match_schema(self._matches),
            description_placeholders={
                "airport_margin": minutes_text(self._pending["airport_margin_minutes"]),
                "drive_buffer": minutes_text(self._pending.get("drive_buffer_minutes", 0)),
            },
        )

class FlightAssistantConfigFlow(AddressSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._setup_address()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        return {"booking": BookingSubentryFlow}

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            try:
                values, found = await self._resolve_address(dict(user_input), "home_address")
                if found:
                    return await self.async_step_confirm_address()
                return self.async_create_entry(title="Flight Assistant", data=values)
            except AddressError as exc:
                errors["base"] = str(exc)
        return self.async_show_form(
            step_id="user",
            data_schema=global_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_confirm_address(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title="Flight Assistant", data=self._selected_address(user_input)
            )
        return self._show_address_choice("confirm_address")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FlightAssistantOptionsFlow()

class FlightAssistantOptionsFlow(AddressSteps, config_entries.OptionsFlow):
    def __init__(self):
        self._setup_address()

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                values = dict(user_input)
                existing_key = self.config_entry.options.get("aerodatabox_api_key", "")
                key = (values.pop("aerodatabox_api_key", "") or "").strip()
                clear = values.pop("clear_aerodatabox_key", False)
                values["aerodatabox_api_key"] = "" if clear else (key or existing_key)
                values, found = await self._resolve_address(values, "home_address")
                if found:
                    return await self.async_step_confirm_address()
                return self.async_create_entry(title="", data=values)
            except AddressError as exc:
                errors["base"] = str(exc)
        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=global_schema(user_input or defaults, include_provider=True),
            errors=errors,
        )

    async def async_step_confirm_address(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=self._selected_address(user_input))
        return self._show_address_choice("confirm_address")

class BookingSubentryFlow(AddressSteps, config_entries.ConfigSubentryFlow):
    """Add a named pending booking to the existing Flight Assistant instance."""
    def __init__(self):
        self._setup_address()
        self._identity = {}
        self._values = {}
        self._segments = []

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            name = (user_input.get("trip_name") or "").strip()
            pnr = re.sub(r"\s+", "", user_input.get("booking_reference") or "").upper()
            surname = (user_input.get("surname") or "").strip()
            mode = user_input.get("registration_mode", "manual")
            if not (1 <= len(name) <= 80):
                errors["trip_name"] = "invalid_name"
            elif (pnr and not surname) or (surname and not pnr) or (
                mode == "pnr_pending" and not pnr
            ):
                errors["booking_reference"] = "booking_pair_required"
            elif pnr and not re.fullmatch(r"[A-Z0-9]{5,8}", pnr):
                errors["booking_reference"] = "invalid_booking_reference"
            elif len(surname) > 100:
                errors["surname"] = "invalid_surname"
            else:
                self._identity = {**user_input, "trip_name": name,
                                  "booking_reference": pnr, "surname": surname}
                if mode == "manual":
                    return await self.async_step_flight()
                return await self.async_step_travel()
        return self.async_show_form(
            step_id="user",
            data_schema=booking_identity_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_flight(self, user_input=None):
        """Enter a flight number/date, optionally supplying origin to disambiguate."""
        errors = {}
        if user_input is not None:
            try:
                number = normalize_flight(user_input["flight_number"])
                travel_date = date.fromisoformat(user_input["flight_date"])
                origin = (user_input.get("departure_airport") or "").strip()
                if origin:
                    origin = normalize_airport(origin)
                segment = {
                    "flight_number": number,
                    "flight_date": travel_date.isoformat(),
                    "departure_airport": origin or None,
                }
                if segment in self._segments:
                    errors["base"] = "duplicate_flight"
                elif len(self._segments) >= 8:
                    errors["base"] = "too_many_flights"
                else:
                    self._segments.append(segment)
                    return await self.async_step_more_flights()
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_flight"
        return self.async_show_form(
            step_id="flight",
            data_schema=flight_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_more_flights(self, user_input=None):
        """Add stopover legs, one at a time; do not invent connecting flights."""
        if user_input is not None:
            if user_input.get("add_another_flight"):
                if len(self._segments) >= 8:
                    return await self.async_step_travel()
                return await self.async_step_flight()
            return await self.async_step_travel()
        return self.async_show_form(
            step_id="more_flights",
            data_schema=more_flights_schema(),
            description_placeholders={
                "segments": ", ".join(
                    f"{x['flight_number']} ({x['flight_date']})"
                    for x in self._segments
                ),
            },
        )

    async def async_step_travel(self, user_input=None):
        errors = {}
        if user_input is not None:
            data = {**self._identity, **user_input}
            if self._identity.get("registration_mode") == "manual":
                data["segments"] = list(self._segments)
            data["airport_margin_minutes"] = int(data["airport_margin_minutes"])
            try:
                if not (45 <= data["airport_margin_minutes"] <= 240
                        and data["airport_margin_minutes"] % 15 == 0):
                    errors["airport_margin_minutes"] = "invalid_margin"
                elif len(str(data.get("origin_address") or "")) > 200:
                    errors["origin_address"] = "invalid_address"
                else:
                    if not (data.get("origin_address") or "").strip():
                        # Keep a per-booking origin: Home Assistant's Home is default.
                        # No per-trip override: coordinator uses the default
                        # origin from Flight Assistant configuration, which
                        # may be a street address different from HA Home.
                        data["departure_latitude"] = None
                        data["departure_longitude"] = None
                        data["resolved_origin_address"] = "Hjem / standardadresse"
                        self._values = data
                        return await self.async_step_confirm_booking()
                    self._pending = data
                    self._matches = await lookup(self.hass, data["origin_address"])
                    return await self.async_step_confirm_origin()
            except AddressError as exc:
                errors["base"] = str(exc)
        default_margin = 90 if self._identity.get("journey_type") == "domestic" else 120
        return self.async_show_form(
            step_id="travel",
            data_schema=trip_travel_schema(user_input or {}, default_margin),
            errors=errors,
        )

    async def async_step_confirm_origin(self, user_input=None):
        if user_input is not None:
            self._values = self._selected_address(user_input, field="origin_address")
            return await self.async_step_confirm_booking()
        return self._show_address_choice("confirm_origin")

    async def async_step_confirm_booking(self, user_input=None):
        if user_input is not None:
            if user_input.get("acknowledge_pending") is True:
                return self.async_create_entry(
                    title=self._values["trip_name"],
                    # Stored privately in HA config entries, not exposed on sensors.
                    # This does not perform an airline request.
                    data={
                        **{key: value for key, value in self._values.items()
                           if self._values.get("registration_mode") != "manual"
                           or key not in ("booking_reference", "surname")},
                        "booking_status": (
                            "pending_flight_lookup" if self._values.get("segments")
                            else "pending_airline_lookup"
                        ),
                    },
                )
            return self.async_show_form(
                step_id="confirm_booking",
                data_schema=vol.Schema({
                    vol.Required("acknowledge_pending", default=False):
                        selector.BooleanSelector()
                }),
                errors={"acknowledge_pending": "required"},
                description_placeholders=self._summary(),
            )
        return self.async_show_form(
            step_id="confirm_booking",
            data_schema=vol.Schema({
                vol.Required("acknowledge_pending", default=False):
                    selector.BooleanSelector()
            }),
            description_placeholders=self._summary(),
        )

    def _summary(self):
        return {
            "trip_name": self._values["trip_name"],
            "airline": dict(AIRLINES)[self._values["airline"]],
            "margin": minutes_text(self._values["airport_margin_minutes"]),
            "transport": dict(MODES)[self._values["transport_mode"]],
            "address": self._values.get("resolved_origin_address") or "Home",
            "segments": ", ".join(
                f"{x['flight_number']} ({x['flight_date']})"
                for x in self._values.get("segments", [])
            ) or "Ingen – PNR-oppslag er ikke tilgjengelig",
        }
