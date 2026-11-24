"""Async HTTP client for the RiPlay bridge API (/api/v1).

The bridge runs inside the RiPlay app (same process and port as its streaming
relay server). All JSON responses use camelCase; they are mapped to snake_case
Python dataclasses here. Errors are typed so the provider can tell a network
problem (BridgeConnectionError) from a wrong token (BridgeAuthError) and from
a missing item (BridgeItemNotFoundError).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import (
    DEFAULT_BRIDGE_PORT,
    DOMAIN,
    HTTP_TIMEOUT_SECONDS,
    PAGE_LIMIT,
)

LOGGER = logging.getLogger(f"ma.provider.{DOMAIN}")


class BridgeError(Exception):
    """Generic bridge error."""


class BridgeConnectionError(BridgeError):
    """The bridge is unreachable (host/port/network)."""


class BridgeAuthError(BridgeError):
    """Wrong or missing token (401)."""


class BridgeItemNotFoundError(BridgeError):
    """The requested item does not exist in the catalog (404)."""


@dataclass
class BridgeArtist:
    item_id: str
    name: str
    thumbnail_url: str | None = None
    album_count: int = 0
    track_count: int = 0


@dataclass
class BridgeAlbum:
    item_id: str
    name: str
    artist_id: str | None = None
    artist_name: str | None = None
    year: int | None = None
    track_count: int = 0
    thumbnail_url: str | None = None


@dataclass
class BridgeTrack:
    item_id: str
    name: str
    artist_id: str | None = None
    artist_name: str | None = None
    album_id: str | None = None
    album_name: str | None = None
    duration_seconds: int = 0
    track_number: int = 0
    disc_number: int = 0
    year: int | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    thumbnail_url: str | None = None
    direct_url: str | None = None
    servable: bool = False


@dataclass
class BridgePlaylist:
    item_id: str
    name: str
    track_count: int = 0
    thumbnail_url: str | None = None


@dataclass
class BridgeTrackDetail:
    track: BridgeTrack
    stream_url: str | None = None
    stream_kind: str | None = None


@dataclass
class BridgePage:
    items: list[Any]
    total: int
    offset: int
    limit: int


@dataclass
class BridgeSearchResult:
    artists: list[BridgeArtist] = field(default_factory=list)
    albums: list[BridgeAlbum] = field(default_factory=list)
    tracks: list[BridgeTrack] = field(default_factory=list)
    playlists: list[BridgePlaylist] = field(default_factory=list)


def _opt_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    return str(value) if value is not None else None


def _opt_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    return int(value) if value else None


def parse_artist(data: dict[str, Any]) -> BridgeArtist:
    """Map the JSON of a bridge artist to a dataclass."""
    return BridgeArtist(
        item_id=str(data["id"]),
        name=str(data.get("name") or "Unknown artist"),
        thumbnail_url=_opt_str(data, "thumbnailUrl"),
        album_count=_opt_int(data, "albumCount") or 0,
        track_count=_opt_int(data, "trackCount") or 0,
    )


def parse_album(data: dict[str, Any]) -> BridgeAlbum:
    """Map the JSON of a bridge album to a dataclass."""
    return BridgeAlbum(
        item_id=str(data["id"]),
        name=str(data.get("name") or "Unknown album"),
        artist_id=_opt_str(data, "artistId"),
        artist_name=_opt_str(data, "artistName"),
        year=_opt_int(data, "year"),
        track_count=_opt_int(data, "trackCount") or 0,
        thumbnail_url=_opt_str(data, "thumbnailUrl"),
    )


def parse_track(data: dict[str, Any]) -> BridgeTrack:
    """Map the JSON of a bridge track to a dataclass."""
    return BridgeTrack(
        item_id=str(data["id"]),
        name=str(data.get("name") or "Unknown track"),
        artist_id=_opt_str(data, "artistId"),
        artist_name=_opt_str(data, "artistName"),
        album_id=_opt_str(data, "albumId"),
        album_name=_opt_str(data, "albumName"),
        duration_seconds=_opt_int(data, "durationSeconds") or 0,
        track_number=_opt_int(data, "trackNumber") or 0,
        disc_number=_opt_int(data, "discNumber") or 0,
        year=_opt_int(data, "year"),
        mime_type=_opt_str(data, "mimeType"),
        size_bytes=_opt_int(data, "sizeBytes"),
        thumbnail_url=_opt_str(data, "thumbnailUrl"),
        direct_url=_opt_str(data, "directUrl"),
        servable=bool(data.get("servable") or False),
    )


def parse_playlist(data: dict[str, Any]) -> BridgePlaylist:
    """Map the JSON of a bridge playlist to a dataclass."""
    return BridgePlaylist(
        item_id=str(data["id"]),
        name=str(data.get("name") or "Playlist"),
        track_count=_opt_int(data, "trackCount") or 0,
        thumbnail_url=_opt_str(data, "thumbnailUrl"),
    )


def parse_track_detail(data: dict[str, Any]) -> BridgeTrackDetail:
    """Map the JSON of a track detail (with its stream URL) to a dataclass."""
    return BridgeTrackDetail(
        track=parse_track(data["track"]),
        stream_url=_opt_str(data, "streamUrl"),
        stream_kind=_opt_str(data, "streamKind"),
    )


class RiplayBridgeClient:
    """aiohttp client towards the RiPlay bridge (/api/v1)."""

    def __init__(
        self,
        host: str,
        token: str,
        port: int = DEFAULT_BRIDGE_PORT,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Create the client; host is the LAN address of the RiPlay device."""
        self._host = host.strip()
        self._token = token
        self._port = int(port)
        self._session = session
        self._owns_session = session is None

    @property
    def base_url(self) -> str:
        """Return the base address of the API, with an IPv6-safe host."""
        host = self._host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self._port}/api/v1"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the owned session, if any."""
        if self._session is not None and self._owns_session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        session = await self._ensure_session()
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 401:
                    raise BridgeAuthError(
                        "Wrong or missing RiPlay bridge token (401): the token is shown "
                        "in RiPlay's server/relay settings"
                    )
                if response.status == 404:
                    raise BridgeItemNotFoundError(f"Item not found: {path}")
                if response.status != 200:
                    text = await response.text()
                    raise BridgeError(f"Bridge HTTP {response.status} on {path}: {text[:200]}")
                return await response.json(content_type=None)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            raise BridgeConnectionError(
                f"RiPlay bridge unreachable on {url}: {exc}"
            ) from exc

    # ───────────────────────────── API ─────────────────────────────

    async def ping(self) -> dict[str, Any]:
        """Public endpoint: quick host/port check (no token needed)."""
        session = await self._ensure_session()
        url = f"{self.base_url}/ping"
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    raise BridgeConnectionError(f"/ping HTTP {response.status}")
                return await response.json(content_type=None)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as exc:
            raise BridgeConnectionError(f"RiPlay bridge unreachable on {url}: {exc}") from exc

    async def health(self) -> dict[str, Any]:
        """Library counts (authenticated)."""
        return await self._get("/health")

    async def artist(self, item_id: str) -> BridgeArtist | None:
        """Single artist, or None when absent."""
        try:
            return parse_artist(await self._get(f"/artists/{item_id}"))
        except BridgeItemNotFoundError:
            return None

    async def album(self, item_id: str) -> BridgeAlbum | None:
        """Single album, or None when absent."""
        try:
            return parse_album(await self._get(f"/albums/{item_id}"))
        except BridgeItemNotFoundError:
            return None

    async def playlist(self, item_id: str) -> BridgePlaylist | None:
        """Single playlist, or None when absent."""
        try:
            return parse_playlist(await self._get(f"/playlists/{item_id}"))
        except BridgeItemNotFoundError:
            return None

    async def artists(self, offset: int = 0, limit: int = PAGE_LIMIT) -> BridgePage:
        """One page of the artists of the RiPlay library."""
        data = await self._get("/library/artists", {"offset": offset, "limit": limit})
        return BridgePage(
            items=[parse_artist(x) for x in data.get("items", [])],
            total=int(data.get("total") or 0),
            offset=int(data.get("offset") or 0),
            limit=int(data.get("limit") or 0),
        )

    async def albums(self, offset: int = 0, limit: int = PAGE_LIMIT) -> BridgePage:
        """One page of the albums of the RiPlay library."""
        data = await self._get("/library/albums", {"offset": offset, "limit": limit})
        return BridgePage(
            items=[parse_album(x) for x in data.get("items", [])],
            total=int(data.get("total") or 0),
            offset=int(data.get("offset") or 0),
            limit=int(data.get("limit") or 0),
        )

    async def tracks(self, offset: int = 0, limit: int = PAGE_LIMIT) -> BridgePage:
        """One page of the tracks of the RiPlay library."""
        data = await self._get("/library/tracks", {"offset": offset, "limit": limit})
        return BridgePage(
            items=[parse_track(x) for x in data.get("items", [])],
            total=int(data.get("total") or 0),
            offset=int(data.get("offset") or 0),
            limit=int(data.get("limit") or 0),
        )

    async def playlists(self, offset: int = 0, limit: int = PAGE_LIMIT) -> BridgePage:
        """One page of the playlists of the RiPlay library."""
        data = await self._get("/library/playlists", {"offset": offset, "limit": limit})
        return BridgePage(
            items=[parse_playlist(x) for x in data.get("items", [])],
            total=int(data.get("total") or 0),
            offset=int(data.get("offset") or 0),
            limit=int(data.get("limit") or 0),
        )

    async def artist_albums(self, artist_id: str, offset: int = 0, limit: int = PAGE_LIMIT) -> BridgePage:
        """One page of the albums of an artist."""
        data = await self._get(f"/artists/{artist_id}/albums", {"offset": offset, "limit": limit})
        return BridgePage(
            items=[parse_album(x) for x in data.get("items", [])],
            total=int(data.get("total") or 0),
            offset=int(data.get("offset") or 0),
            limit=int(data.get("limit") or 0),
        )

    async def album_tracks(self, album_id: str, offset: int = 0, limit: int = PAGE_LIMIT) -> BridgePage:
        """One page of the tracks of an album."""
        data = await self._get(f"/albums/{album_id}/tracks", {"offset": offset, "limit": limit})
        return BridgePage(
            items=[parse_track(x) for x in data.get("items", [])],
            total=int(data.get("total") or 0),
            offset=int(data.get("offset") or 0),
            limit=int(data.get("limit") or 0),
        )

    async def playlist_tracks(self, playlist_id: str, offset: int = 0, limit: int = PAGE_LIMIT) -> BridgePage:
        """One page of the tracks of a playlist."""
        data = await self._get(f"/playlists/{playlist_id}/tracks", {"offset": offset, "limit": limit})
        return BridgePage(
            items=[parse_track(x) for x in data.get("items", [])],
            total=int(data.get("total") or 0),
            offset=int(data.get("offset") or 0),
            limit=int(data.get("limit") or 0),
        )

    async def track(self, item_id: str) -> BridgeTrackDetail:
        """Track detail with the stream URL computed for this request."""
        return parse_track_detail(await self._get(f"/tracks/{item_id}"))

    async def search(self, query: str, limit: int = 20) -> BridgeSearchResult:
        """Multi-type search in the RiPlay library."""
        data = await self._get("/search", {"q": query, "limit": limit})
        return BridgeSearchResult(
            artists=[parse_artist(x) for x in data.get("artists", [])],
            albums=[parse_album(x) for x in data.get("albums", [])],
            tracks=[parse_track(x) for x in data.get("tracks", [])],
            playlists=[parse_playlist(x) for x in data.get("playlists", [])],
        )
