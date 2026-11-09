"""Search configured items and ZIP listings using explicit or inferred platforms."""
from __future__ import annotations

from dataclasses import dataclass
import sys
import time
import posixpath
import re
from urllib.parse import quote, unquote

from pydantic import ValidationError
from rom_hub_sdk import FetchFile, FetchPlan, SearchResult

from .zip_sets import ZipListingError, zip_members
from .set_platforms import infer_platform, within_depth


METADATA = "https://archive.org/metadata/"
DOWNLOAD = "https://archive.org/download/"
DEFAULT_MAX_FILES = 8000
PAGE_SIZE = 400
MAX_PLAYLIST_BYTES = 128 * 1024
MAX_PLAN_FILES = 128
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
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code



@dataclass(frozen=True)
class SetSource:
    identifier: str
    prefix: str
    platform: str | None
    archive: str | None = None

    @classmethod
    def parse(cls, raw: str) -> "SetSource":
        path, separator, platform = str(raw).strip().rpartition(":")
        if not separator:
            path, platform = str(raw).strip(), None
        identifier, _, prefix = path.partition("/")
        if (not _ITEM_ID.fullmatch(identifier)
                or (platform is not None and not _PLATFORM.fullmatch(platform))
                or path.endswith("/") or prefix.startswith("/") or prefix.endswith("/")
                or (prefix and any(segment in ("", ".", "..") for segment in prefix.split("/")))
                or "\\" in prefix or any(ord(ch) < 32 for ch in prefix)):
            raise FileSetError(
                f"invalid file_sets entry {raw!r}; expected "
                "'Archive.org-identifier[/folder-or-zip][:romm-platform]'"
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


@dataclass
class SearchOutcome:
    results: list[SearchResult]
    failures: list[str]


def sources(config: dict) -> list[SetSource]:
    result = []
    seen = set()
    for raw in config.get("file_sets") or []:
        source = SetSource.parse(raw)
        key = (source.identifier, source.archive, source.prefix, source.platform is None)
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
    if title.startswith(wanted + " "):
        return 2
    return 1 if all(word in title.split() for word in wanted.split()) else 0


def _get(http, url: str):
    try:
        response = http.get(url)
    except RuntimeError as exc:
        # The SDK forwards broker transport and size errors as RuntimeError.
        raise FileSetError(f"Archive.org broker request failed for {url}: {exc}") from exc
    if response.status_code in (401, 403):
        raise FileSetError(f"restricted access (HTTP {response.status_code}) for {url}; "
                           "an authorized host-side Archive.org session may be required",
                           status_code=response.status_code)
    if response.status_code != 200:
        raise FileSetError(f"Archive.org returned HTTP {response.status_code} for {url}",
                           status_code=response.status_code)
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
        self._deadline = None
        self._cache: dict[str, list[dict]] = {}
        self._zip_cache: dict[tuple[str, str], list[dict]] = {}
        self._zip_views: dict[tuple[str, str], str] = {}
        self._member_archives = {(source.identifier, source.archive)
                                 for source in self.configured if source.archive}

    def indexes_zip_members(self, identifier: str, name: str) -> bool:
        """Whether a parent ZIP is indexed as individual ROMs elsewhere."""
        if not isinstance(name, str):
            return False
        return ((identifier, name) in self._member_archives or any(
            source.identifier == identifier and source.archive is None
            and source.platform is None and source.contains(name)
            and name.lower().endswith(".zip") and within_depth(None, name) for source in self.configured))

    def _check_budget(self):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise FileSetError("automatic discovery time budget exhausted; use the census catalogue")

    def _files(self, identifier: str) -> list[dict]:
        if identifier in self._cache:
            return self._cache[identifier]
        self._check_budget()
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
            self._check_budget()
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
        self._check_budget()
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
            raise FileSetError(str(exc), status_code=exc.status_code) from exc
        self._zip_cache[key] = result
        self._zip_views[key] = url
        return result

    def resources(self) -> dict[tuple[str, str | None], list[SetSource]]:
        grouped: dict[tuple[str, str | None], list[SetSource]] = {}
        for source in self.configured:
            grouped.setdefault((source.identifier, source.archive), []).append(source)
        return grouped

    def resolve(self, identifier: str, archive: str | None,
                name: str) -> SetSource | None:
        """Resolve from all mappings before filtering by platform."""
        explicit = [s for s in self.configured
                    if s.identifier == identifier and s.archive == archive
                    and s.platform is not None and s.contains(name)]
        if explicit:
            return max(explicit, key=lambda s: len(s.prefix))
        automatic = [s for s in self.configured if s.identifier == identifier
                     and s.platform is None and
                     ((s.archive == archive and s.contains(name)) or
                      (s.archive is None and archive is not None and s.contains(archive)))]
        if not automatic or not within_depth(archive, name):
            return None
        platform = infer_platform(archive, name)
        return SetSource(identifier, "", platform, archive) if platform else None

    def automatic_archives(self, identifier: str) -> list[str]:
        scopes = [s for s in self.configured if s.identifier == identifier
                  and s.platform is None and s.archive is None]
        return sorted({e["name"] for e in self._files(identifier)
                       if _payload(e) and e["name"].lower().endswith(".zip")
                       and within_depth(None, e["name"])
                       and any(s.contains(e["name"]) for s in scopes)
                       and (identifier, e["name"]) not in self._member_archives})

    def skip_reason(self, identifier: str, archive: str | None, entry: dict) -> str:
        name = entry.get("name", "") if isinstance(entry, dict) else ""
        if _payload(entry) and any(s.identifier == identifier and s.platform is None
                                   for s in self.configured):
            if not within_depth(archive, name):
                return "file exceeds automatic discovery folder depth 5"
            return "file has no unambiguous platform label"
        return "unsupported or out-of-scope file"

    @staticmethod
    def source_id(identifier: str, archive: str | None, name: str) -> str:
        if archive is not None:
            return ("zip:" + quote(identifier, safe="") + ":" +
                    quote(archive, safe="") + ":" + quote(name, safe=""))
        return "set:" + quote(identifier, safe="") + ":" + quote(name, safe="")

    @staticmethod
    def file_url(identifier: str, archive: str | None, name: str) -> str:
        return (DOWNLOAD + quote(identifier, safe="") + "/" +
                (quote(archive, safe="/") + "/" if archive else "") +
                quote(name, safe="" if archive else "/"))

    def result(self, identifier: str, archive: str | None, entry: dict
               ) -> SearchResult | None:
        if not _payload(entry):
            return None
        name = entry["name"]
        if archive is None and self.indexes_zip_members(identifier, name):
            return None
        source = self.resolve(identifier, archive, name)
        if source is None:
            return None
        try:
            return SearchResult(
                source_id=self.source_id(identifier, archive, name),
                title=posixpath.basename(name).rsplit(".", 1)[0],
                platform=source.platform,
                size_bytes=_size(entry),
                url=self.file_url(identifier, archive, name),
                extra={"set_item": identifier, "set_file": name,
                       "set_zip": archive or ""},
            )
        except (ValidationError, TypeError, ValueError):
            return None

    def search_outcome(self, query: str, platform: str | None,
                       limit: int) -> SearchOutcome:
        if limit <= 0:
            return SearchOutcome([], [])
        self._deadline = time.monotonic() + 20
        ranked = []
        unavailable = []
        resources = list(self.resources().items())
        for identifier in sorted({s.identifier for s in self.configured
                                  if s.platform is None and s.archive is None}):
            try:
                resources.extend(((identifier, archive), [SetSource(identifier, "", None, archive)])
                                 for archive in self.automatic_archives(identifier))
            except FileSetError as exc:
                unavailable.append(f"{identifier}: {exc}")
        forbidden_items = set()
        for (identifier, archive), choices in resources:
            if archive and identifier in forbidden_items:
                continue
            if platform and not any(s.platform in (None, platform) for s in choices):
                continue
            if (archive and platform and all(s.platform is None for s in choices)
                    and infer_platform(archive, "payload") not in (None, platform)):
                continue
            try:
                entries = (self._zip_files(identifier, archive) if archive
                           else self._files(identifier))
            except FileSetError as exc:
                unavailable.append(f"{identifier}/{archive or '(direct files)'}: {exc}")
                if archive and exc.status_code == 403:
                    forbidden_items.add(identifier)
                continue
            for entry in entries:
                result = self.result(identifier, archive, entry)
                if result is None:
                    if any(s.platform is None for s in choices) and _payload(entry) and not (archive is None and
                            self.indexes_zip_members(identifier, entry["name"])):
                        print(f"archive-org skipped {identifier}/{archive or ''}/{entry['name']}: "
                              f"{self.skip_reason(identifier, archive, entry)}", file=sys.stderr)
                    continue
                if platform and result.platform != platform:
                    continue
                name = entry["name"]
                points = _score(name, query)
                if points:
                    ranked.append((-points, len(name), name.casefold(), result))
        ranked.sort(key=lambda item: item[:3])
        return SearchOutcome([row[3] for row in ranked[:limit]], unavailable)

    def search(self, query: str, platform: str | None,
               limit: int) -> list[SearchResult]:
        outcome = self.search_outcome(query, platform, limit)
        if outcome.failures and not outcome.results:
            raise FileSetError("file-set search unavailable: " +
                               "; ".join(outcome.failures))
        for message in outcome.failures:
            print(f"archive-org file-set skipped: {message}", file=sys.stderr)
        return outcome.results

    def _companions(self, identifier: str, archive: str | None, name: str,
                    entries: list[dict], platform: str) -> list[dict]:
        primary = next(e for e in entries if isinstance(e, dict)
                       and e.get("name") == name)
        if not name.lower().endswith(DISC_SUFFIXES):
            return [primary]
        folder, family = posixpath.dirname(name), _game_family(name)
        playlists = [e for e in entries if isinstance(e, dict)
                     and _valid_name(e.get("name"))
                     and posixpath.dirname(e["name"]) == folder
                     and e["name"].lower().endswith(".m3u")
                     and _game_family(e["name"]) == family]
        if not playlists:
            return [primary]
        if len(playlists) != 1:
            raise FileSetError(f"ambiguous playlists for {name!r}")
        playlist = playlists[0]
        playlist_name = playlist["name"]
        try:
            FetchFile(url=self.file_url(identifier, archive, playlist_name),
                      filename=posixpath.basename(playlist_name))
        except (ValidationError, ValueError) as exc:
            raise FileSetError(f"unsafe playlist {playlist_name!r}") from exc
        if (any(str(playlist.get(k, "")).lower() in ("true", "1", "yes")
                for k in _BLOCKED) or
                (_size(playlist) is not None and _size(playlist) > MAX_PLAYLIST_BYTES)):
            raise FileSetError(f"playlist {playlist_name!r} is blocked or too large")
        url = (self._zip_views[(identifier, archive)] + "&file=" + quote(playlist_name, safe="")
               if archive else self.file_url(identifier, archive, playlist_name))
        try:
            response = self.http.get(url)
        except RuntimeError as exc:
            raise FileSetError(f"Archive.org broker playlist request failed for {url}: {exc}") from exc
        if response.status_code != 200:
            raise FileSetError(f"playlist {playlist_name!r} returned HTTP {response.status_code}")
        content = response.text
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_PLAYLIST_BYTES:
            raise FileSetError(f"playlist {playlist_name!r} exceeds {MAX_PLAYLIST_BYTES} bytes")
        content = content.lstrip("\ufeff")
        by_name = {e["name"]: e for e in entries
                   if isinstance(e, dict) and isinstance(e.get("name"), str)}
        selected: list[dict] = [playlist]
        seen: set[str] = set()
        for line in content.splitlines():
            ref = line.strip()
            if not ref or ref.startswith("#"):
                continue
            original = ref
            if ref.startswith("./"):
                ref = ref[2:]
            if (not ref or ref in (".", "..") or "/" in ref or "\\" in ref
                    or ":" in ref or ref.startswith("~")
                    or not ref.lower().endswith(DISC_SUFFIXES)):
                raise FileSetError(f"unsupported playlist reference {original!r} in {playlist_name!r}")
            full = posixpath.join(folder, ref) if folder else ref
            entry = by_name.get(full)
            owner = self.resolve(identifier, archive, full)
            if not _payload(entry) or owner is None or owner.platform != platform:
                raise FileSetError(f"missing, invalid or out-of-scope playlist disc {original!r}")
            if full not in seen:
                selected.append(entry)
                seen.add(full)
            if len(selected) > MAX_PLAN_FILES:
                raise FileSetError(f"playlist {playlist_name!r} has too many files")
        if name not in seen:
            raise FileSetError(f"selected disc {name!r} is not referenced by {playlist_name!r}")
        return selected

    def plan(self, result: SearchResult) -> FetchPlan:
        self._deadline = None
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
        source = self.resolve(identifier, archive, name)
        if source is None:
            raise FileSetError(f"{name!r} is not in a configured file set")
        if result.platform and source.platform != result.platform:
            raise FileSetError(f"platform changed for {name!r}; search again before importing")
        entries = self._zip_files(identifier, archive) if archive else self._files(identifier)
        entry = next((entry for entry in entries
                      if isinstance(entry, dict) and entry.get("name") == name), None)
        if not _payload(entry):
            raise FileSetError(f"{name!r} is no longer downloadable in {identifier!r}")
        group = self._companions(identifier, archive, name, entries, source.platform)
        files = [
            FetchFile(
                url=self.file_url(identifier, archive, e["name"]),
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
