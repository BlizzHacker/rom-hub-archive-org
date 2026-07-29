# Archive.org plugin for ROM Hub

Implements the RPP v1 `search` and `importer` capabilities:

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `advancedsearch.php` | items in the configured collections, matched **on title** |
| `importer` | `metadata/<identifier>` | picks the payload file and the RomM platform |

## How search matches

Your terms are matched against the item **title**, not the whole record:

    title:("prince" AND "of" AND "persia") AND collection:(softwarelibrary)

It used to be `(prince of persia) AND collection:(...)`, which put the terms
in Archive.org's *default* field — description, subject tags, uploader notes,
everything — and left relevance ranking to sort it out. It did not sort it
out. `sonic` returned **Die Hard (2004)(Die Chefrocker)**, `oregon trail`
returned **Great Hierophant's .WOZ Archive** and **A2R Images**, and
`prince of persia` returned **Total Replay**. Those items really do match
somewhere in their metadata; that is just not the claim someone searching a
ROM library is making.

Two things it deliberately does **not** do:

- **It does not require a phrase.** `title:("prince of persia")` also removes
  the junk, but it demands the words in that order and next to each other —
  checked live, it returns *nothing* for `persia prince` or `hedgehog sonic`.
  Every term must appear in the title; where they appear is not this plugin's
  business.
- **It does not also search the identifier.** Checked live, adding
  `identifier:(...)` changed essentially nothing, because an Archive.org
  identifier already echoes the title.

An empty query drops the title clause entirely, so browsing a collection
still works. Terms are quoted, which makes Lucene's operators literal — real
titles like `r-type` and `sonic & knuckles` need no special handling and were
verified live.

## Install

    rom-hub plugin install https://github.com/<you>/rom-hub-archive-org --ref v0.1.0
    rom-hub import archive-org rubik_202308

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `collections` | `list[str]` | `["softwarelibrary"]` | Archive.org collections to scope searches to |
| `collection` | `str` | `"Archive.org"` | RomM collection imported ROMs are grouped into |

## How the importer routes

One call to `https://archive.org/metadata/<identifier>` answers everything:

- **`metadata.collection` contains `stream_only` → refused.** Archive.org
  itself marks which items it will only stream in-browser. Those items still
  list an ordinary `.zip` in `files[]`, so the flag is the *only* thing
  distinguishing them — which is exactly why routing reads Archive.org's signal
  instead of an allowlist someone has to maintain. The refusal happens before
  any file is chosen, so it never hands you a URL to try by hand.
- **`metadata.emulator_ext` selects the payload** out of the item's file list,
  matched case-insensitively (Archive.org writes both `zip` and `ZIP`). If
  several files match, the largest wins; a file with no `size` — every item has
  one — sorts below every sized file so a metadata stub cannot outrank the ROM.
- **`metadata.emulator` maps to a RomM platform slug** via
  `archive_org/platforms.py`. That table is an exact-match lookup with no
  fallback: an emulator that is not in it raises **"needs mapping"** and names
  itself. Guessing would file a ROM under the wrong system, and nothing about
  the library afterwards would say anything went wrong.

Adding a mapping is a one-line change to `archive_org/platforms.py`. Note that
Archive.org's emulator ids are *not* a hierarchy — `vice-pet` is a PET, not a
C64, and `pce-atarist-color` is an Atari ST, not a Mac — so add exact keys
rather than reaching for a prefix rule.

## Notes

Search results carry `extra.stream_only`, so the UI can route an item to
streaming rather than offering an import that would be refused.

The plugin opens no sockets: `ctx.http` is an RPC back to the Hub, which
checks every URL against this plugin's declared allowlist
(`archive.org`, `*.archive.org`) before fetching anything — including the
download URLs returned in a `FetchPlan`, which the **Hub**, not the plugin,
fetches.
