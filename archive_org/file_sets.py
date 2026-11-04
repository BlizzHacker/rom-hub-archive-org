"""Search individual downloadable files of explicitly configured Archive.org items.

Archive.org's advanced search indexes *items*, not files inside those items.
The metadata API, however, exposes a paginated files manifest. This module
makes each supported file a search result, and hands ROM Hub an exact file URL
instead of asking it to fetch a whole ROM set.

Set entries have the form "identifier[/folder]:romm-platform". The folder is
optional; it scopes results within an item (and can distinguish platforms).
No public collection is scanned or downloaded without explicit configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
import posixpath
import re
from urllib.parse import quote, unquote

from pydantic import ValidationError
from rom_hub_sdk import FetchFile, FetchPlan, SearchResult

from .zip_sets import ZipListingError, zip_members


METADATA = "https://archive.org/metadata/"
DOWNLOAD = "https://archive.org/download/"
DEFAULT_MAX_FILES = 8000
PAGE_SIZE = 400
DISC_SUFFIXES = (".chd", ".iso", ".cso", ".pbp", ".cdi", ".rvz", ".gcm", ".ciso", ".zso")
_DISC_PART = re.compile(r"\s*[\(\[](?:disc|disk)\s*\d+(?:\s*of\s*\d+)?[\)\]]", re.I)

# One payload file per game. Raw cue/bin/gdi sets need a separate, disc-aware
# acquisition plan and are deliberately *not* offered as incomplete games.
PAYLOAD_SUFFIXES = (
    ".zip", ".7z", ".rar", ".chd", ".iso", ".cso", ".pbp",
    ".cdi", ".rvz", ".gcm", ".ciso", ".zso",
    ".nes", ".sfc", ".smc", ".n64", ".z64", ".v64",
    ".gb", ".gbc", ".gba", ".md", ".gen", ".sms", ".gg",
)
_NON_WORD = re.compile(r"[^0-9a-z]+")
_BRACKETED = re.compile(r"[\(\[][^\)\]]*[\)\]]")
_ITEM_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_PLATFORM = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
# A file flagged private may be accessible to an authenticated account.
# Do not treat that metadata flag as an authorization decision; the host
# must still obtain the actual listing/download or return HTTP 403.
_BLOCKED = ("is_dark", "hidden")


class FileSetError(Exception):
    """Invalid configuration or a set that cannot be indexed safely."""


@dataclass(frozen=True)
class SetSource:
    identifier: str
    prefix: str
    platform: str
    archive: str | None = None

    @classmethod
    def parse(cls, raw: str) -> "SetSource":
        path, separator, platform = str(raw).strip().rpartition(":")
        identifier, _, prefix = path.partition("/")
        if (not separator or not _ITEM_ID.fullmatch(identifier)
                or not _PLATFORM.fullmatch(platform)
                or path.endswith("/") or prefix.startswith("/") or prefix.endswith("/")
                or (prefix and any(segment in ("", ".", "..") for segment in prefix.split("/")))
                or "\\" in prefix or any(ord(ch) < 32 for ch in prefix)):
            raise FileSetError(
                f"invalid file_sets entry {raw!r}; expected "
                "'Archive.org-identifier[/folder]:romm-platform'"
            )
        parts = prefix.split("/") if prefix else []
        zip_index = next((i for i, part in enumerate(parts)
                          if part.lower().endswith(".zip")), None)
        if zip_index is not None:
            return cls(identifier, "/".join(parts[zip_index + 1:]), platform,
                       "/".join(parts[:zip_index + 1]))
        return cls(identifier, prefix, platform)

    def contains(self, name: str) -> bool:
        return not self.prefix or name.startswith(self.prefix + "/")


def sources(config: dict) -> list[SetSource]:
    result = []
    seen = set()
    for raw in config.get("file_sets") or []:
        source = SetSource.parse(raw)
        key = (source.identifier, source.archive, source.prefix)
        if key in seen:
            raise FileSetError(f"duplicate file_sets entry for {key!r}")
        seen.add(key)
        result.append(source)
    return result


def _valid_name(name) -> bool:
    if not isinstance(name, str) or not name or len(name) > 2048:
        return False
    if name.startswith("/") or "\\" in name or any(ord(c) < 32 for c in name):
        return False
    return all(part not in ("", ".", "..") for part in name.split("/"))


def _payload(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    name = entry.get("name")
    if not _valid_name(name):
        return False
    if not name.lower().endswith(PAYLOAD_SUFFIXES):
        return False
    # Reject names the ROM Hub downloader cannot safely write (reserved
    # Windows devices, overlong names, forbidden characters) at search time.
    try:
        FetchFile(url="https://archive.org/", filename=posixpath.basename(name))
    except (ValidationError, ValueError):
        return False
    return not any(str(entry.get(k, "")).lower() in ("true", "1", "yes")
                   for k in _BLOCKED)


def _game_family(name: str) -> str:
    """Compare disc filenames without their disc-number suffix."""
    stem = posixpath.basename(name).rsplit(".", 1)[0]
    return _DISC_PART.sub("", stem).strip().casefold()


def _companions(name: str, entries: list[dict]) -> list[dict]:
    """Fetch an available playlist together with every same-family disc image."""
    primary = next(entry for entry in entries if entry["name"] == name)
    if not name.lower().endswith(DISC_SUFFIXES):
        return [primary]
    folder, family = posixpath.dirname(name), _game_family(name)
    relevant = [entry for entry in entries if isinstance(entry, dict)
                and _valid_name(entry.get("name"))
                and posixpath.dirname(entry["name"]) == folder
                and _game_family(entry["name"]) == family
                and (entry["name"].lower().endswith(DISC_SUFFIXES)
                     or entry["name"].lower().endswith(".m3u"))]
    playlist = [entry for entry in relevant
                if entry["name"].lower().endswith(".m3u")]
    if not playlist:
        return [primary]
    if len(playlist) > 1:
        raise FileSetError("ambiguous multi-disc playlists share a game name")
    if any(not _payload(entry) for entry in relevant
           if not entry["name"].lower().endswith(".m3u")):
        raise FileSetError("playlist includes an invalid disc filename")
    return sorted(relevant, key=lambda entry: entry["name"].lower())


def _size(entry) -> int | None:
    try:
        value = int(entry["size"])
        return value if value >= 0 else None
    except (ValueError, TypeError, KeyError):
        return None


def _key(name: str) -> str:
    basename = posixpath.basename(name)
    stem = basename.rsplit(".", 1)[0]
    return " ".join(_NON_WORD.sub(" ", _BRACKETED.sub(" ", stem.lower())).split())


def _score(name: str, query: str) -> int:
    wanted = " ".join(_NON_WORD.sub(" ", query.lower()).split())
    title = _key(name)
    if not wanted:
        return 1
    if title == wanted:
        return 3
    if title.startswith(wanted):
        return 2
    return 1 if all(word in title.split() for word in wanted.split()) else 0


def _get(http, url: str):
    response = http.get(url)
    if response.status_code != 200:
        raise FileSetError(f"Archive.org returned HTTP {response.status_code} for {url}")
    try:
        body = response.json()
    except ValueError as exc:
        raise FileSetError(f"Archive.org returned non-JSON metadata for {url}") from exc
    if not isinstance(body, dict) or "error" in body:
        raise FileSetError(f"Archive.org returned invalid metadata for {url}: "
                           f"{body.get('error') if isinstance(body, dict) else type(body).__name__}")
    return body


class FileSets:
    def __init__(self, http, config: dict):
        self.http = http
        self.config = config
        self.configured = sources(config)
        try:
            requested = int(config.get("max_set_files") or DEFAULT_MAX_FILES)
        except (TypeError, ValueError) as exc:
            raise FileSetError("max_set_files must be a positive integer") from exc
        if requested <= 0:
            raise FileSetError("max_set_files must be positive")
        self.max_files = requested
        self._cache: dict[str, list[dict]] = {}
        self._zip_cache: dict[tuple[str, str], list[dict]] = {}

    def _files(self, identifier: str) -> list[dict]:
        if identifier in self._cache:
            return self._cache[identifier]
        base = METADATA + quote(identifier, safe="")
        metadata = _get(self.http, base + "/metadata").get("result")
        if not isinstance(metadata, dict) or not metadata:
            raise FileSetError(f"Archive.org item {identifier!r} has no metadata")
        collections = metadata.get("collection") or []
        if isinstance(collections, str):
            collections = [collections]
        if "stream_only" in collections:
            raise FileSetError(f"Archive.org item {identifier!r} is stream-only")

        count = _get(self.http, base + "/files_count").get("result")
        if type(count) is not int or count < 0:
            raise FileSetError(f"Archive.org item {identifier!r} has no valid files_count")
        if count > self.max_files:
            raise FileSetError(
                f"Archive.org item {identifier!r} has {count} files, exceeding "
                f"max_set_files={self.max_files}; increase the limit if the "
                "set can be scanned within the plugin timeout"
            )
        files = []
        for start in range(0, count, PAGE_SIZE):
            page = _get(self.http, f"{base}/files?start={start}&count={min(PAGE_SIZE, count-start)}")
            entries = page.get("result")
            if not isinstance(entries, list) or len(entries) != min(PAGE_SIZE, count-start):
                raise FileSetError(
                    f"incomplete file manifest for {identifier!r} at offset {start}"
                )
            files.extend(entries)
        self._cache[identifier] = files
        return files

    def _zip_files(self, identifier: str, archive: str) -> list[dict]:
        key = (identifier, archive)
        if key in self._zip_cache:
            return self._zip_cache[key]
        matched = next((entry for entry in self._files(identifier)
                        if isinstance(entry, dict) and entry.get("name") == archive), None)
        if not _payload(matched) or not archive.lower().endswith(".zip"):
            raise FileSetError(f"ZIP {archive!r} is not listed as downloadable "
                               f"in Archive.org item {identifier!r}")
        base = METADATA + quote(identifier, safe="")
        server = _get(self.http, base + "/d1").get("result")
        directory = _get(self.http, base + "/dir").get("result")
        if (not isinstance(server, str)
                or not re.fullmatch(r"ia[0-9]+\.(?:us|ca)\.archive\.org", server)
                or not isinstance(directory, str)
                or not re.fullmatch(r"/[0-9]+/items/" + re.escape(identifier), directory)):
            raise FileSetError(f"Archive.org item {identifier!r} has invalid storage host or directory")
        url = ("https://" + server + "/view_archive.php?archive=" +
               quote(directory + "/" + archive, safe="/"))
        try:
            result = zip_members(self.http, url, identifier, archive,
                                 max_entries=self.max_files)
        except ZipListingError as exc:
            raise FileSetError(str(exc)) from exc
        self._zip_cache[key] = result
        return result

    def search(self, query: str, platform: str | None, limit: int) -> list[SearchResult]:
        selected = [source for source in self.configured
                    if not platform or source.platform == platform]
        if not selected or limit <= 0:
            return []
        by_item: dict[tuple[str, str | None], list[SetSource]] = {}
        for source in selected:
            by_item.setdefault((source.identifier, source.archive), []).append(source)
        ranked = []
        unavailable = []
        for (identifier, archive), choices in by_item.items():
            try:
                entries = (self._zip_files(identifier, archive) if archive
                           else self._files(identifier))
            except FileSetError as exc:
                unavailable.append(str(exc))
                continue
            for entry in entries:
                if not _payload(entry):
                    continue
                name = entry["name"]
                applicable = [source for source in choices if source.contains(name)]
                if not applicable:
                    continue
                source = max(applicable, key=lambda s: len(s.prefix))
                points = _score(name, query)
                if not points:
                    continue
                marker = ("zip:" + quote(identifier, safe="") + ":"
                          + quote(archive, safe="") + ":" + quote(name, safe="")
                          if archive else
                          "set:" + quote(identifier, safe="") + ":" + quote(name, safe=""))
                url = (DOWNLOAD + quote(identifier, safe="") + "/" +
                       (quote(archive, safe="/") + "/" if archive else "") +
                       quote(name, safe="" if archive else "/"))
                try:
                    result = SearchResult(
                        source_id=marker,
                        title=posixpath.basename(name).rsplit(".", 1)[0],
                        platform=source.platform,
                        size_bytes=_size(entry),
                        url=url,
                        extra={"set_item": identifier, "set_file": name,
                               "set_zip": archive or ""},
                    )
                except (ValidationError, TypeError, ValueError):
                    continue
                ranked.append((-points, len(name), name.casefold(), result))
        ranked.sort(key=lambda item: item[:3])
        if unavailable:
            # Failure of one restricted ZIP must not suppress matches from
            # another configured source. But never report "no matches" when
            # the actual issue was an inaccessible set or a truncated index.
            if not ranked:
                raise FileSetError("file-set search unavailable: " +
                                   "; ".join(unavailable))
            import sys
            for message in unavailable:
                print(f"archive-org file-set skipped: {message}", file=sys.stderr)
        return [row[3] for row in ranked[:limit]]

    def plan(self, result: SearchResult) -> FetchPlan:
        source_id = str(result.source_id or "")
        parts = source_id.split(":")
        if len(parts) == 3 and parts[0] == "set":
            _, encoded_item, encoded_name = parts
            encoded_archive = None
        elif len(parts) == 4 and parts[0] == "zip":
            _, encoded_item, encoded_archive, encoded_name = parts
        else:
            raise FileSetError("invalid file-set search result source id")
        identifier, name = unquote(encoded_item), unquote(encoded_name)
        archive = unquote(encoded_archive) if encoded_archive is not None else None
        if (not _ITEM_ID.fullmatch(identifier) or not _valid_name(name)
                or quote(identifier, safe="") != encoded_item
                or quote(name, safe="") != encoded_name
                or (archive is not None and
                    (not _valid_name(archive)
                     or quote(archive, safe="") != encoded_archive))):
            raise FileSetError("invalid or non-canonical file-set source id")
        choices = [source for source in self.configured
                   if source.identifier == identifier and source.archive == archive
                   and source.contains(name)]
        if not choices:
            raise FileSetError(f"{name!r} is not in a configured file set")
        source = max(choices, key=lambda s: len(s.prefix))
        entries = self._zip_files(identifier, archive) if archive else self._files(identifier)
        entry = next((entry for entry in entries
                      if isinstance(entry, dict) and entry.get("name") == name), None)
        if not _payload(entry):
            raise FileSetError(f"{name!r} is no longer downloadable in {identifier!r}")
        group = _companions(name, entries)
        prefix = (DOWNLOAD + quote(identifier, safe="") + "/" +
                  (quote(archive, safe="/") + "/" if archive else ""))
        files = [
            FetchFile(
                url=prefix + quote(e["name"], safe="" if archive else "/"),
                filename=posixpath.basename(e["name"]),
                size_bytes=_size(e),
            )
            for e in group
        ]
        return FetchPlan(
            files=files,
            platform=source.platform,
            collection=self.config.get("collection") or "Archive.org",
        )
