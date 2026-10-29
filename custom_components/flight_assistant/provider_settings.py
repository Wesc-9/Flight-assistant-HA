"""Select one flight data source at integration level (never per booking).

The provider key belongs to the parent Home Assistant config entry. Each
booking subentry only contains itinerary and user travel preferences.
"""
SOURCE_AUTO = "auto"
SOURCE_AVINOR = "avinor"
SOURCE_AERODATABOX = "aerodatabox"
VALID_SOURCES = {SOURCE_AUTO, SOURCE_AVINOR, SOURCE_AERODATABOX}

def effective_aerodatabox_key(source: str | None, key: str | None) -> str:
    """Avinor-only mode disables RapidAPI calls even if a key is stored."""
    selected = source or SOURCE_AUTO  # Backward compatibility for v0.1.4
    if selected not in VALID_SOURCES:
        return ""
    if selected == SOURCE_AVINOR:
        return ""
    return (key or "").strip()

def source_needs_key(source: str | None) -> bool:
    return source == SOURCE_AERODATABOX
