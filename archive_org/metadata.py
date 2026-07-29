"""Archive.org `metadata`: turn one item identifier into a MetadataPatch.

Same shape as the importer, and the same single round trip:
`GET https://archive.org/metadata/<identifier>` carries the title and the
item's whole file list, and `files[].format` is what makes picking a cover
deterministic rather than a guess at filenames.

The plugin never fetches the cover. It names a URL; the **host** checks
that URL against this plugin's own `network` allowlist and then fetches
it. That is the same rule a FetchPlan URL follows, for the same reason.

Two decisions here are the safe half of a choice that could have gone the
other way:

**An unidentified rom is refused, never guessed.** The `RomRef` the host
supplies carries RomM's name and filename, and neither one is an
Archive.org identifier (`rubik.zip` is not `rubik_202308`). Searching
Archive.org for the rom's name and taking the top hit would produce a
patch for *a* game rather than *this* game, and the operator would have no
way to notice: the wrong cover and the wrong title would simply appear in
their library. So the identifier has to be supplied -- `rom-hub enrich
<plugin> <rom_id> --source-id <identifier>` -- and its absence is a
refusal with that sentence in it.

**The cover filename is ours, not Archive.org's.** The host writes this
name to disk, and `MetadataPatch` refuses anything that is not a plain
bare name. Archive.org filenames are user-supplied and frequently are not
(spaces are fine, but `#`, `%` and non-Latin scripts all appear), so the
extension is taken from the chosen file and the stem is always `cover`.
Nothing about which bytes are fetched depends on it.
"""

import json
import posixpath
from urllib.parse import quote

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

METADATA = "https://archive.org/metadata/"
DOWNLOAD = "https://archive.org/download/"

# Cover candidates, best first. `format` is Archive.org's own
# classification and is far more stable than the filename spelling.
#
#   00_coverscreenshot.jpg  the box art proper, where an item has one
#   Emulator Screenshot     the game itself; every softwarelibrary item has one
#   Item Tile               `__ia_thumb.jpg`, the last resort
_COVER_STEM = "00_coverscreenshot"
_COVER_FORMATS = ("Emulator Screenshot", "Item Tile")

# What the host is willing to call an image. An entry whose extension is
# not here is skipped rather than fetched and posted as a "cover".
_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


class EnrichRefused(Exception):
    """This rom cannot be enriched, and the message says why."""


def _size_of(entry: dict) -> int:
    """`files[].size` is a decimal string, or absent. Never an int."""
    raw = entry.get("size")
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0


def _extension(name: str) -> str:
    return posixpath.basename(name).rsplit(".", 1)[-1].lower() if "." in name else ""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        identifier = (rom.extra.get("source_id") or "").strip()
        if not identifier:
            raise EnrichRefused(
                f"rom {rom.rom_id} ({rom.filename or rom.name!r}) carries no "
                f"Archive.org identifier, and this plugin will not guess one: "
                f"searching for the rom's name and taking the top hit would "
                f"write another game's title and cover into the library. Pass "
                f"the identifier with --source-id."
            )

        item = self._metadata(identifier)
        metadata = item.get("metadata")
        if not isinstance(metadata, dict) or not metadata:
            # A 200 with `{}` is how Archive.org answers for an identifier
            # that does not exist.
            raise EnrichRefused(
                f"Archive.org has no item {identifier!r} (its metadata endpoint "
                f"returned nothing)"
            )

        patch: dict = {}

        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            patch["name"] = title.strip()

        cover = self._cover(item.get("files"))
        if cover is not None:
            patch["artwork_url"] = (
                DOWNLOAD + quote(identifier, safe="") + "/" + quote(cover["name"])
            )
            patch["artwork_filename"] = f"cover.{_extension(cover['name'])}"

        # An item with neither a title nor a cover yields an empty patch,
        # which the host reads as "nothing to change" and acts on by
        # leaving RomM alone. That is the correct outcome, not an error.
        return MetadataPatch(**patch)

    def _metadata(self, identifier: str) -> dict:
        url = METADATA + quote(identifier, safe="")
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise EnrichRefused(
                f"Archive.org returned HTTP {response.status_code} for the "
                f"metadata of {identifier!r}"
            )
        try:
            item = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            # Rate limiting and maintenance pages both arrive as 200 + HTML.
            raise EnrichRefused(
                f"Archive.org's metadata for {identifier!r} was not JSON: {exc}"
            ) from exc
        if not isinstance(item, dict):
            raise EnrichRefused(
                f"Archive.org's metadata for {identifier!r} was not an object"
            )
        return item

    def _cover(self, files) -> dict | None:
        """The best cover in `files[]`, or None if the item has no image.

        Priority is by *kind* first and size second, so an item with real
        box art never has it beaten by a larger screenshot.
        """
        images = [
            entry
            for entry in (files or [])
            if isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and _extension(entry["name"]) in _IMAGE_EXTENSIONS
        ]
        if not images:
            return None

        named = [
            entry
            for entry in images
            if posixpath.basename(entry["name"]).lower().startswith(_COVER_STEM)
        ]
        if named:
            return max(named, key=_size_of)

        for wanted in _COVER_FORMATS:
            matching = [entry for entry in images if entry.get("format") == wanted]
            if matching:
                # Largest wins: the thumbnail of a screenshot is also an
                # "Emulator Screenshot" on some items.
                return max(matching, key=_size_of)
        return None
