"""Archive.org search, at the scale of a whole collection.

Two things this has to be at once: a search box, and a way to enumerate
24,746 items. `index.py` holds the part that makes the second possible --
`advancedsearch.php` will not page past 10,000 results but will answer any
size in one request if you do not ask it for a page -- and the reasoning
for reaching collection scale that way rather than through Archive.org's
scrape API is written down there.

What this module adds on top:

**`platform` is honoured, and it is a RomM slug.** It used to be ignored,
and `SearchResult.platform` used to be set to Archive.org's own emulator
id -- so a caller filtering on `genesis` got nothing back that it could
use, and a caller reading the field got `megadriv`. Both go through
`platforms.py` now: asking for `genesis` searches
`emulator:("genesis" OR "megadriv" OR "megadrij")`, because those are
three spellings of one machine, and every result names the RomM slug. A
platform this source has nothing under fails visibly rather than quietly
widening to everything.

**Collections are configurable, and the default now reaches the
consoles.** It was `["softwarelibrary"]`, which is the Archive's
*software* umbrella and does not contain the Console Living Room. That is
measured, not assumed: `softwarelibrary` holds 250,382 items,
`consolelivingroom` holds 24,746, and **212** items are in both. So out
of the box this plugin could not see a single Mega Drive cartridge. The
default is now both collections; an operator who wants one of them says
so, and any other Archive.org collection works the same way.

**`stream_only` is carried, not filtered.** Archive.org marks the items it
will only play in a browser, and 6,816 of the collection's 24,746 are
marked. Those are not junk: they are the `stream` capability's whole
population, and dropping them from search would put that capability out
of reach. `downloadable_only` exists for the operator who is bulk
importing and does not want to be shown items the importer will refuse.
"""

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

from .controls import extract as extract_controls
from .index import MAX_ROWS, Index, IndexUnavailable, build_query, escape
from .platforms import emulators_for, platform_for

DETAILS = "https://archive.org/details/"

#: Both halves of the Archive's software, because they really are two.
DEFAULT_COLLECTIONS = ["softwarelibrary", "consolelivingroom"]

STREAM_ONLY = "stream_only"

#: What one `search` reply can carry, and it is the host's limit rather
#: than Archive.org's.
#:
#: `protocol.MAX_MESSAGE_CHARS` caps an RPP frame at 8 MiB, and a
#: `SearchResult` from this plugin serialises to 467-602 characters --
#: measured over two captured pages, the larger figure being the one with
#: long titles and four-entry collection lists. 8 MiB / 602 is about
#: 13,900, so 12,000 leaves a real margin. Verified from the other side
#: too: 11,893 results (every downloadable Mega Drive item) came back
#: intact, and asking for all 24,746 did not -- it exceeded the frame,
#: which the host reports as *"the stream is now desynchronised and the
#: peer must be killed"*.
#:
#: Asking for more is **refused**, not quietly truncated. Truncation would
#: answer "how big is this collection" with a number this plugin made up,
#: and the operator would have no way to tell. The refusal names the two
#: knobs that make the ask fit.
MAX_RESULTS = 12000


class SearchRefused(Exception):
    """This search cannot be run, and the message says why."""


def _as_list(value) -> list[str]:
    """Archive.org returns `collection` as a list, or as a bare string when
    an item is in exactly one collection."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _text(value) -> str:
    """`extra` is `dict[str, str]`; upstream fields are whatever they are."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value if v is not None)
    return "" if value is None else str(value)


def _max_rows(config: dict) -> int:
    """`max_rows` from config, clamped, with a bad value ignored.

    A typo in a config file must not be able to ask Archive.org for a
    million rows, and must not be able to stop the plugin working either.
    """
    try:
        value = int(config.get("max_rows") or 0)
    except (TypeError, ValueError):
        return MAX_ROWS
    return value if 0 < value <= MAX_ROWS else MAX_ROWS


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        config = self.ctx.config or {}
        collections = config.get("collections") or DEFAULT_COLLECTIONS
        downloadable_only = bool(config.get("downloadable_only"))

        if limit > MAX_RESULTS:
            raise SearchRefused(
                f"one search reply can carry about {MAX_RESULTS} results -- "
                f"the Hub caps an RPP message at 8 MiB and a result costs "
                f"~600 characters -- and {limit} were asked for. Narrow the "
                f"ask rather than losing part of the answer silently: filter "
                f"by platform, set downloadable_only=true to drop the "
                f"stream-only items, or scope `collections` to one "
                f"collection."
            )

        emulators = None
        wanted = (platform or "").strip()
        if wanted:
            emulators = emulators_for(wanted)
            if not emulators:
                # Not a silent empty result: the operator asked a precise
                # question, and the honest answer names the reason.
                raise SearchRefused(
                    f"this plugin files nothing under the platform {wanted!r}: "
                    f"no Archive.org emulator id in archive_org/platforms.py "
                    f"maps to it. Search without a platform filter to see what "
                    f"the configured collections do hold."
                )

        q = build_query(
            query,
            collections,
            emulators=emulators,
            downloadable_only=downloadable_only,
        )

        index = Index(self.ctx.http, max_rows=_max_rows(config))
        try:
            docs = index.fetch(q, limit)
        except IndexUnavailable as exc:
            raise SearchRefused(str(exc)) from exc

        results: list[SearchResult] = []
        for doc in docs:
            result = self._result(doc, known_controls=index.included_notes)
            if result is not None:
                results.append(result)
        return results

    def _result(self, doc: dict, *, known_controls: bool) -> SearchResult | None:
        identifier = doc.get("identifier")
        title = doc.get("title")
        if not identifier or not title:
            # Items without a title are unusable downstream; skip rather
            # than invent one.
            return None

        collection = _as_list(doc.get("collection"))
        emulator = _text(doc.get("emulator"))
        # The index asks for `notes` -- one of the three places control
        # information lives -- whenever the read can afford the bytes.
        # Answering "does this item have any" is then free, and saves a
        # caller a metadata round trip per item spent finding out there
        # was nothing. When the read could *not* afford it the answer is
        # `unknown`, never `false`: a large read reporting that 11,893
        # Mega Drive items have no control information would be a claim
        # about Archive.org, and a false one.
        if known_controls:
            has_controls = "true" if extract_controls(doc, str(identifier)) else "false"
        else:
            has_controls = "unknown"

        try:
            return SearchResult(
                source_id=identifier,
                title=title if isinstance(title, str) else str(title),
                # The RomM slug, not Archive.org's emulator id. None when
                # the emulator is not in the table -- the importer is
                # where that becomes a refusal, and a search that hid such
                # an item would hide the fact that it needs mapping.
                platform=platform_for(emulator),
                size_bytes=doc.get("item_size"),
                url=f"{DETAILS}{identifier}",
                extra={
                    "stream_only": ("true" if STREAM_ONLY in collection else "false"),
                    "collections": ",".join(collection),
                    "emulator": emulator,
                    "emulator_ext": _text(doc.get("emulator_ext")),
                    "has_controls": has_controls,
                },
            )
        except (ValidationError, TypeError, ValueError):
            # item_size and emulator are whatever upstream put there, and
            # size_bytes is a ge=0 field. One malformed doc used to raise
            # out of search() and cost the plugin every other result in
            # the response -- skip it, like the untitled docs above.
            return None


__all__ = [
    "DEFAULT_COLLECTIONS",
    "Search",
    "SearchRefused",
    "build_query",
    "escape",
]
