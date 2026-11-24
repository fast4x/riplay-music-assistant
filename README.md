# RiPlay for Music Assistant

A community music provider for [Music Assistant](https://music-assistant.io):
it exposes your [RiPlay](https://github.com/fast4x/RiPlay) library (local and
on-device files, direct URLs) as a music source, through the REST bridge
(`/api/v1`) that runs inside the RiPlay app.

The Music Assistant bridge is available in the **FOSS edition of RiPlay** (it
is not shipped in the non-FOSS Android edition); on desktop, the RiPlay app
has no non-free edition and carries the bridge as well.

Multi-room playback, players and queues stay on Music Assistant; the audio
comes straight from your RiPlay device over HTTP, with Range support (seek),
without transcoding and without any Python running on the RiPlay side.

## How it works

```
┌────────────────┐  /api/v1 (Bearer token)  ┌─────────────────────┐
│ Music Assistant│ ────────────────────────► │ RiPlay (bridge)     │
│    server      │ ◄──────────────────────── │  · catalog metadata │
└───────┬────────┘   streamUrl (direct|relay)└─────────────────────┘
        │  HTTP GET (audio, Range)
        ▼
 RiPlay streaming server: /relay/<token>/s/<id> or the direct URL of the file
```

- the provider lists artists, albums, tracks and playlists of the RiPlay
  library (paged, camelCase JSON over the bridge);
- for each playback the bridge computes a fresh `streamUrl`:
  - `direct`: an http(s) URL of the source file itself (the phone is not in the
    audio path);
  - `relay`: `http://<ip>:<port>/relay/<token>/s/<id>` on RiPlay's own
    streaming server (Range/seek supported);
- the provider returns `StreamDetails(StreamType.HTTP, path=streamUrl)` and
  Music Assistant downloads the audio directly.

The stream URL stays inside the Music Assistant server, as its usage policy
requires: it is only returned by `get_stream_details()` (the playback path)
and is never written to `ProviderMapping.url`, which every API client can
read — so neither the relay URL (it carries the token) nor the direct source
URL is ever exposed through the API.

YouTube-only tracks stay visible in the library but can not be streamed by the
bridge (YouTube's ToS keeps the stream inside the app's WebView): they are
marked not playable here — add the official `ytmusic` provider in Music
Assistant for those.

## Requirements

- the **FOSS edition of RiPlay** (Android) or the RiPlay desktop app, with the
  shared `relayserver` module and the `riplay-bridge` module (the bridge ships
  the `/api/v1` API of release 0.1.0 or newer);
- the Music Assistant bridge enabled in RiPlay's server/relay settings, with
  the host, port and token it shows;
- Music Assistant 2.x with the community catalog enabled (Marketplace).

## Installation

1. In Music Assistant open **Settings → Community providers / Marketplace**
   (an administrator enables the community catalog once).
2. Install the **RiPlay** provider.
3. Fill in the settings:
   - **RiPlay bridge host**: the LAN address of the RiPlay device (e.g.
     `192.168.1.10`);
   - **Bridge port**: RiPlay's server port (default `8765`);
   - **API token**: the token shown in RiPlay's server/relay settings.
4. The library sync starts: artists, albums, tracks and playlists of RiPlay
   appear in Music Assistant.

## Development

```sh
# light environment, no server needed: the tests stub the server base class
uv sync --locked          # or: pip install aiohttp music-assistant-models pytest pytest-asyncio
uv run pytest             # or: pytest
```

- `riplay/` is the provider folder (named after its domain, as the community
  catalog requires): `manifest.json`, `strings.json`, translations and the
  code.
- `tests/test_manifest.py` validates the manifest against the catalog rules;
  `tests/test_provider.py` exercises the full provider flow against a fake
  bridge that replicates the Kotlin bridge byte for byte.
- The CI (`.github/workflows/ci.yml`) runs the tests and the community
  catalog checks (`check-provider.yml`) on every push.

## License

Apache-2.0. RiPlay itself is GPL-3.0 and stays untouched: the provider only
talks HTTP to it, there is no code linkage between the two.

## Changelog

- **0.2.0** — stream addresses no longer appear in `ProviderMapping.url` (they
  stay inside the server, per the Music Assistant usage policy); the provider
  targets the FOSS edition of RiPlay, where the bridge lives.
- **0.1.0** — first release: library browsing, search and HTTP playback over
  the RiPlay bridge.

---

<div align="center">
 <i> Made with ❤️ by <a href="https://github.com/fast4x">Fast4x</a> </i>
</div>