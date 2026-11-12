"""Extract member filenames from Internet Archive's ZIP View Contents HTML.

Archive.org serves this on the item's *storage host* at view_archive.php with
an archive=... query parameter. Links in that HTML point back to canonical
archive.org/download/<item>/<zip>/<member> URLs.

Only canonical links into the configured item and ZIP are trusted. An HTML
listing is never proof that the files can be downloaded; the host's downloader
must still authenticate and handle 401/403.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlsplit


class ZipListingError(ValueError):

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code



class _MemberLinks(HTMLParser):
    def __init__(self, item: str, archive: str, max_entries: int):
        super().__init__(convert_charrefs=True)
        self.prefix = f"/download/{item}/{archive}/"
        self.max_entries = max_entries
        self.members: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        # IA's view_archive.php links are normally protocol-relative.
        target = urlsplit(urljoin("https://archive.org/", href))
        if target.scheme != "https" or target.hostname not in ("archive.org", "www.archive.org"):
            return
        path = unquote(target.path)
        if not path.startswith(self.prefix):
            return
        member = path[len(self.prefix):]
        if (not member or member.endswith("/") or
                member in self._seen):
            return
        self._seen.add(member)
        self.members.append(member)
        if len(self.members) > self.max_entries:
            raise ZipListingError(
                f"Archive.org ZIP listing exceeds max_set_files={self.max_entries}"
            )


def zip_members(http, listing_url: str, item: str, archive: str,
                *, max_entries: int) -> list[dict]:
    try:
        response = http.get(listing_url)
    except RuntimeError as exc:
        raise ZipListingError(
            f"Archive.org ZIP listing broker request failed for {item}/{archive}: {exc}"
        ) from exc
    if response.status_code in (401, 403):
        raise ZipListingError(
            f"Archive.org ZIP contents listing is restricted (HTTP "
            f"{response.status_code}); this provider needs authenticated "
            f"host-side HTTP access for {item}/{archive}",
            status_code=response.status_code
        )
    if response.status_code != 200:
        raise ZipListingError(
            f"Archive.org ZIP contents listing returned HTTP {response.status_code}",
            status_code=response.status_code
        )
    parser = _MemberLinks(item, archive, max_entries)
    parser.feed(response.text)
    parser.close()
    if not parser.members:
        raise ZipListingError(
            f"Archive.org ZIP contents listing returned no downloadable member "
            f"links for {item}/{archive}; authentication or ZIP View Contents "
            "support may be required"
        )
    return [{"name": member} for member in parser.members]
