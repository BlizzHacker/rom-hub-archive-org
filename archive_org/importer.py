"""Archive.org `importer`: turn one item identifier into a FetchPlan.

The plugin decides *what* should be fetched and nothing else. It opens no
socket -- `ctx.http` is an RPC back to the host, and the host re-validates
every URL in the returned plan against this plugin's own manifest
allowlist before fetching any of it.

Routing comes from `GET https://archive.org/metadata/<identifier>`, which
carries everything needed in one round trip:

    metadata.emulator      "dosbox"   -> which RomM platform
    metadata.emulator_ext  "zip"      -> which of the item's files is the ROM
    metadata.collection    [...]      -> whether it may be downloaded at all
    files[]                           -> name / format / size (size is a STRING)

Three decisions here are load-bearing, and each one is the safe half of a
choice that could have gone the other way:

**`stream_only` is a refusal, not a warning.** Archive.org marks items it
will only stream in-browser by putting them in the `stream_only`
collection. Those items still list a perfectly ordinary `.zip` in
`files[]`; the flag is the only thing that distinguishes them, which is
precisely why routing reads Archive.org's own signal instead of an
allowlist we would have to maintain. The check runs before any file is
chosen, so a refusal never hands anyone a URL to try by hand.

**An unmapped emulator fails visibly.** See `platforms.py`. Guessing a
platform files a ROM under the wrong system, and nothing downstream ever
notices.

**Largest wins, and "no size" loses.** When several files share the
payload extension the biggest is the ROM; the rest are alternate builds or
stubs. Archive.org omits `size` entirely on some files (`_files.xml` on
every item there is), and a missing size sorts as smallest so a metadata
stub can never outrank the actual game.
"""

import json
import posixpath
from urllib.parse import quote

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .platforms import platform_for

METADATA = "https://archive.org/metadata/"
DOWNLOAD = "https://archive.org/download/"

# Everything imported from here lands in one RomM collection by default, so
# an operator can see at a glance what came from Archive.org and what did not.
DEFAULT_COLLECTION = "Archive.org"

STREAM_ONLY = "stream_only"


class ImportRefused(Exception):
    """This item cannot be imported, and the message says why.

    Raised for every refusal -- stream-only, unmapped emulator, missing
    payload, unusable metadata -- because they all reach an operator the
    same way: as the `error` column of a FAILED job.
    """


def _as_list(value) -> list[str]:
    """Archive.org returns `collection` as a list, or as a bare string when
    an item is in exactly one collection."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _size_of(entry: dict) -> int | None:
    """`files[].size` is a decimal string, or absent. Never an int."""
    raw = entry.get("size")
    if raw is None:
        return None
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        identifier = (result.source_id or "").strip()
        if not identifier:
            raise ImportRefused("the search result carries no Archive.org identifier")

        item = self._metadata(identifier)
        metadata = item.get("metadata")
        if not isinstance(metadata, dict) or not metadata:
            # A 200 with `{}` is how Archive.org answers for an identifier
            # that does not exist. Nothing below can be attempted.
            raise ImportRefused(
                f"Archive.org has no item {identifier!r} (its metadata endpoint "
                f"returned nothing)"
            )

        # 1. May this be downloaded at all? Asked first, and answered without
        #    naming a file, so a refusal cannot double as instructions.
        if STREAM_ONLY in _as_list(metadata.get("collection")):
            raise ImportRefused(
                f"Archive.org item {identifier!r} is stream-only: it is in the "
                f"'stream_only' collection, which means Archive.org permits "
                f"playing it in a browser but not downloading it. It cannot be "
                f"imported -- but it is not unplayable: this plugin's 'stream' "
                f"capability resolves it to the page that plays it. Try "
                f"'rom-hub stream archive-org {identifier} --open'."
            )

        # 2. Which platform? Never guessed -- see platforms.py.
        emulator = metadata.get("emulator")
        if not isinstance(emulator, str) or not emulator.strip():
            raise ImportRefused(
                f"Archive.org item {identifier!r} declares no emulator, so there "
                f"is nothing to map to a RomM platform"
            )
        platform = platform_for(emulator)
        if platform is None:
            raise ImportRefused(
                f"emulator {emulator!r} (Archive.org item {identifier!r}) needs "
                f"mapping: it is not in this plugin's emulator -> RomM platform "
                f"table, and guessing would file the ROM under the wrong system. "
                f"Add it to archive_org/platforms.py."
            )

        # 3. Which file is the ROM?
        payload = self._payload(identifier, metadata, item.get("files"))
        name = payload["name"]

        return FetchPlan(
            files=[
                FetchFile(
                    url=DOWNLOAD + quote(identifier, safe="") + "/" + quote(name),
                    # The path, if any, belongs in the URL. `filename` is what
                    # the host opens for writing, and FetchFile rejects
                    # anything but a bare name for exactly that reason.
                    filename=posixpath.basename(name),
                    size_bytes=_size_of(payload),
                )
            ],
            platform=platform,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )

    def _metadata(self, identifier: str) -> dict:
        url = METADATA + quote(identifier, safe="")
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise ImportRefused(
                f"Archive.org returned HTTP {response.status_code} for the "
                f"metadata of {identifier!r}"
            )
        try:
            item = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            # Rate limiting and maintenance pages both arrive as 200 + HTML.
            raise ImportRefused(
                f"Archive.org's metadata for {identifier!r} was not JSON: {exc}"
            ) from exc
        if not isinstance(item, dict):
            raise ImportRefused(
                f"Archive.org's metadata for {identifier!r} was not an object"
            )
        return item

    def _payload(self, identifier: str, metadata: dict, files) -> dict:
        extension = metadata.get("emulator_ext")
        if not isinstance(extension, str) or not extension.strip():
            raise ImportRefused(
                f"Archive.org item {identifier!r} declares no emulator_ext, so "
                f"which of its files is the ROM cannot be determined"
            )
        extension = extension.strip().lstrip(".").lower()

        candidates = [
            entry
            for entry in (files or [])
            if isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and entry["name"].lower().endswith("." + extension)
            # A bare ".zip" has no basename to write to disk.
            and posixpath.basename(entry["name"]) not in ("", "." + extension)
        ]
        if not candidates:
            raise ImportRefused(
                f"Archive.org item {identifier!r} has no file ending in "
                f"{'.' + extension!r}, which is the extension its metadata "
                f"names as the payload (emulator_ext={extension!r})"
            )

        # Largest wins; a missing size sorts below every real one. `name` is
        # the tie-break so the same item always plans the same file.
        return max(
            candidates, key=lambda entry: (_size_of(entry) or -1, entry["name"])
        )
