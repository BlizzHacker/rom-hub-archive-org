# Archive.org plugin for RomM Hub

Implements the RPP v1 `search` capability against Archive.org's
`advancedsearch.php` API.

## Install

    romm-hub plugin install https://github.com/<you>/romm-hub-archive-org --ref v0.1.0

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `collections` | `list[str]` | `["softwarelibrary"]` | Archive.org collections to scope searches to |

## Notes

Results carry `extra.stream_only`. Archive.org marks items that may only be
played in-browser by placing them in the `stream_only` collection; later
phases route those to streaming rather than import.
