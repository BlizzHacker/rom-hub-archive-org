"""Cataloguing `softwarelibrary` in windows that cannot overlap.

250,509 items (2026-08-08), the largest source this project has reached.
The whole difficulty is not enumerating it -- that is forty-odd polite
requests -- it is finding **units whose declared totals sum to the
collection's**, because without that the headline number is a lie in the
direction that flatters.

## Sub-collections are the obvious partition and are not one

`softwarelibrary_c64` holds 98,843 items, `_apple` 42,273, `_msdos`
23,200, `_atari` 15,570, `_amiga` 13,206, `_zx_spectrum` 12,305. Summing
them looks like a census and is not: they **overlap**. In one 500-item
sample 440 items were in `softwarelibrary_apple` *and* 433 in
`softwarelibrary_apple_contribs` *and* all 500 in `softwarelibrary`
itself. A catalogue built on them would report more entries than the
collection has while claiming to be complete -- the exact failure the
census capability exists to prevent, arriving as a bigger number.

## `item_size` windows are disjoint by construction

A numeric range partitions the collection with no overlap and no gap,
provided every item has the field. Measured, live, and re-checked at the
start of **every** build because the whole design rests on it::

    collection:(softwarelibrary) AND NOT item_size:[* TO *]   ->  0

The last window ends on Lucene's open bound, `[N TO *]`, rather than on a
constant: an item larger than a number somebody guessed would otherwise
fall outside the partition and be lost silently. With those two
properties the windows tile `[0, inf)` exactly, so their totals *must*
sum to the collection's -- and each window's total comes from its own
`rows=0` request, a denominator the enumeration had no hand in producing.

Measured on 2026-08-08: the 27 windows below summed to **250,509**, and
`collection:(softwarelibrary)` answered **250,509** to a separate
`rows=0`. The design note in `docs/DESIGN.md` recorded 250,398 on
2026-08-01; the collection gained 111 items in the week between, which is
what a live source does and why the sum is checked against the service
rather than against a number in a document.

## Why the ladder is a constant

The boundaries below were derived by asking Archive.org where its
9,600th, 19,200th, ... smallest item sits. That derivation costs one
request per window, and `scope()` cannot afford it: a scope failure fails
the **whole build**, not one unit, and a two-sided `item_size` range
query was measured at 0.75s on a quiet service and 2.0s on a busy one --
27 of them is 20 to 54 seconds against the host's 30-second call budget.
A scope that is a coin-flip on service load is not a scope.

So `scope()` makes two cheap requests and no more, and **correctness does
not depend on the ladder being current**. The windows tile the size axis
whatever the boundaries are; a stale ladder only makes some window hold
more items than one response fits, which `enumerate` handles by splitting
it further with `Index.next_window` -- the same partitioner `search` uses.
Growth costs requests, never rows.

## What a unit is, and what it is not

A unit is `item_size:[<low> TO <high>]`, and the id **is** a Lucene
clause: `collection:(softwarelibrary) AND <unit id>` pasted into
`advancedsearch.php` returns that unit's declared total, so every row of
the completeness report can be checked by hand against the service it
came from. There is no classification here, unlike the No-Intro census, where
the units were items of genuinely different kinds -- a directory of
games, a 44 GB pack, a 928 GB CDN mirror. A size window is a slice of one
collection, so every window is the same kind of thing and every window is
walked.

## What is skipped, and what is kept anyway

Kept: items with no `emulator` (30,281 of them), items under mediatypes
that are not `software` (`movies` holds `msdos_Epidemic_1983`, `texts`
holds `undertale_20260410` -- the mediatype does not decide whether an
item is a program), and items Archive.org marks `stream_only` (5,941),
which are recorded as such rather than dropped. An item whose `emulator`
is missing or is not in `platforms.EMULATOR_PLATFORMS` is catalogued with
**no platform**: counted and searchable, never filed under a plausible
neighbour.

Skipped, named and counted: `mediatype:collection` items -- 200 of them,
`fav-naira92` and `The MicroCom Collection` and the like. Those are
listings *of* items, and cataloguing a listing as if it were a game is
how a catalogue ends up with more entries than the collection has.

## Digests

There are none. `advancedsearch.php` publishes no hash at item level, and
the metadata endpoint that does would be one request per item -- 250,509
of them. So `rom_hub.grouping` merges these rows on the parsed name
alone, which is the weaker half of its evidence ladder. Said here because
the No-Intro census *does* carry sha1/md5/crc32 and collapses two uploads
of one set on proof; this one cannot, and a reader comparing the two
distinct-game counts should know why.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from rom_hub_sdk import CensusPage, CensusProvider, CensusRecord, CensusUnit

from .index import (
    MAX_RESPONSE_BYTES,
    OPEN,
    Index,
    IndexUnavailable,
    parse_size_clause,
    size_clause,
    size_window,
)
from .platforms import platform_for

#: The collection this census is for. Configurable, because the partition
#: is a property of `item_size` and not of `softwarelibrary` -- but the
#: ladder below was measured against this one.
DEFAULT_CENSUS_COLLECTION = "softwarelibrary"

#: Fields read per item. `collection` is 156 of the measured 272 bytes per
#: document and is asked for anyway: it is the only thing that says
#: whether an item can be imported or only streamed, and a catalogue that
#: could not tell those apart would promise downloads it cannot deliver.
CENSUS_FIELDS = (
    "identifier",
    "title",
    "item_size",
    "emulator",
    "mediatype",
    "collection",
)

#: Rows asked for per page, and **not** the largest that would fit.
#:
#: The first real build asked for a whole 9,599-item window in one
#: request. It works: 2.5 MB of JSON, 2.3 seconds end to end through the
#: subprocess, measured. It also lost two windows out of twenty-seven to a
#: transient Archive.org slowdown, because a call that normally takes 2.3
#: seconds only has to be thirteen times slower once to exceed the host's
#: 30-second budget and be killed. Half the rows is half the bytes on the
#: one request most likely to spike, and it costs one extra request per
#: window against a service that answers a `rows=0` count in 0.3 seconds.
#:
#: `rom_hub.types.MAX_CENSUS_RECORDS_PER_PAGE` (10,000) is the host's
#: ceiling on a page; this is well under it, and under the byte budget
#: checked below.
PAGE_ROWS = 5000

#: Measured over six windows spread across the ladder: 262, 272, 272, 280,
#: 287 and 326 bytes per document with `CENSUS_FIELDS`. The largest full
#: window response was 2,755,087 bytes for 9,599 documents. Rounded up,
#: because an underestimate here is a failed request rather than a slow
#: one.
BYTES_PER_DOC = 320

#: A page that would not fit is not a theoretical problem -- it is what
#: `Index.read_smallest_first` halves `rows` for, and what the window
#: splitting below exists to absorb. The assertion is here so the two
#: numbers cannot drift apart unnoticed.
assert PAGE_ROWS * BYTES_PER_DOC < MAX_RESPONSE_BYTES

#: How long one capability call may spend before this plugin gives up on
#: its own terms, against the host's 30 seconds. Set with room for an
#: in-flight request to return and for a 5,000-record page to be
#: serialised back, because the point is to fail *inside* the budget: the
#: host enforces its ceiling by killing the subprocess, so a plugin that
#: overruns costs a process restart and a unit, where one that gives up
#: costs a unit and says why.
CALL_BUDGET_SECONDS = 20.0

#: Where the size windows begin, in bytes. Derived on 2026-08-08 by asking
#: `advancedsearch.php` for the item_size of the 9,600th smallest item in
#: what was left, twenty-six times. Each of the first twenty-six windows
#: held 9,599 or 9,600 items; the last held 946. See the module note for
#: why this is a constant and why nothing breaks when it goes stale.
SIZE_LADDER = (
    0,
    156_074,
    209_198,
    254_475,
    327_798,
    397_155,
    475_955,
    570_241,
    673_537,
    772_359,
    900_059,
    1_018_573,
    1_176_443,
    1_398_232,
    1_718_954,
    2_123_631,
    2_483_885,
    3_104_093,
    3_868_368,
    4_916_434,
    6_169_066,
    7_616_815,
    9_718_106,
    13_661_900,
    29_643_913,
    211_727_989,
    5_698_242_445,
)

#: The skip reasons, as constants: a reason spelled two ways splits into
#: two rows of the completeness report and reads as two different
#: findings.
SKIP_SUB_COLLECTION = (
    "a sub-collection listing, not a software item (mediatype:collection)"
)
SKIP_NO_IDENTIFIER = "the search index returned a document with no identifier"
SKIP_UNREPRESENTABLE = "the item's metadata would not fit a catalogue record"
SKIP_VANISHED = (
    "counted in the window but gone from the index by the time the window "
    "was read -- the collection changes while it is being read"
)


class CensusUnavailable(Exception):
    """A window could not be read, and the message says which part."""


@contextmanager
def _as_census_failure():
    """`IndexUnavailable` reaches the host as this module's own refusal.

    One exception type per plugin module keeps the failure legible in the
    `error` column of the completeness report -- and `index` raises for
    reasons this module has no better wording for than the one `index`
    already wrote.
    """
    try:
        yield
    except IndexUnavailable as exc:
        raise CensusUnavailable(str(exc)) from exc


class Census(CensusProvider):
    """`collection:softwarelibrary`, sliced by `item_size`, arithmetic kept."""

    # -- scope ----------------------------------------------------------

    def scope(self) -> list[CensusUnit]:
        """The size ladder as units, after checking the one thing it needs.

        Two requests, both cheap, and neither of them an enumeration in
        disguise:

        1. the collection's own `numFound`, which is the number the units'
           declared totals have to sum to and which goes into every
           window's label so the report prints what it is reconciling
           against;
        2. `NOT item_size:[* TO *]`, which **must** be zero. An item with
           no `item_size` sits in none of the windows, so it would be lost
           without ever appearing as a shortfall -- the one way this design
           can be silently wrong, checked on every build rather than once
           in a design note.
        """
        index = self._index()
        base = self._base_query()

        with _as_census_failure():
            declared = index.total(base)
            unsized = index.total(f"{base} AND NOT item_size:[* TO *]")

        if not declared:
            raise CensusUnavailable(
                f"{base!r} matched nothing on advancedsearch.php. That is "
                f"not an empty collection: it is a query that found no "
                f"items, and cataloguing zero of zero would be a "
                f"completeness claim about nothing."
            )

        if unsized is None:
            raise CensusUnavailable(
                "Archive.org would not say how many items of "
                f"{base!r} have no item_size. Every window of this census "
                "is an item_size range, so an unanswered question there is "
                "an unknown number of items in no window at all."
            )
        if unsized:
            raise CensusUnavailable(
                f"{unsized:,} items of {base!r} carry no item_size. The "
                f"windows of this census are item_size ranges, so those "
                f"items belong to no window and would be missing from the "
                f"catalogue without ever appearing as a shortfall. "
                f"Refusing to build a partition that cannot cover its own "
                f"collection."
            )

        units: list[CensusUnit] = []
        edges = sorted(set(SIZE_LADDER))
        for position, low in enumerate(edges, start=1):
            high = edges[position] - 1 if position < len(edges) else OPEN
            units.append(
                CensusUnit(
                    unit_id=size_clause(low, high),
                    label=(
                        f"item_size {low:,} to "
                        f"{'unbounded' if high == OPEN else format(high, ',')} "
                        f"bytes -- window {position} of {len(edges)} over a "
                        f"collection of {declared:,} items"
                    )[:500],
                    # Every window is a slice of one collection, so there
                    # is nothing here to classify. See the module note.
                    kind="roms",
                    # Fetched by `enumerate` from this window's own
                    # `rows=0`, not here: 27 range counts is 20 to 54
                    # seconds and a scope that overruns kills the build
                    # rather than one unit.
                    declared_total=None,
                    # A size window spans every machine in the collection.
                    # Stated as unknown rather than guessed; the records
                    # carry their own.
                    platform=None,
                    include=True,
                )
            )
        return units

    # -- walking --------------------------------------------------------

    def enumerate(self, unit: CensusUnit, cursor: str | None) -> CensusPage:
        """One window, in as many size-ordered slices as it takes.

        The first call fetches the window's declared total from its own
        `rows=0` request and carries it in the cursor, because a resumed
        build starts a fresh subprocess and this object remembers nothing.
        Every later call resumes at a byte count, never at an offset: the
        collection gains items while it is being read, and an offset into a
        result set that has shifted under you is how a walk silently skips
        rows.
        """
        low, high = _parse_unit_id(unit.unit_id)
        resume, seen, declared = _parse_cursor(cursor, low)

        index = self._index()
        base = self._base_query()

        with _as_census_failure():
            if declared is None:
                declared = index.total(size_window(base, low, high))
                if declared is None:
                    raise CensusUnavailable(
                        f"Archive.org would not count "
                        f"{size_window(base, low, high)!r}. Without the "
                        f"window's own total there is no denominator to "
                        f"check the walk against, and a walk that cannot be "
                        f"checked is a list, not a census."
                    )
            docs, asked = index.read_smallest_first(
                size_window(base, resume, high), PAGE_ROWS, list(CENSUS_FIELDS)
            )

        # `asked` is what the successful request actually asked for, which
        # is not always `PAGE_ROWS`: a response that would not fit is
        # retried with half the rows. Comparing against it rather than
        # against `PAGE_ROWS` is what keeps a shrunken read from being
        # mistaken for a finished window.
        if len(docs) < asked:
            return self._final_page(docs, seen, declared)
        return self._partial_page(unit, docs, resume, seen, declared)

    def _partial_page(
        self,
        unit: CensusUnit,
        docs: list[dict],
        resume: int,
        seen: int,
        declared: int,
    ) -> CensusPage:
        """A slice that filled its response, and where the next one starts.

        The documents came back smallest first, so the largest size in the
        page is a boundary the rest of the window sits at or above. Every
        document *at* that size is dropped from this page and read again in
        the next one: some of them are beyond the response and taking the
        ones that fit would split a size across two pages, which is the one
        way this partition could lose a row.
        """
        boundary = _largest_size(docs)
        if boundary is None or boundary <= resume:
            # Every document in a full response shares one byte count, and
            # there are more of them than a response holds. Refused by name
            # with the number in the message, as the No-Intro census
            # refuses an item it cannot read in one request -- a truncated
            # window would understate this unit's coverage silently.
            raise CensusUnavailable(
                f"more than {len(docs):,} items of "
                f"{unit.unit_id!r} share item_size {resume:,} exactly, so "
                f"no smaller window can be cut inside them and one response "
                f"cannot hold them. Refusing this window rather than "
                f"truncating it."
            )

        records, skipped = self._read_docs(
            [d for d in docs if _as_int(d.get("item_size")) != boundary]
        )
        walked = len(records) + sum(skipped.values())
        return CensusPage(
            records=records,
            cursor=_cursor(boundary, seen + walked, declared),
            declared_total=declared,
            skipped=skipped,
        )

    def _final_page(
        self, docs: list[dict], seen: int, declared: int
    ) -> CensusPage:
        """The slice that finished the window, reconciled against its total.

        Two requests seconds apart against a live collection can disagree,
        and the honest handling differs by direction:

        * **fewer walked than declared** -- items were counted and then
          removed, or were never returned. Named and counted as a skip, so
          the unit still balances and the report still shows the number.
        * **more walked than declared** -- items were added between the
          count and the read. The larger figure is the source's own more
          recent statement about the window, so it becomes the declared
          total and the report compares against it. Recorded here because
          revising a denominator upward is exactly the move that can hide
          a shortfall, and it must never happen anywhere but this branch.
        """
        records, skipped = self._read_docs(docs)
        walked = seen + len(records) + sum(skipped.values())

        if walked < declared:
            skipped[SKIP_VANISHED] = skipped.get(SKIP_VANISHED, 0) + (
                declared - walked
            )
        return CensusPage(
            records=records,
            cursor=None,
            declared_total=max(declared, walked),
            skipped=skipped,
        )

    # -- one item -------------------------------------------------------

    def _read_docs(self, docs: list[dict]) -> tuple[list[CensusRecord], dict[str, int]]:
        records: list[CensusRecord] = []
        skipped: dict[str, int] = {}
        for doc in docs:
            reason = self._skip_reason(doc)
            if reason is not None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            record = self._record(doc)
            if record is None:
                skipped[SKIP_UNREPRESENTABLE] = (
                    skipped.get(SKIP_UNREPRESENTABLE, 0) + 1
                )
                continue
            records.append(record)
        return records, skipped

    @staticmethod
    def _skip_reason(doc: dict) -> str | None:
        identifier = doc.get("identifier")
        if not isinstance(identifier, str) or not identifier.strip():
            return SKIP_NO_IDENTIFIER
        if str(doc.get("mediatype") or "").strip().lower() == "collection":
            return SKIP_SUB_COLLECTION
        return None

    @staticmethod
    def _record(doc: dict) -> CensusRecord | None:
        identifier = str(doc["identifier"]).strip()
        title = doc.get("title")
        if not isinstance(title, str) or not title.strip():
            # Eight items of the collection carry no title at all
            # (`c64__-`, `zzt_YAAAY`, `demoscene_-Trybit`, ...). The
            # identifier is what Archive.org files them under, so it is
            # what they are catalogued under -- an item with no name is
            # still an item, and dropping it would make the census
            # understate the source to keep its own bookkeeping tidy.
            title = identifier

        emulator = str(doc.get("emulator") or "").strip()
        collections = doc.get("collection")
        if isinstance(collections, str):
            collections = [collections]
        elif not isinstance(collections, list):
            collections = []

        extra = {}
        if emulator:
            extra["emulator"] = emulator[:100]
        mediatype = str(doc.get("mediatype") or "").strip()
        if mediatype:
            extra["mediatype"] = mediatype[:50]
        if "stream_only" in collections:
            # Archive.org's own marker, and the same signal `importer.py`
            # refuses on. Carried so the catalogue can say "playable, not
            # downloadable" instead of promising a fetch that would be
            # refused one call later.
            extra["stream_only"] = "1"

        try:
            return CensusRecord(
                record_id=identifier[:600],
                title=title[:500],
                # None when the emulator is absent or is not in the table.
                # Catalogued anyway: the item exists whether or not this
                # plugin knows which machine it is for, and a plausible
                # neighbour is the one answer that would be worse than
                # none.
                platform=platform_for(emulator),
                size_bytes=_as_int(doc.get("item_size")),
                url=f"https://archive.org/details/{identifier}",
                extra=extra,
            )
        except Exception:  # noqa: BLE001 - one odd item, not the window
            return None

    # -- query ----------------------------------------------------------

    def _index(self) -> Index:
        """An index reader that stops before the host's budget does.

        See `CALL_BUDGET_SECONDS`. The deadline is set here rather than
        inside `Index` so `search` -- which makes one small request and
        answers a person -- keeps its unbudgeted behaviour.
        """
        return Index(
            self.ctx.http, deadline=time.monotonic() + CALL_BUDGET_SECONDS
        )

    def _base_query(self) -> str:
        collection = str(
            self.ctx.config.get("census_collection") or DEFAULT_CENSUS_COLLECTION
        ).strip()
        if not collection:
            collection = DEFAULT_CENSUS_COLLECTION
        return f"collection:({collection})"


# -- helpers -------------------------------------------------------------


def _parse_unit_id(unit_id: str) -> tuple[int, int | str]:
    """The window a stored unit id names.

    The id is `size_clause`'s output verbatim -- `item_size:[0 TO 156073]`
    -- so `collection:(softwarelibrary) AND <unit id>` is literally the
    query whose `numFound` is that unit's declared total. The host stores
    a unit's id and hands it back on a resumed build without its `extra`,
    so the bounds have to survive in the id; making them survive as
    something anyone can paste into `advancedsearch.php` costs nothing and
    makes every row of the completeness report checkable by hand.
    """
    try:
        return parse_size_clause(unit_id)
    except ValueError as exc:
        raise CensusUnavailable(
            f"unit id {unit_id!r} is not an item_size window ({exc}). This "
            f"census has no other kind of unit, and guessing at bounds is "
            f"how a window ends up overlapping its neighbour."
        ) from exc


def _cursor(low: int, seen: int, declared: int) -> str:
    """Where to resume, what has been accounted for, and against what.

    All three, because a resumed build starts a fresh subprocess: the
    plugin that continues this window is not the one that started it and
    remembers neither the running total nor the denominator. Re-fetching
    the denominator instead would be a second `rows=0` against a
    collection that has moved, which is a different number to reconcile
    against than the one the earlier pages were counted under.
    """
    return f"{low}|{seen}|{declared}"


def _parse_cursor(cursor: str | None, low: int) -> tuple[int, int, int | None]:
    if not cursor:
        return low, 0, None
    parts = str(cursor).split("|")
    if len(parts) != 3:
        raise CensusUnavailable(
            f"cursor {cursor!r} is not 'low|seen|declared'. Resuming from a "
            f"cursor this plugin did not write would resume at the wrong "
            f"byte count, and the rows between the two would be missing "
            f"from the catalogue without being missing from its arithmetic."
        )
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise CensusUnavailable(f"cursor {cursor!r} is malformed: {exc}") from exc


def _largest_size(docs: list[dict]) -> int | None:
    sizes = [s for s in (_as_int(d.get("item_size")) for d in docs) if s is not None]
    return max(sizes) if sizes else None


def _as_int(value) -> int | None:
    """An integer from a field Archive.org spells inconsistently.

    `item_size` arrives as a number from the search index and as a decimal
    string elsewhere. Anything else becomes None, which the host reports as
    "no declared total" -- distinct from zero, and deliberately so.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None
