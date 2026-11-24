"""Constants and configuration keys of the RiPlay provider for Music Assistant."""

DOMAIN = "riplay"
NAME = "RiPlay"
VERSION = "0.2.0"

# Config entry keys (declared in get_config_entries, texts in strings.json)
CONF_BRIDGE_HOST = "bridge_host"
CONF_BRIDGE_PORT = "bridge_port"
CONF_API_TOKEN = "api_token"

# Default: the port of RiPlay's streaming/relay server, which hosts the bridge too
DEFAULT_BRIDGE_PORT = 8765

# HTTP timeout towards the bridge (LAN: a few seconds are enough)
HTTP_TIMEOUT_SECONDS = 15

# Pagination: Music Assistant walks the async generators until they are exhausted
PAGE_LIMIT = 500

# Mapping of the bridge mime types -> ContentType of music-assistant-models
MIME_TO_CONTENT_TYPE = {
    "audio/mpeg": "MP3",
    "audio/mp3": "MP3",
    "audio/flac": "FLAC",
    "audio/mp4": "M4A",
    "audio/m4a": "M4A",
    "audio/aac": "AAC",
    "audio/ogg": "VORBIS",
    "audio/opus": "OPUS",
    "audio/vorbis": "VORBIS",
    "audio/wav": "WAV",
    "audio/x-wav": "WAV",
    "audio/aiff": "AIFF",
    "audio/alac": "ALAC",
    "audio/wma": "WMAV2",
    "audio/dsf": "DSF",
    "audio/x-ape": "APE",
    "audio/wavpack": "WAVPACK",
}
