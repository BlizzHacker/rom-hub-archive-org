"""Reading Archive.org's index at collection scale, and what that costs.

`search.py` used to send one `advancedsearch.php` request with `rows=limit`
and `page=1`. That is fine for a search box and useless for a collection:
the Console Living Room holds **24,746 items**, and nothing in the plugin
could see past the first page of them.

Getting the rest is not a matter of asking for more pages. The four
findings that shape this module are written down here rather than left to
be discovered again by the next person -- and note that only the first
two are about Archive.org. The limit that actually decides the shape of
this code is the Hub's own, in section 3.

## 1. `page` is what imposes the ceiling, not `rows`

`advancedsearch.php` refuses to page deeper than 10,000 results::

    rows=100  page=101  -> {"error": "[DEEP_PAGING] Requested results would
                            exceed the deep paging limit for this service,
                            10000 results; ..."}
    rows=1000 page=11   -> the same
    rows=2000 page=6    -> the same

and the error text itself names the way out: *"You may request any number
of results at one time if you do NOT specify any page."* Measured against
the live service::

    q=collection:(consolelivingroom) AND emulator:("genesis")
      AND NOT collection:(stream_only)
    rows=11000, no page  ->  10,045 docs in ~2 seconds

So a bulk read omits `page` entirely. Whether one response is then enough
is a separate question, answered in section 3. `page` is still used for
small asks, where it is the natural resume point -- see `Index.fetch`.

## 2. The scrape API is the documented answer and the wrong one

`/services/search/v1/scrape` is what that error message points at, and it
does work: cursor-paged, it returned all 24,746 items of the collection in
137 seconds over 25 pages of 1,000 -- with each successive page slower
than the last (3.5s early, 9.5s at the end). It has two disqualifying
properties.

**It does not apply a field filter.** Asked for
`collection:(consolelivingroom) AND emulator:("genesis")` on a fresh
connection it answered `total=24746` -- the whole collection -- and the
first page came back 36 genesis, 20 nes, 12 a2600, 5 gbcolor. The
`emulator:` clause is silently dropped. Every one of 33 per-emulator
queries returned the identical first 100 identifiers. A filter that is
accepted and ignored is worse than one that is rejected, because the
caller gets a plausible answer to a question it did not ask.

**Its result set is not reliably the one you asked for.** Consecutive
requests on one keep-alive connection returned each other's totals:
`collection:(nasa)` and `collection:(consolelivingroom)` both answered
`total=208822` in one run and both answered `total=24746` in another,
depending only on which query had been sent first.

Neither of those is a thing to build a plugin's idea of "the collection"
on, and both were reproduced from this workstation more than once. The
scrape endpoint is therefore **not used**. That is a finding about
Archive.org, not a preference, and it is why this module reaches
collection scale through `advancedsearch.php` alone.

## 3. The host caps a response at 4 MiB, and that is the real ceiling

Neither of the limits above is what actually stops a plugin reading a
whole collection. `ctx.http` is an RPC and the host refuses a response
over `broker.fetcher.MAX_RESPONSE_BYTES`::

    ResponseTooLarge: response from 'https://archive.org/advancedsearch.php'
    exceeded the 4194304-byte limit at 4198942 bytes; bulk transfer is a
    host concern, not a ctx.http one

That is the right rule -- an untrusted subprocess must not be able to ask
the host to buffer arbitrary amounts -- so the plugin is what has to fit
inside it. Two measurements decide how, taken against the 11,893
downloadable Mega Drive items:

    7 fields incl. notes   5,482,997 bytes   461 bytes/doc   over
    6 fields, no notes     3,093,609 bytes   260 bytes/doc   under
    5 fields               1,827,164 bytes   154 bytes/doc

So `notes` -- the control boilerplate, ~400 characters of it repeated on
every Mega Drive item -- is nearly half the payload. It is asked for only
while the result set is small enough to afford it; past that the
`has_controls` flag on a search result goes quiet rather than the search
failing, and `metadata` still reads the field per item.

And past ~14,000 documents even the lean field set will not fit, which is
why `_collect` **partitions the query** rather than paging it. `item_size`
is indexed, is present on all 24,746 items of the collection (checked:
`NOT item_size:[* TO *]` matches zero), and a numeric range splits any
query into two disjoint halves that between them lose nothing. Bisect
until each half fits, then read each half with one page-less request.
That is the only shape that gets past 10,000 *and* stays under 4 MiB,
because `page` cannot reach past 10,000 and the page-less form has no
offset to chunk with.

## 4. Be a considerate client

Everything here is a GET against a free public service that rate-limits,
so: responses are cached for the life of the process (`Index` is
constructed once per capability call, and one `search()` that pages will
hit the cache rather than re-ask), failures back off rather than retry
immediately, and a request that fails twice **halves `rows` and tries
again** -- a caller that asked for 20,000 and can be given 5,000 is better
served than one given an exception.
"""

from __future__ import annotations

import json
import time

ENDPOINT = "https://archive.org/advancedsearch.php"

#: Fields asked for on every index read. `emulator` is what platform
#: routing needs and `collection` is what tells import from stream. All
#: are indexed, verified live.
FIELDS = (
    "identifier",
    "title",
    "collection",
    "item_size",
    "emulator",
    "emulator_ext",
)

#: `notes` is one of the three places Archive.org keeps control
#: information -- see `controls.py` -- and is ~400 characters of
#: boilerplate on every Mega Drive item, which is nearly half the bytes of
#: a large response. Asked for only when the result set can afford it.
NOTES_FIELD = "notes"

#: The host's own ceiling on one `ctx.http` response. Copied rather than
#: imported: a plugin has no access to `rom_hub`, by design.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: Measured against the live service, over 11,893 documents. Rounded up
#: from 461 and 260 -- an underestimate here is a failed request.
BYTES_PER_DOC_WITH_NOTES = 512
BYTES_PER_DOC = 288

#: Two thirds of the cap. The per-document figures are averages over one
#: corpus and a collection of unusually long titles would beat them, so
#: the margin is what keeps a miscalculation from being a failure.
BUDGET = 0.66


def _rows_that_fit(with_notes: bool) -> int:
    per_doc = BYTES_PER_DOC_WITH_NOTES if with_notes else BYTES_PER_DOC
    return int(MAX_RESPONSE_BYTES * BUDGET / per_doc)


#: ~5,400 with `notes`, ~9,600 without.
SAFE_ROWS_WITH_NOTES = _rows_that_fit(True)
SAFE_ROWS = _rows_that_fit(False)

#: Bigger than any Archive.org item by a wide margin (4 TiB), and the
#: upper bound the size bisection starts from.
#:
#: `_collect` uses it as a closed upper bound because it only ever needs a
#: window big enough to hold the rest of a result set it is peeling. Code
#: that needs the windows to *provably* tile the whole size axis wants
#: `OPEN` instead -- see `size_window`.
MAX_ITEM_SIZE = 2**42

#: Lucene's open bound. `item_size:[N TO *]` is the only upper bound that
#: cannot be beaten by an item larger than a constant somebody guessed, so
#: a partition whose completeness is the claim ends on it.
OPEN = "*"

#: A bisection that cannot terminate must stop anyway. Reached only if a
#: single `item_size` value holds more documents than fit in one
#: response, which no observed collection does.
MAX_PARTITION_REQUESTS = 200

#: What a caller is told to stay under if it wants to keep using `page`.
#: Not enforced -- `page` works up to the deep-paging limit and the
#: refusal below quotes that -- but it is the size at which asking for one
#: request at a time stops being the sensible shape.
PAGE_CEILING = 1000

#: `advancedsearch.php` refuses to page past this. Not a number this
#: module chose -- see the DEEP_PAGING quote above.
DEEP_PAGING_LIMIT = 10000

#: The largest single bulk request this will make. Above the whole Console
#: Living Room (24,746) with room to spare, and low enough that a config
#: typo cannot ask Archive.org for a million rows.
MAX_ROWS = 50000

#: How many times one request is attempted before its `rows` is halved.
ATTEMPTS_PER_SIZE = 2

#: How far `rows` may be halved before giving up. 20,000 -> 625.
HALVINGS = 5


class IndexUnavailable(Exception):
    """Archive.org's index could not be read, and the message says why.

    Reaches an operator the same way every other refusal in this plugin
    does: as the `error` column of a FAILED job.
    """


def escape(term: str) -> str:
    r"""Make one term safe to sit inside a Lucene quoted phrase.

    Quoting is what neutralises Lucene's operators: `-`, `&`, `:` and the
    rest are literal text inside `"..."`, which is why real titles like
    `r-type` and `sonic & knuckles` need no special handling. Only the two
    characters that can *end* the phrase early have to be escaped -- the
    quote itself, and the backslash that would otherwise consume the
    escape. Backslash first, or escaping the quote would then
    double-escape.
    """
    return term.replace("\\", "\\\\").replace('"', '\\"')


def build_query(
    query: str | None,
    collections: list[str],
    *,
    emulators: list[str] | None = None,
    downloadable_only: bool = False,
) -> str:
    '''The advancedsearch `q`, with the user's terms confined to the title.

    This used to be `({query}) AND collection:({scope})`, which put a bare
    term into Archive.org's *default* field -- effectively the whole
    record, description and subject tags and uploader notes included --
    and then let relevance ranking sort it out. It did not sort it out.
    Searching `sonic` returned `Die Hard (2004)(Die Chefrocker)`;
    `oregon trail` returned `Great Hierophant's .WOZ Archive`;
    `prince of persia` returned `Total Replay`. Those items match
    somewhere in their metadata, which is not a claim anybody searching a
    ROM library is making.

    So each term is required to appear **in the title**, and all of them
    must:

        title:("prince" AND "of" AND "persia") AND collection:(softwarelibrary)

    Two deliberate choices about how far to narrow:

    **Terms, not a phrase.** `title:("prince of persia")` also fixes the
    junk, but it demands adjacency and word order -- verified live, it
    returns *zero* results for `hedgehog sonic` and `persia prince`, while
    the AND-of-terms form answers both with the Sonic and Prince of Persia
    titles. A search that silently returns nothing because the words were
    typed in a different order is a worse bug than the one being fixed.

    **Title only, not title-or-identifier.** Checked live: adding
    `identifier:(...)` changed essentially nothing, because an
    Archive.org identifier already echoes the title. It is complexity
    that buys no recall.

    An empty query drops the clause entirely rather than emitting
    `title:()`, which is a syntax error, or `title:("")`, which matches
    nothing -- browsing a collection has to stay possible, and browsing
    is the whole point of a collection-scale read.

    `emulators` narrows to Archive.org's own machine ids and
    `downloadable_only` drops the `stream_only` half of the collection.
    Both are clauses Archive.org's *advancedsearch* honours; neither
    survives the scrape API, which is one of the reasons this module does
    not use it.
    '''
    clauses = []

    terms = [t for t in (query or "").split() if t]
    if terms:
        inner = " AND ".join(f'"{escape(t)}"' for t in terms)
        clauses.append(f"title:({inner})")

    scope = " OR ".join(collections)
    clauses.append(f"collection:({scope})")

    if emulators:
        wanted = " OR ".join(f'"{escape(e)}"' for e in emulators)
        clauses.append(f"emulator:({wanted})")

    if downloadable_only:
        # Archive.org's own marker for "playable in a browser, not
        # downloadable". Excluding it here means a bulk import never plans
        # a fetch that `importer.py` would refuse one call later.
        clauses.append("NOT collection:(stream_only)")

    return " AND ".join(clauses)


def size_clause(low: int, high: int | str = MAX_ITEM_SIZE) -> str:
    """One `item_size` range, inclusive at both ends.

    The one place this project writes such a clause, because two places
    would eventually disagree about whether the bounds are inclusive --
    and a partition whose windows overlap by one byte double-counts every
    item of exactly that size, while one that gaps by one byte loses them.
    `high` may be `OPEN`, which is what the last window of a partition
    uses.
    """
    return f"item_size:[{low} TO {high}]"


def parse_size_clause(clause: str) -> tuple[int, int | str]:
    """`size_clause` read back, or a ValueError naming what was wrong.

    `census.py` uses the clause itself as a unit id, so this is what turns
    a stored id back into the window it names.
    """
    text = str(clause).strip()
    if not (text.startswith("item_size:[") and text.endswith("]")):
        raise ValueError(f"{clause!r} is not an item_size range")
    low, sep, high = text[len("item_size:[") : -1].partition(" TO ")
    if not sep:
        raise ValueError(f"{clause!r} has no 'TO' between its bounds")
    return int(low), (OPEN if high == OPEN else int(high))


def size_window(q: str, low: int, high: int | str = MAX_ITEM_SIZE) -> str:
    """`q` narrowed to one `item_size` range."""
    return f"{q} AND {size_clause(low, high)}"


class Index:
    """One capability call's view of `advancedsearch.php`.

    Holds the response cache, so a capability that reads the index twice
    for the same query pays for it once. Deliberately not shared between
    calls: a plugin process is short-lived, and a cache that outlived a
    call would start answering with yesterday's collection.
    """

    def __init__(self, http, *, max_rows: int = MAX_ROWS, deadline: float | None = None):
        self._http = http
        self._max_rows = max(1, min(int(max_rows), MAX_ROWS))
        #: A `time.monotonic()` value past which no further request is
        #: made. Optional, and `search` does not set it.
        #:
        #: **It exists because overrunning the host's budget is worse than
        #: failing inside it.** `broker.host` enforces its 30-second
        #: ceiling by killing the subprocess -- the only portable way to
        #: interrupt a blocking pipe read -- so a plugin that overruns does
        #: not fail one call, it dies, and the host has to start another
        #: process. Measured on the first real census build: two windows of
        #: twenty-seven were lost that way to a transient slowdown, on a
        #: call that normally takes 2.3 seconds. Giving up while there is
        #: still time to say so turns a kill into a legible failure of one
        #: unit.
        self.deadline = deadline
        self._cache: dict[tuple, list[dict]] = {}
        #: Whether the last `fetch` asked for `notes`. A caller reading
        #: control information out of the documents has to know the
        #: difference between "this item has none" and "this read could
        #: not afford to ask" -- reporting the second as the first is a
        #: statement about the corpus that would simply be untrue.
        self.included_notes = True

    def fetch(self, q: str, limit: int, *, page: int | None = None) -> list[dict]:
        """Up to `limit` documents for `q`.

        Three shapes, and which one runs is decided by size alone:

        * **paged** -- `page` given and the whole ask inside Archive.org's
          deep-paging limit. The natural resume point for a search box.
        * **one request** -- no `page`, and a result set that fits in one
          response. The common bulk case.
        * **partitioned** -- a result set too big for one response, read
          as consecutive `item_size` windows. The only way past 10,000,
          because `page` cannot reach there and the page-less form has no
          offset to chunk with.
        """
        limit = max(1, min(int(limit), self._max_rows))
        paged = page is not None and (page * limit) <= DEEP_PAGING_LIMIT
        if page is not None and not paged:
            raise IndexUnavailable(
                f"Archive.org will not page past {DEEP_PAGING_LIMIT} results "
                f"(asked for page {page} of {limit}), and a bulk read cannot "
                f"resume from a page number beyond it. Ask for the results in "
                f"one request instead: this plugin does that automatically for "
                f"a limit over {PAGE_CEILING}."
            )

        key = (q, limit, page if paged else None)
        if key in self._cache:
            return self._cache[key]

        if paged or limit <= SAFE_ROWS_WITH_NOTES:
            self.included_notes = True
            docs = self._read(q, limit, page if paged else None, with_notes=True)
        else:
            docs = self._collect(q, limit)
        self._cache[key] = docs
        return docs

    # -- one query, one response ----------------------------------------

    def _read(
        self,
        q: str,
        rows: int,
        page: int | None,
        *,
        with_notes: bool,
        fields: list[str] | None = None,
        sort: str | None = None,
    ) -> list[dict]:
        """`rows` documents in one response, retrying and then shrinking.

        A caller that asked for 4,000 and can be given 1,000 is better
        served than one given an exception, so a request that fails twice
        halves `rows` and tries again.
        """
        return self._read_sized(
            q, rows, page, with_notes=with_notes, fields=fields, sort=sort
        )[0]

    def read_smallest_first(
        self, q: str, rows: int, fields: list[str]
    ) -> tuple[list[dict], int]:
        """Up to `rows` documents for `q`, smallest `item_size` first.

        Returns the documents **and the number of rows the successful
        request actually asked for**, which is the only way a caller can
        tell "that is the whole of `q`" from "the response would not fit
        and `rows` was halved". `census.py` needs that distinction: the
        first means a window is finished, the second means it has to be
        split further, and guessing wrong in either direction is a wrong
        completeness claim rather than a slow one.

        Sorted ascending because a partition resumes from the largest size
        it has seen. Unsorted, the documents that came back are an
        arbitrary subset and no size bound can be drawn under them.
        """
        return self._read_sized(
            q, rows, None, with_notes=False, fields=fields, sort="item_size asc"
        )

    def _read_sized(
        self,
        q: str,
        rows: int,
        page: int | None,
        *,
        with_notes: bool,
        fields: list[str] | None = None,
        sort: str | None = None,
    ) -> tuple[list[dict], int]:
        for _ in range(HALVINGS + 1):
            docs = self._attempt(
                q, rows, page, with_notes=with_notes, fields=fields, sort=sort
            )
            if docs is not None:
                return docs, rows
            if rows <= 1:
                break
            rows = max(1, rows // 2)

        raise IndexUnavailable(
            f"Archive.org's search endpoint did not answer for {q!r}, over "
            f"{HALVINGS + 1} attempts ending at rows={rows}. It rate-limits, "
            f"and both a rate-limited response and a maintenance page arrive "
            f"as something that is not JSON; try again, or narrow the query."
        )

    # -- more than one response's worth ---------------------------------

    def _collect(self, q: str, limit: int) -> list[dict]:
        """`limit` documents for `q`, however many responses that takes.

        The result set is peeled off in `item_size` order: ask where the
        N-th smallest document sits, take everything up to that size, then
        start the next window one byte above it. `item_size` is indexed,
        numeric, and present on **every** item of the collection this was
        measured against (`NOT item_size:[* TO *]` matches zero), so the
        windows are disjoint, ordered, and between them lose nothing.

        **Why a rank lookup rather than a bisection on the size range.**
        Bisecting `[0, 2**42]` is the obvious implementation and is far too
        slow: item sizes are heavily skewed small, so the first fifteen
        splits all put the entire corpus in the lower half, and each one
        costs a round trip. There is a 30-second wall-clock budget on a
        plugin call (`broker.host`), which that spends before reading a
        single document. Asking the service where the N-th document sits
        -- `sort[]=item_size asc, rows=1, page=N` -- costs one request and
        lands the boundary exactly, so a window is one lookup plus one
        read. Measured at 0.65s for the lookup.

        **Which is why the window size is what it is.** `page` on that
        lookup is subject to the same 10,000-result deep-paging limit as
        everything else, so a window may not exceed it -- and `SAFE_ROWS`,
        the response-budget ceiling, is 9,611. The two constraints are
        compatible only because the byte budget is the tighter one.

        `notes` is dropped for the whole read as soon as the total says it
        will not fit, rather than per window: a field present on some
        results and absent from others, depending on where a boundary
        landed, would be a worse answer than one consistently absent.
        """
        total = self.total(q)
        with_notes = total is not None and total <= SAFE_ROWS_WITH_NOTES
        self.included_notes = with_notes
        ceiling = min(SAFE_ROWS_WITH_NOTES if with_notes else SAFE_ROWS,
                      DEEP_PAGING_LIMIT)

        if total is not None and total <= ceiling:
            return self._read(q, min(limit, total), None, with_notes=with_notes)

        out: list[dict] = []
        seen: set = set()
        low = 0
        requests = 0

        while len(out) < limit and requests < MAX_PARTITION_REQUESTS:
            wanted = min(ceiling, limit - len(out))
            bounded, next_low = self.next_window(q, low, ceiling)
            requests += 1
            self._absorb(
                self._read(bounded, wanted, None, with_notes=with_notes),
                out, seen, limit,
            )
            requests += 1
            if next_low is None:
                break
            low = next_low

        return out

    def next_window(
        self, q: str, low: int, ceiling: int, high: int | str = MAX_ITEM_SIZE
    ) -> tuple[str, int | None]:
        """The next `item_size` slice of `q` at or above `low`, and its successor.

        Returns the bounded query for at most `ceiling` documents, and the
        `low` that the window after it starts from -- `None` when this
        window is the tail of `[low, high]` and nothing follows it.

        Factored out of `_collect` so `census.py` steps the same way rather
        than growing a second partitioner. Two implementations of "where
        does the next window start" is exactly how a catalogue ends up
        claiming a total its own windows do not add up to.
        """
        remaining = size_window(q, low, high)
        boundary = self.size_at_rank(remaining, ceiling)

        if boundary is None:
            # Fewer than `ceiling` documents left: the tail is one read.
            return remaining, None

        if boundary > low:
            # **Below** the boundary, not up to it. The boundary size may
            # be shared by several documents -- Archive.org's `item_size`
            # is a byte count and nothing makes it unique -- and a window
            # that included some of them would have to either exceed
            # `ceiling` or drop the rest. Excluding the size entirely puts
            # every document that has it in the next window, where they are
            # read together.
            return size_window(q, low, boundary - 1), boundary

        # The boundary is the window's own first size, so one byte count
        # holds `ceiling` documents or more. Take that size alone and step
        # past it. This is the only case that can lose a document, and only
        # above `ceiling` items of exactly equal size -- which no observed
        # collection has.
        return size_window(q, low, low), low + 1

    @staticmethod
    def _absorb(part: list[dict], out: list[dict], seen: set, limit: int) -> None:
        """Add `part` to `out`, skipping identifiers already there.

        Windows are disjoint by construction, so this should never drop
        anything -- it is here because "should never" and "does not" are
        different claims when the boundaries come from a remote service.
        """
        for doc in part:
            identifier = doc.get("identifier")
            if identifier in seen:
                continue
            seen.add(identifier)
            out.append(doc)
            if len(out) >= limit:
                return

    def size_at_rank(self, q: str, rank: int) -> int | None:
        """`item_size` of the `rank`-th smallest document, or None.

        None means there are fewer than `rank` documents -- which is the
        signal that the remaining window fits in one read, so it is a
        result rather than a failure.
        """
        if rank < 1 or rank > DEEP_PAGING_LIMIT:
            return None
        self._check_deadline(q)
        response = self._http.get(
            ENDPOINT,
            params={
                "q": q,
                "rows": 1,
                "page": rank,
                "output": "json",
                "fl[]": ["item_size"],
                "sort[]": "item_size asc",
            },
        )
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict) or body.get("error"):
            return None
        docs = (body.get("response") or {}).get("docs") or []
        if not docs or not isinstance(docs[0], dict):
            return None
        size = docs[0].get("item_size")
        return size if isinstance(size, int) else None

    # -- one attempt ----------------------------------------------------

    def _attempt(
        self,
        q: str,
        rows: int,
        page: int | None,
        *,
        with_notes: bool = True,
        fields: list[str] | None = None,
        sort: str | None = None,
    ) -> list[dict] | None:
        """`rows` documents, or None for "ask again, smaller".

        None rather than an exception because the caller's response to a
        failure is to retry, and a bare return value keeps the retry loop
        readable. A *permanent* problem -- a query the service rejects --
        raises instead, since halving `rows` will never fix it.
        """
        if fields is None:
            fields = list(FIELDS) + ([NOTES_FIELD] if with_notes else [])
        params = {"q": q, "fl[]": fields, "rows": rows, "output": "json"}
        if page is not None:
            params["page"] = page
        if sort is not None:
            params["sort[]"] = sort

        for _ in range(ATTEMPTS_PER_SIZE):
            self._check_deadline(q)
            response = self._http.get(ENDPOINT, params=params)
            if response.status_code != 200:
                continue
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError):
                # Rate limiting and maintenance both arrive as 200 + HTML.
                continue
            if not isinstance(body, dict):
                continue

            error = body.get("error")
            if isinstance(error, str) and error:
                # A rejected query, not a busy service. Halving `rows`
                # would only make the same complaint arrive more slowly.
                raise IndexUnavailable(
                    f"Archive.org refused the query {q!r}: {error.strip()}"
                )

            docs = (body.get("response") or {}).get("docs")
            if isinstance(docs, list):
                return [d for d in docs if isinstance(d, dict)]
        return None

    def _check_deadline(self, q: str) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise IndexUnavailable(
                f"gave up on {q!r} rather than overrun the host's call "
                f"budget. Archive.org rate-limits, and a plugin that keeps "
                f"asking past its budget is killed rather than answered -- "
                f"which costs the whole call instead of this one request."
            )

    def total(self, q: str) -> int | None:
        """`numFound` for `q`, or None if the service would not say.

        `rows=0` is the cheap form: Archive.org counts without returning
        any document. Used to tell an operator how much of a collection
        they are about to ask for.
        """
        self._check_deadline(q)
        response = self._http.get(
            ENDPOINT, params={"q": q, "rows": 0, "output": "json"}
        )
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict):
            return None
        found = (body.get("response") or {}).get("numFound")
        return found if isinstance(found, int) else None
