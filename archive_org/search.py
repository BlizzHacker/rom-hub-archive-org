"""Archive.org search over the advancedsearch.php scraping API.

`collection` is requested up front so stream-only items can be flagged
without a second round trip: Archive.org marks non-downloadable items by
putting them in the `stream_only` collection, and that flag is what decides
whether a later phase offers import or streaming.
"""

from romm_hub_sdk import SearchProvider, SearchResult

ENDPOINT = "https://archive.org/advancedsearch.php"
DETAILS = "https://archive.org/details/"
DEFAULT_COLLECTIONS = ["softwarelibrary"]
FIELDS = ["identifier", "title", "collection", "item_size", "emulator"]


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        collections = self.ctx.config.get("collections") or DEFAULT_COLLECTIONS
        scope = " OR ".join(collections)
        q = f"({query}) AND collection:({scope})"

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
            results.append(
                SearchResult(
                    source_id=identifier,
                    title=title if isinstance(title, str) else str(title),
                    platform=doc.get("emulator"),
                    size_bytes=doc.get("item_size"),
                    url=f"{DETAILS}{identifier}",
                    extra={
                        "stream_only": "true" if "stream_only" in collection else "false",
                        "collections": ",".join(collection),
                    },
                )
            )
        return results
