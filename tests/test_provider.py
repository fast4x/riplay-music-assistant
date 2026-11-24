"""End-to-end flow of the provider against a fake bridge.

The stub of the server base class is minimal on purpose: everything else (the
media items, the StreamDetails, the enums) is the REAL music-assistant-models
package, so the tests validate the mapping the way the server will see it.
The fake bridge replicates the Kotlin `riplay-bridge` module exactly: same
endpoints, same camelCase JSON, same Bearer token, same 401/404 behaviour.
"""

from __future__ import annotations

import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from tests import DOMAIN

TOKEN = "testtoken"
BRIDGE_VERSION = "0.1.0"
INSTANCE_ID = "riplay#1"

STREAM_URL_1 = "http://127.0.0.1:9999/relay/testtoken/s/abc123"

ARTIST_1 = {
    "id": "artist-1",
    "name": "Local Artists",
    "thumbnailUrl": "http://127.0.0.1:9999/art/artist1.jpg",
    "albumCount": 1,
    "trackCount": 2,
}

ALBUM_1 = {
    "id": "album-1",
    "name": "Local Album",
    "artistId": "artist-1",
    "artistName": "Local Artists",
    "year": 2024,
    "trackCount": 1,
    "thumbnailUrl": "http://127.0.0.1:9999/art/album1.jpg",
}

TRACK_1 = {
    "id": "local-1",
    "name": "Local Song",
    "artistId": "artist-1",
    "artistName": "Local Artists",
    "albumId": "album-1",
    "albumName": "Local Album",
    "durationSeconds": 212,
    "trackNumber": 1,
    "discNumber": 1,
    "year": 2024,
    "mimeType": "audio/flac",
    "sizeBytes": 41000000,
    "thumbnailUrl": "http://127.0.0.1:9999/art/track1.jpg",
    "directUrl": None,
    "servable": True,
}

TRACK_2 = {
    "id": "yt-1",
    "name": "YouTube-only Track",
    "artistId": "artist-1",
    "artistName": "Local Artists",
    "durationSeconds": 180,
    "servable": False,
}

PLAYLIST_1 = {"id": "1", "name": "Favourites", "trackCount": 2}

HEALTH = {
    "app": "RiPlay",
    "bridgeVersion": BRIDGE_VERSION,
    "artistCount": 1,
    "albumCount": 1,
    "trackCount": 2,
    "playlistCount": 1,
    "servableTrackCount": 1,
}


def track_detail(track_id: str, server_port: int) -> dict:
    """Replicate BridgeRoutes /tracks/{id} plus the relay URL resolver."""
    tracks = [TRACK_1, TRACK_2]
    track = next((t for t in tracks if t["id"] == track_id), None)
    if track is None:
        return {}
    stream_url, kind = None, None
    if track["servable"]:
        stream_url = f"http://127.0.0.1:{server_port}/relay/testtoken/s/{track_id}"
        kind = "relay"
    return {"track": track, "streamUrl": stream_url, "streamKind": kind}


class FakeBridgeHandler(BaseHTTPRequestHandler):
    """Fake bridge: same API, same status codes, same camelCase JSON."""

    server_port: int = 0  # set after bind

    def log_message(self, fmt, *args):  # keep the output clean
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        header = self.headers.get("Authorization", "")
        if header != f"Bearer {TOKEN}":
            self._json({"error": "unauthorized", "message": "Wrong token"}, 401)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        server_port = self.server_port

        def page(items: list) -> dict:
            offset = int(query.get("offset", ["0"])[0])
            limit = int(query.get("limit", ["500"])[0])
            window = items[offset : offset + limit]
            return {"items": window, "total": len(items), "offset": offset, "limit": limit}

        if path == "/api/v1/ping":
            return self._json({"ok": True, "app": "RiPlay", "bridgeVersion": BRIDGE_VERSION})

        if not self._check_auth():
            return

        if path == "/api/v1/health":
            return self._json(HEALTH)
        if path == "/api/v1/library/artists":
            return self._json(page([ARTIST_1]))
        if path == "/api/v1/library/albums":
            return self._json(page([ALBUM_1]))
        if path == "/api/v1/library/tracks":
            return self._json(page([TRACK_1, TRACK_2]))
        if path == "/api/v1/library/playlists":
            return self._json(page([PLAYLIST_1]))
        if path == "/api/v1/artists/artist-1":
            return self._json(ARTIST_1)
        if path == "/api/v1/artists/artist-1/albums":
            return self._json(page([ALBUM_1]))
        if path == "/api/v1/albums/album-1":
            return self._json(ALBUM_1)
        if path == "/api/v1/albums/album-1/tracks":
            return self._json(page([TRACK_1]))
        if path == "/api/v1/playlists/1":
            return self._json(PLAYLIST_1)
        if path == "/api/v1/playlists/1/tracks":
            return self._json(page([TRACK_1, TRACK_2]))
        if path.startswith("/api/v1/tracks/"):
            detail = track_detail(path.rsplit("/", 1)[-1], server_port)
            if not detail:
                return self._json({"error": "not_found"}, 404)
            return self._json(detail)
        if path == "/api/v1/search":
            q = query.get("q", [""])[0].lower()
            result = {
                "artists": [ARTIST_1] if q and q in "local artists" else [],
                "albums": [ALBUM_1] if q and q in "local album" else [],
                "tracks": [t for t in (TRACK_1, TRACK_2) if q and q in t["name"].lower()],
                "playlists": [PLAYLIST_1] if q and q in "favourites" else [],
            }
            return self._json(result)
        return self._json({"error": "not_found"}, 404)


class FakeConfig:
    """Stub of ProviderConfig with get_value."""

    def __init__(self, values: dict):
        self._values = values
        self.instance_id = INSTANCE_ID

    def get_value(self, key: str):
        return self._values.get(key)


def install_server_stub() -> None:
    """Register a minimal music_assistant.models.music_provider in sys.modules."""
    ma_pkg = types.ModuleType("music_assistant")
    ma_pkg.__path__ = []  # it is a package
    ma_models = types.ModuleType("music_assistant.models")
    ma_models.__path__ = []
    mp_module = types.ModuleType("music_assistant.models.music_provider")

    class Provider:
        def __init__(self, mass=None, manifest=None, config=None, supported_features=None):
            self.mass = mass
            self.manifest = manifest
            self.config = config
            self._supported_features = supported_features or set()

        @property
        def domain(self) -> str:
            return self.manifest.domain

        @property
        def name(self) -> str:
            return getattr(self.manifest, "name", DOMAIN)

        @property
        def instance_id(self) -> str:
            return getattr(self.config, "instance_id", "riplay#stub")

        @property
        def supported_features(self):
            return self._supported_features

    class MusicProvider(Provider):
        pass

    mp_module.Provider = Provider
    mp_module.MusicProvider = MusicProvider
    ma_models.music_provider = mp_module
    ma_pkg.models = ma_models
    sys.modules["music_assistant"] = ma_pkg
    sys.modules["music_assistant.models"] = ma_models
    sys.modules["music_assistant.models.music_provider"] = mp_module


install_server_stub()

from music_assistant_models.enums import (  # noqa: E402
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import (  # noqa: E402
    LoginFailed,
    MediaNotFoundError,
    ProviderUnavailableError,
)
from music_assistant_models.media_items import Track  # noqa: E402
from music_assistant_models.streamdetails import StreamDetails  # noqa: E402

from riplay import RiplayProvider, setup  # noqa: E402
from riplay.bridge_client import BridgeAuthError, BridgeConnectionError, RiplayBridgeClient  # noqa: E402


@pytest.fixture()
async def bridge():
    """Start the fake bridge on a free port and shut it down afterwards."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeBridgeHandler)
    port = httpd.server_address[1]
    FakeBridgeHandler.server_port = port
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port
    httpd.shutdown()


def make_provider(port: int, token: str = TOKEN, host: str = "127.0.0.1") -> RiplayProvider:
    return RiplayProvider(
        mass=None,
        manifest=SimpleNamespace(domain=DOMAIN, name="RiPlay"),
        config=FakeConfig(
            {"bridge_host": host, "bridge_port": port, "api_token": token}
        ),
        supported_features={
            ProviderFeature.BROWSE,
            ProviderFeature.SEARCH,
            ProviderFeature.LIBRARY_ARTISTS,
            ProviderFeature.LIBRARY_ALBUMS,
            ProviderFeature.LIBRARY_TRACKS,
            ProviderFeature.LIBRARY_PLAYLISTS,
            ProviderFeature.ARTIST_ALBUMS,
        },
    )


async def test_setup_module_function(bridge: int) -> None:
    """The module-level setup returns an initialized provider instance."""
    provider = await setup(None, SimpleNamespace(domain=DOMAIN), FakeConfig({}))
    assert isinstance(provider, RiplayProvider)
    assert provider.is_streaming_provider is False
    assert provider.supported_features


async def test_config_entries_declare_settings(bridge: int) -> None:
    """The settings of the provider are declared in code (new API)."""
    provider = make_provider(bridge)
    entries = {entry.key: entry for entry in await provider.get_config_entries()}
    assert set(entries) == {"bridge_host", "bridge_port", "api_token"}
    assert entries["bridge_host"].required is True
    assert entries["bridge_port"].default_value == 8765
    assert entries["api_token"].required is True


async def test_handle_async_init_pings_bridge(bridge: int) -> None:
    """The async init validates the configuration against the live bridge."""
    provider = make_provider(bridge)
    await provider.handle_async_init()
    try:
        assert provider.client.base_url.endswith("/api/v1")
    finally:
        await provider.unload()


async def test_handle_async_init_without_host_fails(bridge: int) -> None:
    """A missing host refuses the load with a clear error."""
    provider = make_provider(bridge)
    provider.config._values["bridge_host"] = None
    with pytest.raises(ProviderUnavailableError):
        await provider.handle_async_init()


async def test_handle_async_init_wrong_token(bridge: int) -> None:
    """A wrong token surfaces as LoginFailed."""
    provider = make_provider(bridge, token="wrong-token")
    with pytest.raises(LoginFailed):
        await provider.handle_async_init()


async def test_handle_async_init_dead_host() -> None:
    """An unreachable bridge surfaces as ProviderUnavailableError."""
    provider = make_provider(9, host="127.0.0.1")  # port 1: nothing listens there
    with pytest.raises(ProviderUnavailableError):
        await provider.handle_async_init()


async def test_library_mapping_with_real_models(bridge: int) -> None:
    """Library generators produce real music-assistant-models items."""
    provider = make_provider(bridge)
    await provider.handle_async_init()
    try:
        artists = [a async for a in provider.get_library_artists()]
        assert len(artists) == 1 and artists[0].name == "Local Artists"
        assert artists[0].provider == INSTANCE_ID

        albums = [a async for a in provider.get_library_albums()]
        assert len(albums) == 1 and albums[0].year == 2024

        tracks = [t async for t in provider.get_library_tracks()]
        assert len(tracks) == 2
        t1 = next(t for t in tracks if t.item_id == "local-1")
        assert isinstance(t1, Track)
        assert t1.provider == INSTANCE_ID
        assert t1.duration == 212
        assert t1.is_playable is True
        assert len(t1.provider_mappings) == 1
        mapping = next(iter(t1.provider_mappings))
        assert mapping.provider_domain == DOMAIN
        assert mapping.provider_instance == INSTANCE_ID
        assert mapping.available is True
        assert t1.album is not None and t1.album.name == "Local Album"
        assert len(t1.artists) == 1

        t2 = next(t for t in tracks if t.item_id == "yt-1")
        assert t2.is_playable is False

        playlists = [p async for p in provider.get_library_playlists()]
        assert len(playlists) == 1 and playlists[0].name == "Favourites"
    finally:
        await provider.unload()


async def test_single_items_and_collections(bridge: int) -> None:
    """Single lookups and the list/collection methods of the new API."""
    provider = make_provider(bridge)
    await provider.handle_async_init()
    try:
        artist = await provider.get_artist("artist-1")
        assert artist.name == "Local Artists"

        albums = await provider.get_artist_albums("artist-1")
        assert isinstance(albums, list) and len(albums) == 1

        album = await provider.get_album("album-1")
        assert album.name == "Local Album"

        album_tracks = await provider.get_album_tracks("album-1")
        assert isinstance(album_tracks, list) and len(album_tracks) == 1

        playlist = await provider.get_playlist("1")
        assert playlist.name == "Favourites"

        playlist_tracks = await provider.get_playlist_tracks("1", page=0)
        assert isinstance(playlist_tracks, list) and len(playlist_tracks) == 2

        with pytest.raises(MediaNotFoundError):
            await provider.get_artist("nope")
        with pytest.raises(MediaNotFoundError):
            await provider.get_album("nope")
        with pytest.raises(MediaNotFoundError):
            await provider.get_playlist("nope")
    finally:
        await provider.unload()


async def test_search_multi_type(bridge: int) -> None:
    """Search returns only the requested media types."""
    provider = make_provider(bridge)
    await provider.handle_async_init()
    try:
        result = await provider.search("o", ["track", "album", "artist", "playlist"], limit=5)
        assert len(result.tracks) >= 1
        assert len(result.albums) >= 1
        assert len(result.artists) >= 1

        result_tracks_only = await provider.search("o", ["track"], limit=5)
        assert len(result_tracks_only.tracks) >= 1
        assert result_tracks_only.artists == []
    finally:
        await provider.unload()


async def test_stream_details_relay(bridge: int) -> None:
    """StreamDetails carry the relay URL of the bridge for servable tracks."""
    provider = make_provider(bridge)
    await provider.handle_async_init()
    try:
        details = await provider.get_stream_details("local-1", MediaType.TRACK)
        assert isinstance(details, StreamDetails)
        assert details.stream_type == StreamType.HTTP
        assert details.provider == INSTANCE_ID
        assert details.media_type == MediaType.TRACK
        assert details.path == f"http://127.0.0.1:{bridge}/relay/testtoken/s/local-1"
        assert details.duration == 212
        assert details.size == 41000000
        assert details.allow_seek is True
        assert details.audio_format.content_type is not None
    finally:
        await provider.unload()


async def test_stream_details_youtube_only_track(bridge: int) -> None:
    """A YouTube-only track can not be streamed: clear MediaNotFoundError."""
    provider = make_provider(bridge)
    await provider.handle_async_init()
    try:
        with pytest.raises(MediaNotFoundError):
            await provider.get_stream_details("yt-1", MediaType.TRACK)
        with pytest.raises(MediaNotFoundError):
            await provider.get_stream_details("nope", MediaType.TRACK)
    finally:
        await provider.unload()


async def test_client_typed_errors(bridge: int) -> None:
    """The client tells auth problems from connection problems."""
    bad_client = RiplayBridgeClient("127.0.0.1", "wrong-token", port=bridge)
    try:
        with pytest.raises(BridgeAuthError):
            await bad_client.health()
    finally:
        await bad_client.close()

    dead_client = RiplayBridgeClient("127.0.0.1", TOKEN, port=1)
    try:
        with pytest.raises(BridgeConnectionError):
            await dead_client.health()
    finally:
        await dead_client.close()
