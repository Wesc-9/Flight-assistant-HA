"""Constants for Flight Assistant."""
DOMAIN = "flight_assistant"
PLATFORMS = ["sensor"]
STORAGE_VERSION = 1
UPDATE_INTERVAL_SECONDS = 180
DEFAULT_MARGIN_MINUTES = 120
DEFAULT_EXTRA_DRIVE_MINUTES = 15
ENTUR_URL = "https://api.entur.io/journey-planner/v3/graphql"
AVINOR_URL = "https://asrv.avinor.no/XmlFeed/v1.0"
# The coordinate is the airport terminal vicinity, not a passenger drop-off lane.
AIRPORTS = {
    "TRD": (63.4576, 10.9243), "OSL": (60.1939, 11.1004),
    "BGO": (60.2934, 5.2181), "SVG": (58.8768, 5.6378),
    "TOS": (69.6833, 18.9189), "BOO": (67.2692, 14.3653),
    "AES": (62.5625, 6.1197), "KRS": (58.2042, 8.0854),
    "EVE": (68.4913, 16.6781), "ALF": (69.9761, 23.3717),
    "LYR": (78.2461, 15.4656), "HAU": (59.3453, 5.2084),
    "MOL": (62.7447, 7.2625), "KSU": (63.1118, 7.8245),
    "BDU": (69.0558, 18.5404), "KKN": (69.7258, 29.8913),
    "TRF": (59.1867, 10.2586)
}
