# Archive.org plugin for ROM Hub

Implements five RPP v1 capabilities:

| Capability | Endpoint | Does |
|---|---|---|
| `search` | `advancedsearch.php` | items in the configured collections, matched **on title** |
| `importer` | `metadata/<identifier>` | picks the payload file and the RomM platform |
| `metadata` | `metadata/<identifier>` | title, cover, and **how the game is controlled** |
| `stream` | `metadata/<identifier>` | resolves an item to the page that plays it |
| `census` | `advancedsearch.php` | all of `softwarelibrary`, in windows whose totals add up |

## The census, and why the units are byte counts

`rom-hub census build archive-org` walks the whole of `softwarelibrary` —
**250,509 items** on 2026-08-08 — into a catalogue that can state its own
denominator. The hard part is not the walk. It is choosing units whose
declared totals *sum* to the collection's, because units that overlap
produce a number that is too big and reads like success.

**Sub-collections overlap.** In one 500-item sample 440 items were in
`softwarelibrary_apple`, 433 in `softwarelibrary_apple_contribs`, and all
500 in `softwarelibrary` itself. Adding up `_c64` (98,843), `_apple`
(42,273), `_msdos` (23,200) and the rest reports more entries than the
collection has.

**`item_size` ranges do not.** The 27 windows tile `[0, ∞)` — the first
starts at zero, each ends one byte below the next, and the last ends on
Lucene's open bound so no item is too large to be inside the partition.
Every item carries `item_size` (`NOT item_size:[* TO *]` matches zero),
which `scope` re-checks on every build and **refuses** to partition
without: an item in no window would be missing without ever showing up as
a shortfall.

A unit's id is its clause, so the report is checkable by hand:

```
$ rom-hub census report archive-org --units
archive-org: complete -- 250,509 of 250,509 declared entries across 27 units
  250,309 catalogued rows -> 242,928 distinct dumps in 189,878 games
  skipped, by reason:
         200  a sub-collection listing, not a software item (mediatype:collection)

UNIT                                     KIND        DECL    KEPT STATE
item_size:[0 TO 156073]                  roms       9,599   9,439 done
item_size:[156074 TO 209197]             roms       9,590   9,585 done
...
item_size:[5698242445 TO *]              roms         946     946 done
```

and `collection:(softwarelibrary) AND item_size:[0 TO 156073]` at `rows=0`
is where that 9,599 came from. The twenty-seven declared totals add up to
250,509, which is what the collection's own `numFound` answered — measured
2026-08-08, 651 seconds, 81 requests.

**What is kept.** Everything except sub-collection *listings*
(`mediatype:collection`), which are named and counted as a skip. Items
with no `emulator`, items whose emulator is not in the platform table, and
items under a mediatype that is not `software` are all catalogued —
unmapped ones with **no platform** rather than a plausible neighbour.
`stream_only` items are marked, not dropped.

**What it cannot do.** `advancedsearch.php` publishes no digest at item
level, so `rom_hub.grouping` merges these rows on the parsed name alone.
The No-Intro census carries sha1/md5/crc32 and collapses duplicate uploads
on proof; this one cannot.

`census_collection` (default `softwarelibrary`) chooses the collection.
One, not the search scope's list: two collections would need two
partitions and two denominators, and summing them would double-count the
212 items in both.

## The Console Living Room

`https://archive.org/details/consolelivingroom` is **24,746 items**, and until
v0.3.0 this plugin could not see one of them: its default scope was
`softwarelibrary`, which is a different collection. Measured —
`softwarelibrary` holds 250,382 items, the Console Living Room 24,746, and
**212** items are in both. The default is now both collections.

What is in there, from a census of every `emulator` value (132 distinct):

| machine | items | downloadable | plays in RomM? |
|---|---:|---:|---|
| Mega Drive / Genesis (`genesis`, `megadriv`, `megadrij`) | 12,445 | **11,893** | yes |
| Atari 2600 (`a2600`, `a2600p`) | 3,026 | **2,514** | yes |
| PlayStation (`psx`) | 2,688 | 97 | yes |
| Atari 7800 (`a7800`) | 720 | 9 | yes |
| Master System (`sms`, `smsj`, `sms-phaser`) | 574 | 21 | yes |
| Game Gear (`gamegear`) | 445 | 19 | yes |
| Game Boy Advance (`gba`) | 382 | **382** | yes |
| Game Boy Color (`gbcolor`) | 363 | **363** | yes |
| NES (`nes`, `nesp`, `nespal`) | 359 | **359** | yes |
| TurboGrafx-16 (`tg16`) | 310 | **310** | yes |
| ColecoVision (`coleco`) | 265 | 32 | yes |
| Neo Geo Pocket Color (`ngpc`) | 228 | **228** | yes |
| SNES (`snes`, `snesp`) | 221 | **221** | yes |
| Game Boy (`gameboy`) | 218 | **218** | yes |
| Atari 5200 (`a5200`) | 218 | 6 | yes |
| WonderSwan (`wswan`, `wscolor`) | 322 | 60 | yes |
| Vectrex (`vectrex`) | 151 | 151 | **no core** |
| Intellivision (`intv`, `intv2`, `intvsrs`) | 185 | 3 | netplay only |
| SG-1000 (`sg1000`) | 116 | 46 | **no core** |
| Odyssey 2 (`odyssey2`) | 132 | 10 | **no core** |
| Channel F (`channelf`) | 48 | 3 | **no core** |
| Arcadia 2001 (`arcadia`) | 58 | 0 | **no core** |

24,746 items in total, **17,930 of them downloadable**. The 6,816 that are not
are Archive.org's `stream_only` items; the `stream` capability is what makes
those reachable, and the importer refuses them by name.

The **Genesis, NES and SNES rows are the prize**: ~12,500 downloadable ROMs on
machines RomM's own EmulatorJS player runs, which means they work on every
RomM client including the Xbox app.

Point it somewhere else if you want to:

    rom-hub plugin config archive-org --set collections=consolelivingroom
    rom-hub plugin config archive-org --set downloadable_only=true
    rom-hub search sonic --platform genesis --limit 500

## Reaching a whole collection

`advancedsearch.php` refuses to page past 10,000 results:

    rows=100 page=101  ->  {"error": "[DEEP_PAGING] Requested results would
                            exceed the deep paging limit for this service,
                            10000 results; ..."}

and the error itself names the way out — *"You may request any number of
results at one time if you do NOT specify any page."* Verified live: `rows=11000`
with no `page` returned all **10,045** downloadable Mega Drive items in about
two seconds. So a small read pages, and a bulk read does not.

**The scrape API is not used, and that is a finding rather than a preference.**
`/services/search/v1/scrape` is what Archive.org's own error message points at.
It does page the whole collection with a cursor — 24,746 items in 137 seconds
over 25 requests — but:

- **it silently ignores a field filter.** Asked for
  `collection:(consolelivingroom) AND emulator:("genesis")` it answers
  `total=24746` and the first page comes back 36 genesis, 20 nes, 12 a2600. All
  33 per-emulator queries returned identical results. A filter that is accepted
  and ignored is worse than one that is refused.
- **its result set is not reliably the one you asked for.** Consecutive requests
  on one connection returned each other's totals — `collection:(nasa)` and
  `collection:(consolelivingroom)` both answered `208822` in one run and both
  answered `24746` in another, depending only on which query went first.

Both reproduced more than once. See `archive_org/index.py`.

### The limits that actually bind are the Hub's, not Archive.org's

Three of them, all found by running into them, and all correct rules that the
plugin has to fit inside rather than argue with:

| limit | value | what it does | how this fits |
|---|---|---|---|
| `ctx.http` response | 4 MiB | `ResponseTooLarge` | drop `notes` when the ask is big; partition the query |
| plugin call | 30 s wall clock | the process is killed | one rank lookup + one read per window, not a bisection |
| RPP reply frame | 8 MiB chars | *"the stream is now desynchronised"* | refuse an ask over 12,000 results |

The response cap is why a large read partitions rather than pages. `page`
cannot reach past 10,000 and the page-less form has no offset to chunk with, so
the *query* is split: ask where the N-th smallest item sits
(`sort[]=item_size asc, rows=1, page=N`, 0.65s), take everything up to that
size, start the next window one byte above. `item_size` is on every one of the
24,746 items — `NOT item_size:[* TO *]` matches zero — so the windows are
disjoint and lose nothing.

The wall clock is why it is a rank lookup and not a bisection on the size
range: sizes are skewed small, so bisecting `[0, 2**42]` puts the whole corpus
in the lower half fifteen times running and spends the entire budget before
reading a document.

The reply frame is why an ask over **12,000** results is refused rather than
truncated. A result serialises to 467–602 characters, measured; 11,893 came
back intact and 24,746 did not. Truncating would answer "how big is this
collection" with a number the plugin made up, so the refusal names the knobs
instead — filter by platform, set `downloadable_only`, or scope `collections`.

Measured end to end through the CLI, against the live service:

    rom-hub search "" --platform genesis --limit 12000
    1 of 1 sources responded, 11893 results          # 7.4 s

## Controls

Archive.org tells a reader which key is which console button, because the
reader is holding a keyboard. That travels into the library, and **what is
carried was measured before it was designed**:

| field | items | shape | carried |
|---|---:|---|---|
| `controller` | 405 | structured: `joystick`, `paddle`, `keypad`, `driving` | verbatim |
| `emulator_instructions` | 1,818 | prose, 8 distinct texts, one per machine | always |
| `notes` | 14,317 | prose, *usually* control boilerplate | when it reads as control text |

**16,127 of 24,746 items carry at least one.** Coverage is very uneven and the
plugin does not pretend otherwise: 10,382 of 10,557 Genesis items have `notes`,
and **1** of 219 SNES items does. A rom that gets no control blob is the normal
case for several platforms.

**And for NES and SNES it is every case.** Eleven downloadable NES or SNES
items in the whole collection have a `notes` or `emulator_instructions` field,
and not one is a control mapping — nine are lists of related games
(*"(1985) Battle City [Nintendo Family Computer] · (1989) Tank 1989
[Dendy] · …"*), one is a sound-test menu path, one is an alternate title. All
eleven are checked in as a fixture and all eleven are rejected. If you import
the NES half of the Console Living Room you will get no control information,
because there is none to get.

`notes` is gated because it is also where an uploader writes *"Unofficial boxart
by me"*. The gate requires the text to name both something you press and
something on a controller; across the 72 distinct `notes` texts in a 4,000-item
sample it admits 10 covering 3,937 items and rejects 62 covering 63.
`emulator_instructions` is **not** gated, and that is evidence-based too: the
same gate rejects two of its eight texts (Socrates, Arcadia) which plainly are
instructions written without the word "button".

**The prose is never parsed into a key-to-button table.** It looks parseable —
*"There are three buttons, A, B and C, which are CONTROL, ALT/OPTION and
SPACE"* — and a parser for the ten common boilerplates could be written. It
would then be a table this plugin invented, sitting in a field that says it came
from Archive.org, and the first reworded sentence would turn it into a *wrong*
table rather than a missing one. Archive.org publishes sentences; this carries
sentences.

It lands in `raw_metadata["raw_manual_metadata"]["archive_org_controls"]` —
the only one of RPP's eight raw blobs that is not the name of a metadata
provider, so putting Archive.org's text there claims nothing untrue about where
it came from. It is written **only** when there is something real to write:
`MetadataPatch` reads an absent field as "leave the library alone", and an empty
one as "replace what is there with nothing".

    rom-hub enrich archive-org 4211 --source-id whac-a-critter-usa-unl

### RomM 4.9.2 accepts the field and stores nothing

Measured, and it is not this plugin's bug — it is the whole `raw_metadata`
channel of RPP v1 against RomM. `PUT /api/roms/{id}` **declares** all eight
raw fields in its own OpenAPI schema:

    raw_igdb_metadata  raw_moby_metadata     raw_ss_metadata
    raw_launchbox_metadata  raw_hasheous_metadata  raw_flashpoint_metadata
    raw_hltb_metadata  raw_manual_metadata

Every one of them is accepted with `200` and none of them comes back. On
RomM 4.9.2, sending `{"probe": "..."}` to each and reading the rom again:

| sent | read back |
|---|---|
| `raw_manual_metadata` | `manual_metadata = {}` |
| `raw_moby_metadata` | `moby_metadata = {}` |
| `raw_flashpoint_metadata` | `flashpoint_metadata = {}` |
| `raw_hltb_metadata` | `hltb_metadata = {}` |

Pairing the blob with its id does not help — `moby_id=12345` **persists** and
`raw_moby_metadata` alongside it still does not — and no rom in a 200-rom
library has a non-empty raw blob of any kind.

So the control information is carried correctly through RPP and RomM 4.9.2
drops it on the floor. Nothing here works around that: writing the text into
`summary`, which does persist, would put a keyboard mapping in the field that
holds the game's description, and a library that lies about what a field means
is worse than one that is missing data. The plugin keeps writing the field it
should write. A backend that stores it will store it.

**Today, `stream` is the path where the mapping actually arrives.** It carries
the same text on the target, and nothing in between discards it:

    $ rom-hub stream archive-org Alex_Kidd_in_the_Enchanted_Castle_E_REV02_
    url          https://archive.org/details/Alex_Kidd_in_the_Enchanted_Castle_E_REV02_
    title        Alex Kidd in the Enchanted Castle (E) (REV02) [!]
    controls     Sega Genesis/Megadrive Controls: Press the 1 key to start games.
                 Use Arrow Keys to move up, left, right and down. There are three
                 buttons, A, B and C, which are CONTROL, ALT/OPTION and SPACE.
    emulator     genesis
    platform     genesis
    stream_only  false

## Streaming the other half

6,816 items will not download, and they are not junk — Archive.org plays them
in the browser with Emularity, and an item's `/details/` page **is** the
emulator. The importer refuses those by name and points at the capability that
can still play them:

    rom-hub stream archive-org <identifier> --open

The target carries the platform, the `controller` value and the instruction
text, because a browser-bound player is exactly where a keyboard mapping
matters. It never invents a media URL: Archive.org serves the item, not a
stream.

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

    rom-hub plugin install https://github.com/<you>/rom-hub-archive-org --ref v0.3.0
    rom-hub search sonic --platform genesis
    rom-hub import archive-org Sonic_The_Hedgehog_W_REV01_h1C

## Config

| Key | Type | Default | Meaning |
|---|---|---|---|
| `collections` | `list[str]` | `["softwarelibrary", "consolelivingroom"]` | Archive.org collections to scope searches to |
| `collection` | `str` | `"Archive.org"` | RomM collection imported ROMs are grouped into |
| `downloadable_only` | `bool` | `false` | drop `stream_only` items from search — for bulk importing, where seeing items the importer will refuse is noise |
| `max_rows` | `int` | `50000` | ceiling on a single bulk request, so a typo cannot ask Archive.org for a million rows |

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

### What is still deliberately unmapped

The census found 132 distinct `emulator` values in the Console Living Room. 82
keys are mapped. The rest raise "needs mapping" on purpose:

- **~60 MAME romset names** — `galaxian`, `mspacman`, `outrun`, `tmnt2`,
  `bublbobl` — one or two items each, ~120 in total. They are almost certainly
  `arcade`, and "almost certainly" is not the standard this table is held to:
  the key is a *game* id rather than a *machine* id, so the family cannot be
  closed by inspection.
- **Six machines RomM has no slug for**: `bally` (Bally Astrocade, 20),
  `apfm1000` (15), `gamepock` (11), `socrates` (8), `sv8000` (7), `fgtlayer` (4).
- **Composite values** — `gameboy,gb` and
  `dosbox,dosbox_drive_d,emularity_win31/win31.zip`, one item each. Splitting on
  the comma is exactly the prefix rule this table refuses.
- **`genisis`** — one item, and a misspelling. Mapping a typo teaches the table
  to accept typos.
- **`sb486`** (30 items) — the one row that was written from reasoning and then
  deleted after checking. The name reads as a 486 PC with a SoundBlaster, so it
  was mapped to `dos`. Every item under it is a **Subor famiclone**:
  `emulator_ext` is `nes`, the subjects say Famiclone and Subor, the titles are
  Chinese NES multicarts and study cartridges. `nes` is not the answer either —
  a study cartridge wants the machine's keyboard and its own mapper, so filing
  it there would produce exactly the ROM that imports and does nothing.
- **Ambiguous targets** — `ruffle-swf` (Flash), `cloudpilot-*` (PalmOS), `v86`
  (a JavaScript x86 that boots whatever you give it).

Twelve of the machines this now imports to have **no EmulatorJS core** — the
Vectrex, the SG-1000, the Odyssey 2, the Channel F and friends. They import,
they appear in the library, and they do nothing when played. That is a fine
thing to want from a catalogue, so the Hub warns rather than refuses; each one
has a line in `rom_hub.playability.NO_EQUIVALENT` saying which machine it is and
why no playable slug is the same hardware.

## Notes

Search results carry `extra.stream_only`, so the UI can route an item to
streaming rather than offering an import that would be refused.

The plugin opens no sockets: `ctx.http` is an RPC back to the Hub, which
checks every URL against this plugin's declared allowlist
(`archive.org`, `*.archive.org`) before fetching anything — including the
download URLs returned in a `FetchPlan`, which the **Hub**, not the plugin,
fetches.
