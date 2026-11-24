"""RiPlay provider for Music Assistant.

Exposes the music library of RiPlay (local files, Music Vault, direct URLs) as a
music source, through the REST bridge /api/v1 that runs inside the RiPlay app
(same process and port as its streaming relay server).

Playback flow:
1. Music Assistant asks for the StreamDetails of a track;
2. the provider calls GET /api/v1/tracks/{id} on the bridge;
3. the bridge answers with a streamUrl:
   - "direct": an http(s) URL straight to the source (the phone stays out of the
     audio path);
   - "relay": the URL of RiPlay's own streaming server
     (http://<ip>:<port>/relay/<token>/s/<id>, with Range support);
4. the provider returns StreamDetails(StreamType.HTTP, path=streamUrl): Music
   Assistant downloads the audio directly, without transcoding.

YouTube-only tracks stay visible in the library but are not servable by the
bridge (their stream can not leave the WebView per YouTube's ToS); they are
reported as not playable, and the ytmusic provider covers them in Music
Assistant. The app version of RiPlay matters: the bridge API /api/v1 with
camelCase JSON and Bearer-token auth arrived with the shared relayserver module.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Sequence
from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import (
    ConfigEntryType,
    ContentType,
    ImageType,
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import LoginFailed, MediaNotFoundError, ProviderUnavailableError
from music_assistant_models.media_items import (
    Album,
    Artist,
    AudioFormat,
    ItemMapping,
    MediaItemMetadata,
    MediaItemImage,
    Playlist,
    ProviderMapping,
    SearchResults,
    Track,
)
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.models.music_provider import MusicProvider

from .bridge_client import (
    BridgeAlbum,
    BridgeArtist,
    BridgeAuthError,
    BridgeConnectionError,
    BridgeError,
    BridgeItemNotFoundError,
    BridgePlaylist,
    BridgeTrack,
    RiplayBridgeClient,
)
from .const import (
    CONF_API_TOKEN,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    DEFAULT_BRIDGE_PORT,
    DOMAIN,
    MIME_TO_CONTENT_TYPE,
    PAGE_LIMIT,
)

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

# these features decide what the provider does; provides in manifest.json is for
# discovery only: the media types it offers whatever the config or the account
SUPPORTED_FEATURES = {
    ProviderFeature.BROWSE,
    ProviderFeature.SEARCH,
    ProviderFeature.LIBRARY_ARTISTS,
    ProviderFeature.LIBRARY_ALBUMS,
    ProviderFeature.LIBRARY_TRACKS,
    ProviderFeature.LIBRARY_PLAYLISTS,
    ProviderFeature.ARTIST_ALBUMS,
}

LOGGER = logging.getLogger(f"ma.provider.{DOMAIN}")


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize the provider for one settings entry."""
    return RiplayProvider(mass, manifest, config, SUPPORTED_FEATURES)


class RiplayProvider(MusicProvider):
    """Music source backed by the bridge of a RiPlay instance on the LAN."""

    def __init__(
        self,
        mass: MusicAssistant,
        manifest: ProviderManifest,
        config: ProviderConfig,
        supported_features: set[ProviderFeature],
    ) -> None:
        """Initialize the provider instance."""
        super().__init__(mass, manifest, config, supported_features)
        self._client: RiplayBridgeClient | None = None

    @property
    def is_streaming_provider(self) -> bool:
        """Return False: the catalog of this source is the user's own library."""
        return False

    async def get_config_entries(self) -> tuple[ConfigEntry, ...]:
        """Return the settings of this provider instance."""
        return (
            ConfigEntry(
                key=CONF_BRIDGE_HOST,
                type=ConfigEntryType.STRING,
                required=True,
                description=(
                    "LAN address of the RiPlay device, as shown in the app's "
                    "server/relay settings (e.g. 192.168.1.10)."
                ),
            ),
            ConfigEntry(
                key=CONF_BRIDGE_PORT,
                type=ConfigEntryType.INTEGER,
                required=False,
                default_value=DEFAULT_BRIDGE_PORT,
                description=(
                    "Port of RiPlay's server: the same port serves the bridge and "
                    "the streaming relay."
                ),
            ),
            ConfigEntry(
                key=CONF_API_TOKEN,
                type=ConfigEntryType.SECURE_STRING,
                required=True,
                description=(
                    "Token shown in RiPlay's server/relay settings: it authorizes "
                    "both /api/v1 and the streaming URLs."
                ),
            ),
        )

    async def handle_async_init(self) -> None:
        """Connect to the bridge and validate the configuration."""
        host = str(self.config.get_value(CONF_BRIDGE_HOST) or "")
        token = str(self.config.get_value(CONF_API_TOKEN) or "")
        port = int(self.config.get_value(CONF_BRIDGE_PORT) or DEFAULT_BRIDGE_PORT)
        if not host:
            raise ProviderUnavailableError(
                "No RiPlay bridge host configured: fill in the LAN address of the "
                "RiPlay device in the provider settings."
            )
        client = RiplayBridgeClient(host=host, token=token, port=port)
        try:
            ping = await client.ping()
            LOGGER.info(
                "RiPlay bridge reached on %s (app=%s, bridge=%s)",
                client.base_url,
                ping.get("app"),
                ping.get("bridgeVersion"),
            )
            health = await client.health()
            LOGGER.info(
                "RiPlay library: %s tracks, %s artists, %s albums, %s playlists "
                "(%s servable)",
                health.get("trackCount"),
                health.get("artistCount"),
                health.get("albumCount"),
                health.get("playlistCount"),
                health.get("servableTrackCount"),
            )
        except BridgeAuthError as exc:
            await client.close()
            raise LoginFailed(
                "Wrong RiPlay bridge token: set the same token shown in the app's "
                "server/relay settings in the provider settings."
            ) from exc
        except BridgeConnectionError as exc:
            await client.close()
            raise ProviderUnavailableError(
                f"RiPlay bridge unreachable ({exc}): check that the app is open, "
                "that 'Expose library to Music Assistant' is enabled and that "
                "host/port match the provider settings."
            ) from exc
        self._client = client

    async def unload(self, is_removed: bool = False) -> None:
        """Close the HTTP session when the instance is unloaded."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    @property
    def client(self) -> RiplayBridgeClient:
        """Return the bridge client (present after handle_async_init)."""
        assert self._client is not None, "provider not initialized"
        return self._client

    # ─────────────────────── DTO -> media items ───────────────────────

    @property
    def _instance(self) -> str:
        return self.instance_id

    def _content_type(self, mime: str | None) -> ContentType:
        if mime:
            name = MIME_TO_CONTENT_TYPE.get(mime.lower())
            if name:
                return ContentType[name]
        return ContentType.UNKNOWN

    def _audio_format(self, mime: str | None, bitrate: int | None = None) -> AudioFormat:
        content_type = self._content_type(mime)
        return AudioFormat(
            content_type=content_type,
            codec_type=content_type,
            output_format_str=content_type.name.lower() if content_type != ContentType.UNKNOWN else "",
            bit_rate=bitrate,
        )

    def _image(self, url: str | None) -> MediaItemImage | None:
        if not url:
            return None
        return MediaItemImage(
            type=ImageType.THUMB,
            path=url,
            provider=DOMAIN,
            remotely_accessible=url.startswith("http"),
        )

    def _metadata(self, image_url: str | None) -> MediaItemMetadata:
        image = self._image(image_url)
        return MediaItemMetadata(images=[image] if image else None)

    def _mapping(self, item_id: str, audio_format: AudioFormat | None = None, url: str | None = None) -> ProviderMapping:
        return ProviderMapping(
            item_id=item_id,
            provider_domain=DOMAIN,
            provider_instance=self._instance,
            available=True,
            audio_format=audio_format or self._audio_format(None),
            url=url,
        )

    def _artist(self, dto: BridgeArtist) -> Artist:
        return Artist(
            item_id=dto.item_id,
            provider=self._instance,
            name=dto.name,
            metadata=self._metadata(dto.thumbnail_url),
            provider_mappings={self._mapping(dto.item_id)},
        )

    def _artist_mapping(self, item_id: str | None, name: str | None) -> ItemMapping | None:
        if not item_id or not name:
            return None
        return ItemMapping(item_id=item_id, provider=DOMAIN, name=name)

    def _album(self, dto: BridgeAlbum) -> Album:
        artists = []
        if dto.artist_name:
            artists.append(
                self._artist_mapping(dto.artist_id, dto.artist_name)
                or ItemMapping(item_id=f"{dto.item_id}#artist", provider=DOMAIN, name=dto.artist_name)
            )
        return Album(
            item_id=dto.item_id,
            provider=self._instance,
            name=dto.name,
            year=dto.year,
            artists=artists,
            metadata=self._metadata(dto.thumbnail_url),
            provider_mappings={self._mapping(dto.item_id)},
        )

    def _track(self, dto: BridgeTrack, stream_url: str | None = None) -> Track:
        audio_format = self._audio_format(dto.mime_type)
        artists = []
        if dto.artist_name:
            mapping = self._artist_mapping(dto.artist_id, dto.artist_name)
            artists.append(mapping or ItemMapping(item_id=f"{dto.item_id}#artist", provider=DOMAIN, name=dto.artist_name))
        album: ItemMapping | None = None
        if dto.album_name:
            album = ItemMapping(
                item_id=dto.album_id or f"{dto.item_id}#album",
                provider=DOMAIN,
                name=dto.album_name,
                year=dto.year,
                image=self._image(dto.thumbnail_url),
            )
        return Track(
            item_id=dto.item_id,
            provider=self._instance,
            name=dto.name,
            duration=dto.duration_seconds or 0,
            track_number=dto.track_number or 0,
            disc_number=dto.disc_number or 0,
            artists=artists,
            album=album,
            is_playable=dto.servable,
            metadata=self._metadata(dto.thumbnail_url),
            provider_mappings={
                ProviderMapping(
                    item_id=dto.item_id,
                    provider_domain=DOMAIN,
                    provider_instance=self._instance,
                    available=dto.servable,
                    audio_format=audio_format,
                    url=stream_url or dto.direct_url,
                )
            },
        )

    def _playlist(self, dto: BridgePlaylist) -> Playlist:
        return Playlist(
            item_id=dto.item_id,
            provider=self._instance,
            name=dto.name,
            owner="RiPlay",
            is_editable=False,
            metadata=self._metadata(dto.thumbnail_url),
            provider_mappings={self._mapping(dto.item_id)},
        )

    # ─────────────────────────────── library ───────────────────────────────

    async def get_library_artists(self) -> AsyncGenerator[Artist]:
        """Yield the artists of the RiPlay library."""
        offset = 0
        while True:
            page = await self.client.artists(offset=offset, limit=PAGE_LIMIT)
            for dto in page.items:
                yield self._artist(dto)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break

    async def get_library_albums(self) -> AsyncGenerator[Album]:
        """Yield the albums of the RiPlay library."""
        offset = 0
        while True:
            page = await self.client.albums(offset=offset, limit=PAGE_LIMIT)
            for dto in page.items:
                yield self._album(dto)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break

    async def get_library_tracks(self) -> AsyncGenerator[Track]:
        """Yield the tracks of the RiPlay library."""
        offset = 0
        while True:
            page = await self.client.tracks(offset=offset, limit=PAGE_LIMIT)
            for dto in page.items:
                yield self._track(dto)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break

    async def get_library_playlists(self) -> AsyncGenerator[Playlist]:
        """Yield the playlists of the RiPlay library."""
        offset = 0
        while True:
            page = await self.client.playlists(offset=offset, limit=PAGE_LIMIT)
            for dto in page.items:
                yield self._playlist(dto)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break

    # ───────────────────────────── single items ─────────────────────────────

    async def get_artist(self, prov_artist_id: str) -> Artist:
        """Return full details of a single artist."""
        dto = await self.client.artist(prov_artist_id)
        if dto is None:
            raise MediaNotFoundError(f"Artist {prov_artist_id} not found")
        return self._artist(dto)

    async def get_album(self, prov_album_id: str) -> Album:
        """Return full details of a single album."""
        dto = await self.client.album(prov_album_id)
        if dto is None:
            raise MediaNotFoundError(f"Album {prov_album_id} not found")
        return self._album(dto)

    async def get_track(self, prov_track_id: str) -> Track:
        """Return full details of a single track."""
        detail = await self.client.track(prov_track_id)
        return self._track(detail.track, stream_url=detail.stream_url)

    async def get_playlist(self, prov_playlist_id: str) -> Playlist:
        """Return full details of a single playlist."""
        dto = await self.client.playlist(prov_playlist_id)
        if dto is None:
            raise MediaNotFoundError(f"Playlist {prov_playlist_id} not found")
        return self._playlist(dto)

    async def get_artist_albums(self, prov_artist_id: str) -> list[Album]:
        """Return the albums of an artist."""
        albums: list[Album] = []
        offset = 0
        while True:
            page = await self.client.artist_albums(prov_artist_id, offset=offset, limit=PAGE_LIMIT)
            albums.extend(self._album(dto) for dto in page.items)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break
        return albums

    async def get_album_tracks(self, prov_album_id: str) -> list[Track]:
        """Return the tracks of an album."""
        tracks: list[Track] = []
        offset = 0
        while True:
            page = await self.client.album_tracks(prov_album_id, offset=offset, limit=PAGE_LIMIT)
            tracks.extend(self._track(dto) for dto in page.items)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break
        return tracks

    async def get_playlist_tracks(
        self,
        prov_playlist_id: str,
        page: int = 0,
    ) -> Sequence[Track]:
        """Return one page of tracks of a playlist."""
        result = await self.client.playlist_tracks(
            prov_playlist_id, offset=page * PAGE_LIMIT, limit=PAGE_LIMIT
        )
        return [self._track(dto) for dto in result.items]

    # ─────────────────────────────── search ───────────────────────────────

    async def search(
        self,
        search_query: str,
        media_types: list[MediaType],
        limit: int = 5,
    ) -> SearchResults:
        """Perform a search in the RiPlay library."""
        result = await self.client.search(search_query, limit=max(limit * 2, 20))
        return SearchResults(
            artists=[self._artist(dto) for dto in result.artists[:limit]]
            if MediaType.ARTIST in media_types
            else [],
            albums=[self._album(dto) for dto in result.albums[:limit]]
            if MediaType.ALBUM in media_types
            else [],
            tracks=[self._track(dto) for dto in result.tracks[:limit]]
            if MediaType.TRACK in media_types
            else [],
            playlists=[self._playlist(dto) for dto in result.playlists[:limit]]
            if MediaType.PLAYLIST in media_types
            else [],
        )

    # ─────────────────────────────── streaming ───────────────────────────────

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Return StreamDetails: StreamType.HTTP with the URL given by the bridge.

        The bridge computes the streamUrl for THIS request:
        - direct -> the URL of the source (the phone is not in the audio path);
        - relay  -> /relay/<token>/s/<id> of RiPlay's streaming server, with
          Range support (seek) already working.
        """
        try:
            detail = await self.client.track(item_id)
        except BridgeItemNotFoundError as exc:
            raise MediaNotFoundError(f"Track {item_id} not found") from exc
        except BridgeConnectionError as exc:
            raise ProviderUnavailableError(f"RiPlay bridge unreachable: {exc}") from exc
        if detail.stream_url is None:
            raise MediaNotFoundError(
                f"Track {item_id} can not be streamed by the bridge "
                "(YouTube-only content or an inaccessible file): use the ytmusic "
                "provider for streaming content."
            )
        audio_format = self._audio_format(detail.track.mime_type)
        return StreamDetails(
            provider=self._instance,
            item_id=item_id,
            audio_format=audio_format,
            media_type=media_type,
            stream_type=StreamType.HTTP,
            path=detail.stream_url,
            duration=detail.track.duration_seconds or None,
            size=detail.track.size_bytes,
            allow_seek=True,
            can_seek=True,
        )