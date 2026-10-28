# Flight Assistant for Home Assistant

[![Åpne Flight Assistant i HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wesc-9&repository=Flight-assistant-HA&category=integration)

**[Åpne Flight Assistant direkte i HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=Wesc-9&repository=Flight-assistant-HA&category=integration)**

**Current version: v0.1.1** (first release: v0.1.0) — one Home Assistant device per saved trip, three arrival-time travel proposals and per-trip travel preferences. Airline PNR lookup and actual position tracking are not yet implemented.

An early, installable Home Assistant custom integration for following SAS and Norwegian flights, including **Avinor flight status**, **gate and check-in area** (when supplied), **Entur public transport suggestions** and optional **Waze driving departure suggestions**.

> **Important:** Version 0.1.1 cannot retrieve an airline booking using only a PNR/booking reference and surname. SAS/Norwegian do not provide a verified, openly accessible passenger-booking API for this use. Do **not** enter PNRs, surnames or passwords in Home Assistant actions. No fabricated booking lookup, scraping of private endpoints or credential collection is included. The PNR-first workflow is a later development goal, contingent on an authorized method.

## Add named booking devices (v0.1.1)

You should configure Flight Assistant **once**. To add a second or subsequent trip, open **Settings → Devices & services → Flight Assistant → Add entry / Add trip**, *not* "Add integration".

Each trip asks for a custom name, airline (SAS/Norwegian), booking reference (PNR), surname, domestic/international, bag, transport mode (car/public transport/both/walking/cycling), airport arrival margin and optional Norwegian departure address. The new flow confirms an address from **Entur Geocoder v3** and lets you save the trip as a pending Home Assistant subentry with a separate device and booking-status sensor.

**Caution:** PNR lookups are not connected to SAS or Norwegian in v0.1.1. A saved booking does not prove the airline reservation exists, and no dates, Avinor feed or transport itinerary can be discovered using PNR alone yet. Until an authorized PNR lookup exists, only manually entered flight numbers (Developer Tools action) can receive Avinor updates.

Booking references and surnames are stored locally in HA's config-entry storage **without extra encryption**, never as sensor attributes, notifications or logs. Protect admin access and backups. The booking flow explicitly warns and asks for confirmation before saving.

The main integration setup has been improved: enter a Norwegian street address or leave blank for Home Assistant's Home location, and use the numeric minute sliders. After address lookup, selected times are also summarized as **60 min = 1 t**, **90 min = 1 t 30 min**. Dynamically formatting the slider thumb itself requires a custom frontend and is not yet implemented.

Walking/cycling routing remains unimplemented; these options only record a transport preference.

## Releases and updates

**First release: [v0.1.0](https://github.com/Wesc-9/Flight-assistant-HA/releases/tag/v0.1.0).** This is a first-test release, not a completed automatic PNR booking assistant. Future updates will be published as new GitHub Releases with tags matching `custom_components/flight_assistant/manifest.json`, for example `v0.1.1`, `v0.1.2`.

HACS can recognize these published release tags and show updates. If Flight Assistant was previously installed from the default branch before the first release existed, manually use **Redownload** in HACS and select **v0.1.0** to switch to versioned releases. Read the GitHub release notes before upgrading. Restart Home Assistant after each update.

## Installation (Home Assistant OS)

**Enkel installasjon:** Klikk på knappen **Åpne i HACS** øverst på denne siden. Velg Home Assistant-instansen din, åpne Flight Assistant i HACS og trykk **Last ned**. Start Home Assistant på nytt, og gå til **Innstillinger → Enheter og tjenester → Legg til integrasjon → Flight Assistant**.

> Knappen åpner integrasjonen i din egen HACS-installasjon. Den installerer ikke kode automatisk. Du må ha HACS installert, og bekrefte nedlastingen selv. Dersom knappen ikke fungerer, følg de manuelle stegene nedenfor.

### Manuell installasjon

1. HACS → three dots → **Custom repositories** → `https://github.com/Wesc-9/Flight-assistant-HA` → **Integration**.
2. Install **Flight Assistant** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **Flight Assistant**.
4. Confirm home coordinates, target time at the airport (default 120 min before scheduled departure), and driving buffer (15 min).
5. Developer tools → Actions → `flight_assistant.add_flight`:

```yaml
action: flight_assistant.add_flight
data:
  flight_number: SK345
  origin: TRD
  destination: OSL
  scheduled: "2026-11-02T12:30:00+01:00"
```

You may specify per-trip preferences. For instance:
```yaml
action: flight_assistant.add_flight
data:
  flight_number: SK345
  origin: TRD
  destination: OSL
  scheduled: "2026-11-02T12:30:00+01:00"
  arrival_scheduled: "2026-11-02T13:30:00+01:00"
  journey_type: domestic
  checked_bag: true
  airport_margin_minutes: 90
  transport_mode: both
  departure_address: "YOUR ADDRESS"
  departure_latitude: 63.4
  departure_longitude: 10.4
```

The exact values above are illustrative, not a real trip or a verified address. When using a custom address, enter real corresponding coordinates for Entur. Without coordinates, Waze may accept the address, but public transport results are unavailable; we will implement address search later.

**Default airport arrival targets:** 90 min before departure for domestic, 120 min for international. Both are user-adjustable in 15-minute steps between 45 and 240 min. Check actual airline deadlines separately; these are not guarantees. Baggage options affect how conservatively a later suggestion is displayed. All suggestions are anchored to the original scheduled flight time.

The **scheduled** value must be an actual departure from your ticket, with its local UTC offset; example values above are illustrative, not an actual timetable. An origin IATA code is required to query Avinor.

### Entities

- `sensor.flight_assistant_next_flight` — next flight, flight status, gate, check-in area, planned/updated times and suggested departures in its attributes.
- One device per saved flight with a flight status sensor and a countdown sensor. Each device is linked to the integration. The `id` attribute identifies the flight for removal.

To remove a trip:
```yaml
action: flight_assistant.remove_flight
data:
  flight_id: "COPY_ID_FROM_SENSOR"
```

## Travel-time recommendations

- **Avinor:** queries the documented public XML feed for supported Norwegian airports once every 3 minutes while a flight is within 24 hours before or 6 hours after its scheduled departure. Outside this period, a saved flight stays planned but live data is not available. Gate, destination and check-in area are provided only when Avinor has them. Avinor data are best-effort and not guaranteed.
- **Entur:** suggests a latest-departing public transport itinerary arriving by the airport target, using real-time journey planning. Only queried within three days of the airport target. The actual stop and transfer details appear in `public_transport.legs`.
- **Waze:** install the **Waze Travel Time** built-in HA integration to calculate live driving duration via its `get_travel_times` action. Only queries within 12 hours of a flight because **current** traffic cannot be represented as a reliable long-term forecast; caches for 10 minutes.
- **Calculated time:** scheduled departure – airport margin – travel minutes – additional car/parking buffer. A published flight delay never silently reduces the airport arrival margin, as bag drop and security deadlines may still depend on original schedule.
- **Unknown data:** a missing provider integration, unknown airport coordinate, unavailable feed or no itinerary yields an empty suggestion rather than an invented time. Torp (TRF) is not part of Avinor's feed.

### Airports with travel-coordinate support

TRD, OSL, BGO, SVG, TOS, BOO, AES, KRS, EVE, ALF, LYR, HAU, MOL, KSU, BDU, KKN and TRF. Other Avinor airports still work for flight status; transport suggestions require a terminal coordinate to be added in `const.py`. Coordinates are approximate terminals, not precise car drop-off points.

### Three suggestions per booking

`travel_suggestions` is exposed as the `suggestions` attribute of each flight sensor. Arrival windows are **good time** (30 minutes earlier than selected), **recommended** (exact selected margin) and **later** (30 minutes later if a conservative margin floor is satisfied). Each contains a Waze-derived driving time and/or a validated Entur itinerary when available; unsupported means `null`, never a fake departure. Only recommendation #2 is used for leave reminders. A third suggestion may be absent.

The current integration is managed as one configured Flight Assistant instance, while every manually saved flight has its own Home Assistant device. A per-device **Add booking reference + surname** wizard and airline itinerary resolver are pending authorized airline access; it would be unsafe and misleading to invent the returned booking.

Flight progress is an optional **schedule-based estimated percentage** if actual departed status and a manually provided arrival schedule exist. It is **not aircraft GPS location**. A later flight tracker integration is required before showing a plane moving on a real map.

### Home Assistant events

Use native HA automations to send phone notifications when these events occur:

- `flight_assistant_changed` — fields changed based on Avinor (flight id/number, changed field names).
- `flight_assistant_leave_reminder` — `mode` = `drive` or `public_transport`, `phase` = `soon` (0–20 min) or `now` (up to 3 min late), `leave_at` ISO time.

Example automation, adjust mobile notification action to match your phone:
```yaml
alias: Flight Assistant - leave reminder
triggers:
  - trigger: event
    event_type: flight_assistant_leave_reminder
actions:
  - action: notify.mobile_app_MY_PHONE
    data:
      title: "Flight Assistant"
      message: >
        {{ trigger.event.data.flight_number }}:
        {{ 'Bil' if trigger.event.data.mode == 'drive' else 'Kollektiv' }}
        – avreise {{ as_timestamp(trigger.event.data.leave_at) | timestamp_custom('%H:%M', true) }}
mode: queued
```

Events fire only while Home Assistant is running; this release does not guarantee a notification if HA is restarted at the exact trigger time. Flight details persist in Home Assistant's local `.storage` directory; no PNR, surname or authentication secrets are saved.

## Dashboard card

A built-in Markdown card example is available in [examples/next-flight-card.yaml](examples/next-flight-card.yaml). Paste it into the dashboard's manual card editor; it shows the next flight, countdown, suggested travel alternatives and a clearly labelled schedule-based progress bar after departure (only when departure status and arrival schedule exist). It does not claim to show live aircraft GPS.

The definitive product design and outstanding phases are recorded in [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md).

## Data sources and limitations

- [Avinor: flight-data and required attribution](https://partner.avinor.no/en/services/flight-data/): **[Flydata fra Avinor](https://www.avinor.no)**. Published codes and scheduled timestamps are UTC; Home Assistant displays ISO timestamps with offsets.
- [Entur Journey Planner v3](https://developer.entur.no/docs/open-services/journey-planner): requires `ET-Client-Name` and provides public transport itineraries in Norway.
- [Waze Travel Time action](https://www.home-assistant.io/actions/waze_travel_time.get_travel_times/): optional integration.
- **Not in 0.1.0:** PNR/surname lookup, personal check-in completion status, boarding passes, automatic email importing, international non-Avinor airports, real-time airborne position, multi-segment automatic import and a custom dashboard card.

This is a first implementation. API interoperability has not yet been validated against a live Home Assistant installation or a real passenger booking. Please report issues in this repository. Do not rely on suggestions as your only source when catching a flight.
