"""Archive.org search over the advancedsearch.php scraping API.

`collection` is requested up front so stream-only items can be flagged
without a second round trip: Archive.org marks non-downloadable items by
putting them in the `stream_only` collection, and that flag is what decides
whether a later phase offers import or streaming.

**Queries are scoped to the title.** See `build_query` -- this was a real
relevance bug, not a preference.
"""

from pydantic import ValidationError

from rom_hub_sdk import SearchProvider, SearchResult

ENDPOINT = "https://archive.org/advancedsearch.php"
DETAILS = "https://archive.org/details/"
DEFAULT_COLLECTIONS = ["softwarelibrary"]
FIELDS = ["identifier", "title", "collection", "item_size", "emulator"]


def _escape(term: str) -> str:
    r"""Make one term safe to sit inside a Lucene quoted phrase.

    Quoting is what neutralises Lucene's operators: `-`, `&`, `:` and the
    rest are literal text inside `"..."`, which is why real titles like
    `r-type` and `sonic & knuckles` need no special handling. Only the two
    characters that can *end* the phrase early have to be escaped -- the
    quote itself, and the backslash that would otherwise consume the escape.
    Backslash first, or escaping the quote would then double-escape.
    """
    return term.replace("\\", "\\\\").replace('"', '\\"')


def build_query(query: str | None, collections: list[str]) -> str:
    '''The advancedsearch `q`, with the user's terms confined to the title.

    This used to be `({query}) AND collection:({scope})`, which put a bare
    term into Archive.org's *default* field -- effectively the whole record,
    description and subject tags and uploader notes included -- and then let
    relevance ranking sort it out. It did not sort it out. Searching `sonic`
    returned `Die Hard (2004)(Die Chefrocker)`; `oregon trail` returned
    `Great Hierophant's .WOZ Archive` and `A2R Images`; `prince of persia`
    returned `Total Replay` and `Monmallineun Tarokbeom`. Those items match
    somewhere in their metadata, which is not a claim anybody searching a ROM
    library is making.

    So each term is required to appear **in the title**, and all of them must:

        title:("prince" AND "of" AND "persia") AND collection:(softwarelibrary)

    Two deliberate choices about how far to narrow:

    **Terms, not a phrase.** `title:("prince of persia")` also fixes the
    junk, but it demands adjacency and word order -- verified live, it
    returns *zero* results for `hedgehog sonic` and `persia prince`, while
    the AND-of-terms form answers both with the Sonic and Prince of Persia
    titles. A search that silently returns nothing because the words were
    typed in a different order is a worse bug than the one being fixed.

    **Title only, not title-or-identifier.** Checked live: adding
    `identifier:(...)` changed essentially nothing, because an Archive.org
    identifier already echoes the title. It is complexity that buys no
    recall.

    An empty query drops the clause entirely rather than emitting
    `title:()`, which is a syntax error, or `title:("")`, which matches
    nothing -- browsing a collection has to stay possible.
    '''
    scope = " OR ".join(collections)
    terms = [t for t in (query or "").split() if t]
    if not terms:
        return f"collection:({scope})"
    inner = " AND ".join(f'"{_escape(t)}"' for t in terms)
    return f"title:({inner}) AND collection:({scope})"


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        collections = self.ctx.config.get("collections") or DEFAULT_COLLECTIONS
        q = build_query(query, collections)

        response = self.ctx.http.get(
            ENDPOINT,
            params={
                "q": q,
                "fl[]": FIELDS,
                "rows": limit,
                "page": 1,
                "output": "json",
            },
        )
        docs = response.json().get("response", {}).get("docs", [])

        results: list[SearchResult] = []
        for doc in docs:
            identifier = doc.get("identifier")
            title = doc.get("title")
            if not identifier or not title:
                # Items without a title are unusable downstream; skip rather
                # than invent one.
                continue
            collection = doc.get("collection") or []
            if isinstance(collection, str):
                collection = [collection]
            try:
                results.append(
                    SearchResult(
                        source_id=identifier,
                        title=title if isinstance(title, str) else str(title),
                        platform=doc.get("emulator"),
                        size_bytes=doc.get("item_size"),
                        url=f"{DETAILS}{identifier}",
                        extra={
                            "stream_only": (
                                "true" if "stream_only" in collection else "false"
                            ),
                            "collections": ",".join(collection),
                        },
                    )
                )
            except (ValidationError, TypeError, ValueError):
                # item_size and emulator are whatever upstream put there, and
                # size_bytes is a ge=0 field. One malformed doc used to raise
                # out of search() and cost the plugin every other result in
                # the response -- skip it, like the untitled docs above.
                continue
        return results
