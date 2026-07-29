"""Archive.org `stream`: point a player at an item that can be played.

The routing signal is the same one the importer uses, read the other way
round. Archive.org marks items it will only play in a browser by putting
them in the `stream_only` collection; the importer refuses those, and this
is what makes that refusal a redirection rather than a dead end -- the
game is playable, just not downloadable.

An emulated Archive.org item plays at its own details page, which loads
Emularity in the browser. So the target is a URL on `archive.org`, inside
the same allowlist everything else this plugin returns is inside, and the
host checks it before handing it on.

What this deliberately does not do is invent a media URL. Archive.org
serves the *item*, not a stream; anything shaped like a direct video or
audio endpoint would be a fabrication, and a plugin that fabricates a
target is a plugin whose refusals cannot be believed either.
"""

import json
from urllib.parse import quote

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget

METADATA = "https://archive.org/metadata/"
DETAILS = "https://archive.org/details/"

STREAM_ONLY = "stream_only"


class StreamRefused(Exception):
    """This item cannot be streamed, and the message says why."""


def _as_list(value) -> list[str]:
    """Archive.org returns `collection` as a list, or as a bare string when
    an item is in exactly one collection."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        identifier = (result.source_id or "").strip()
        if not identifier:
            raise StreamRefused("the search result carries no Archive.org identifier")

        item = self._metadata(identifier)
        metadata = item.get("metadata")
        if not isinstance(metadata, dict) or not metadata:
            raise StreamRefused(
                f"Archive.org has no item {identifier!r} (its metadata endpoint "
                f"returned nothing)"
            )

        emulator = metadata.get("emulator")
        if not isinstance(emulator, str) or not emulator.strip():
            # No emulator means no in-browser player. Saying so is more use
            # than handing back a details page that will not play anything.
            raise StreamRefused(
                f"Archive.org item {identifier!r} declares no emulator, so there "
                f"is nothing to stream: it is not an emulated item"
            )

        collections = _as_list(metadata.get("collection"))
        title = metadata.get("title")
        return StreamTarget(
            kind="url",
            target=DETAILS + quote(identifier, safe=""),
            mime_type="text/html",
            title=title if isinstance(title, str) and title.strip() else None,
            extra={
                "emulator": emulator.strip(),
                # The operator's cue for why an item that will not import
                # still turns up here.
                "stream_only": "true" if STREAM_ONLY in collections else "false",
                "identifier": identifier,
            },
        )

    def _metadata(self, identifier: str) -> dict:
        url = METADATA + quote(identifier, safe="")
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise StreamRefused(
                f"Archive.org returned HTTP {response.status_code} for the "
                f"metadata of {identifier!r}"
            )
        try:
            item = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise StreamRefused(
                f"Archive.org's metadata for {identifier!r} was not JSON: {exc}"
            ) from exc
        if not isinstance(item, dict):
            raise StreamRefused(
                f"Archive.org's metadata for {identifier!r} was not an object"
            )
        return item
