# Flight Assistant for Home Assistant

An early, installable Home Assistant custom integration for following SAS and Norwegian flights, including **Avinor flight status**, **gate and check-in area** (when supplied), **Entur public transport suggestions** and optional **Waze driving departure suggestions**.

> **Important:** Version 0.1.0 cannot retrieve an airline booking using only a PNR/booking reference and surname. SAS/Norwegian do not provide a verified, openly accessible passenger-booking API for this use. Do **not** enter PNRs, surnames or passwords in Home Assistant actions. No fabricated booking lookup, scraping of private endpoints or credential collection is included. The PNR-first workflow is a later development goal, contingent on an authorized method.

## Installation (Home Assistant OS)

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

The **scheduled** value must be an actual departure from your ticket, with its local UTC offset; example values above are illustrative, not an actual timetable. An origin IATA code is required to query Avinor.

### Entities

- `sensor.flight_assistant_next_flight` — next flight, flight status, gate, check-in area, planned/updated times and suggested departures in its attributes.
- One sensor for each saved flight. The `id` attribute identifies the flight for removal.

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

## Data sources and limitations

- [Avinor: flight-data and required attribution](https://partner.avinor.no/en/services/flight-data/): **[Flydata fra Avinor](https://www.avinor.no)**. Published codes and scheduled timestamps are UTC; Home Assistant displays ISO timestamps with offsets.
- [Entur Journey Planner v3](https://developer.entur.no/docs/open-services/journey-planner): requires `ET-Client-Name` and provides public transport itineraries in Norway.
- [Waze Travel Time action](https://www.home-assistant.io/actions/waze_travel_time.get_travel_times/): optional integration.
- **Not in 0.1.0:** PNR/surname lookup, personal check-in completion status, boarding passes, automatic email importing, international non-Avinor airports, real-time airborne position, multi-segment automatic import and a custom dashboard card.

This is a first implementation. API interoperability has not yet been validated against a live Home Assistant installation or a real passenger booking. Please report issues in this repository. Do not rely on suggestions as your only source when catching a flight.
