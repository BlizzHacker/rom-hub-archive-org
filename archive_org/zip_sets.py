"""Read Archive.org's public ZIP 'View Contents' HTML without fetching the ZIP.

The browser-facing /download/<item>/<archive>.zip/ URL exposes links to
individual ZIP members. The parser intentionally trusts *only* canonical
download links below that exact item/ZIP prefix. The host's HTTP response
budget (4 MiB) still applies: an oversized directory listing fails rather
than silently returning a subset of games.
"""
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit


class ZipListingError(ValueError):
    pass


class _MemberLinks(HTMLParser):
    def __init__(self, directory_url: str, max_entries: int):
        super().__init__(convert_charrefs=True)
        self.directory_url = directory_url
        self.prefix = unquote(urlsplit(directory_url).path)
        self.max_entries = max_entries
        self.members: list[str] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        target = urlsplit(urljoin(self.directory_url, href))
        if target.scheme != "https" or target.hostname not in ("archive.org", "www.archive.org"):
            return
        path = unquote(target.path)
        if not path.startswith(self.prefix):
            return
        member = path[len(self.prefix):]
        if not member or member.endswith("/") or member in self._seen:
            return
        self._seen.add(member)
        self.members.append(member)
        if len(self.members) > self.max_entries:
            raise ZipListingError(
                f"ZIP listing exceeds max_set_files={self.max_entries}; "
                "narrow the configured item or raise the limit"
            )


def zip_members(http, directory_url: str, *, max_entries: int) -> list[dict]:
    response = http.get(directory_url)
    if response.status_code != 200:
        raise ZipListingError(
            f"Archive.org ZIP contents listing returned HTTP {response.status_code} "
            f"for {directory_url}; check that the archive supports View Contents"
        )
    parser = _MemberLinks(directory_url, max_entries)
    parser.feed(response.text)
    parser.close()
    if not parser.members:
        raise ZipListingError(
            f"Archive.org ZIP contents listing returned no downloadable member links "
            f"for {directory_url}; it may be locked or lack View Contents support"
        )
    return [{"name": member} for member in parser.members]
