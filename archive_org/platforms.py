"""Archive.org `metadata.emulator` -> RomM platform slug.

**This table is the only thing standing between an import and a ROM filed
under the wrong system**, so it is an exact-match lookup with no fallback.
An emulator that is not spelled out below raises "needs mapping" and the
import stops. That is deliberate: a visible gap is cheap to close, and a
silently misfiled ROM is not, because nothing about the library afterwards
says anything went wrong.

Two temptations were considered and rejected:

* **Prefix rules.** Archive.org's emulator strings have an obvious
  family/variant shape -- `vice-resid`, `vice-pet`, `pce-macplus`,
  `pce-atarist-color`, `apple2ee-helper` -- and a rule like "split on the
  first hyphen" would map most of them for free. It would also map
  `vice-pet` to the C64 and `pce-atarist-color` to a Mac, because in both
  families the variant *is* the machine. The families are not hierarchies,
  so the shortcut is wrong exactly where it looks most useful.
* **Falling back to `dos`,** or to anything else, for an unknown emulator.
  That is the misfiling this table exists to prevent.

The keys were sampled from live Archive.org (2,000 items across
`collection:(softwarelibrary) AND emulator:[* TO *]`, ~220k items total),
so they are what the corpus actually contains rather than what an
emulator list suggests it might. The values were checked against RomM
4.9.2's own platform-slug enum -- a slug RomM does not know would fail
later, at `platform_id()`, with a much less useful message.

**The console half was measured, not guessed.** Every `emulator` value in
the Console Living Room was counted -- 132 distinct values over 24,746
items -- and the rows below marked *(CLR)* were added from that census
with the item count they answer for. Two things follow from having the
whole distribution rather than a sample:

* The console ids are **flat**, unlike the home-computer families the
  paragraph above warns about. `megadriv`, `megadrij` and `genesis` are
  one machine under three spellings; `gameboy`, `gbcolor` and `gba` are
  three machines under three spellings. Neither is a hierarchy, so each
  one is still written out.
* A `p`/`j` suffix is a **region**, not a machine: `nesp` and `nespal` are
  a PAL NES, `smsj` is a Japanese Master System, `a2600p` a PAL 2600.
  RomM has one slug per machine, so they all land on it.

Emulators deliberately left out surface as "needs mapping", which is the
correct answer until someone decides where they should land:

* **Ambiguous targets.** `ruffle-swf` (Flash), `cloudpilot-*` (PalmOS),
  `v86` (a JavaScript x86 that boots whatever you give it).
* **Machines RomM has no slug for**, checked against the slugs the other
  plugins in this catalogue already file to: `bally` (Bally Astrocade,
  20 items), `apfm1000` (APF MP-1000, 15), `socrates` (VTech Socrates, 8),
  `sv8000` (Bandai Super Vision 8000, 7), `gamepock` (Epoch Game Pocket
  Computer, 11), `fgtlayer` (4). A mapping cannot be invented for a
  platform the library does not have.
* **MAME romset names.** About 60 values -- `galaxian`, `mspacman`,
  `outrun`, `tmnt2`, `bublbobl` and so on -- each carried by one or two
  items, ~120 items in total. They are almost certainly `arcade`, and
  "almost certainly" is not the standard this table is held to: the key
  is a *game* id rather than a *machine* id, so the family cannot be
  closed by inspection and each would have to be verified individually.
  `mame` itself is mapped, and these are not it.
* **`sb486`** (30 items), which is the one row of this table that was
  written from reasoning and then deleted after checking. The name reads
  as a 486 PC with a SoundBlaster, so it was mapped to `dos`. Every item
  under it is a **Subor famiclone**: `emulator_ext` is `nes`, the
  subjects say `Famiclone` / `Subor`, and the titles are Chinese NES
  multicarts and study cartridges. `nes` is not the answer either -- a
  Subor study cartridge wants the machine's keyboard and its own mapper,
  so filing it under `nes` would produce exactly the ROM that imports and
  does nothing. Two plausible slugs and no way to choose between them
  from the metadata is what "needs mapping" is for.
* **Composite values.** `gameboy,gb` and
  `dosbox,dosbox_drive_d,emularity_win31/win31.zip` are one item each and
  are an emulator id with a loader configuration appended. Splitting on
  the comma is exactly the prefix rule this table refuses.
* **`genisis`** -- one item, and a misspelling of `genesis` rather than a
  machine. Mapping a typo teaches the table to accept typos.
"""

# Archive.org emulator id -> RomM platform slug.
EMULATOR_PLATFORMS: dict[str, str] = {
    # PC
    "dosbox": "dos",
    "dosbox-sync": "dos",
    # Commodore. `vice-resid` is VICE with the reSID chip emulation and is
    # by far the most common emulator id in the corpus; `vice-pet` is the
    # same emulator pointed at a completely different machine.
    "vice": "c64",
    "vice-resid": "c64",
    "vice-c64": "c64",
    "vice-pet": "cpet",
    "vice-vic20": "vic-20",
    # Apple. Every apple2* variant is an Apple II revision or disk format
    # (`woz` is an image format, `ee` a ROM revision); the IIgs and the ///
    # are separate machines and separate slugs.
    "apple2": "appleii",
    "apple2e": "appleii",
    "apple2ee": "appleii",
    "apple2ee-helper": "appleii",
    "apple2eeecho": "appleii",
    "apple2p": "appleii",
    "apple2woz": "appleii",
    "apple2gs": "apple-iigs",
    "apple3": "appleiii",
    # Atari 8-bit. RomM folds the 800/800XL line into one slug.
    "a800": "atari8bit",
    "a800xl": "atari8bit",
    "a800xlp": "atari8bit",
    "a800cart": "atari8bit",
    # Commodore Amiga, via Scripted Amiga Emulator.
    "sae-a500p": "amiga",
    "sae-a500": "amiga",
    "sae-a1200": "amiga",
    # Amstrad CPC.
    "cpc6128": "acpc",
    # Sinclair. The ZX81 and the Spectrum are distinct slugs in RomM.
    "zx81": "zx81",
    "spectrum": "zxs",
    # (CLR) `spec128` is the 128K Spectrum -- more memory, same machine
    # and the same `fuse` core.
    "spec128": "zxs",
    # Sega. (CLR) `megadriv` 1,785 and `megadrij` 103 are the same machine
    # as `genesis` 10,557 under Archive.org's export/domestic spellings.
    "megadriv": "genesis",
    "megadrij": "genesis",
    "genesis": "genesis",
    "32x": "sega32",
    "gamegear": "gamegear",
    "sms": "sms",
    # (CLR) `smsj` is the Japanese Master System, `sms-phaser` the same
    # machine configured for the Light Phaser light gun. One RomM slug.
    "smsj": "sms",
    "sms-phaser": "sms",
    "sg1000": "sg1000",
    # Nintendo.
    "nes": "nes",
    # (CLR) PAL NES, under two spellings. A region, not a machine.
    "nesp": "nes",
    "nespal": "nes",
    "snes": "snes",
    "snesp": "snes",
    "gameboy": "gb",
    "gbcolor": "gbc",
    "gba": "gba",
    # Atari consoles. (CLR) `a2600` alone is 3,025 items -- the second
    # largest machine in the collection after the Mega Drive.
    "a2600": "atari2600",
    "a2600p": "atari2600",
    "a5200": "atari5200",
    "a7800": "atari7800",
    "lynx": "lynx",
    # NEC. `sgx` is the SuperGrafx, which is not a TurboGrafx-16: it has
    # its own slug in RomM and its own EmulatorJS core.
    "tg16": "tg16",
    "sgx": "supergrafx",
    # SNK handhelds.
    "ngp": "neo-geo-pocket",
    "ngpc": "neo-geo-pocket-color",
    # Bandai.
    "wswan": "wonderswan",
    "wscolor": "wonderswan-color",
    # Sony.
    "psx": "psx",
    # Mattel. Three Intellivision revisions, one machine.
    "intv": "intellivision",
    "intv2": "intellivision",
    "intvsrs": "intellivision",
    # Coleco.
    "coleco": "colecovision",
    # Machines with no EmulatorJS core. Importing them is a legitimate
    # thing to want -- a library is not only a player -- and the host says
    # so before the ROM lands: see `rom_hub.playability`.
    "vectrex": "vectrex",
    "odyssey2": "odyssey-2",
    "channelf": "fairchild-channel-f",
    "arcadia": "arcadia-2001",
    "gamecom": "game-dot-com",
    "svision": "supervision",
    "megaduck": "mega-duck-slash-cougar-boy",
    "supracan": "super-acan",
    "scv": "epoch-super-cassette-vision",
    "crvision": "creativision",
    "advision": "adventure-vision",
    # The GX4000 is Amstrad's console. It shares the CPC's hardware and
    # not its media -- cartridges, no disk drive, no keyboard -- and RomM
    # gives it its own slug, so it does not fold into `acpc`.
    "gx4000": "amstrad-gx4000",
    # Hampa Hug's `pce`, which is a machine emulator rather than a PC
    # Engine emulator -- the variant names the machine.
    "pce-macplus": "mac",
    "pce-atarist-color": "atari-st",
    "pce-atarist": "atari-st",
    # (CLR) vMac in its Colour Macintosh configuration. Still a Mac.
    "vmac-colormac": "mac",
    # Tandy / Radio Shack.
    "mc10": "trs-80-mc-10",
    "coco2cart": "trs-80-color-computer",
    "coco2disk": "trs-80-color-computer",
    "coco3disk": "trs-80-color-computer",
    # Mattel.
    "aquarius": "aquarius",
    # Arcade.
    "mame": "arcade",
}


def platform_for(emulator: str) -> str | None:
    """The RomM platform slug for an Archive.org emulator id, or None.

    None means "not in the table", which callers must turn into a visible
    refusal. It never means "use a default".
    """
    if not isinstance(emulator, str):
        return None
    return EMULATOR_PLATFORMS.get(emulator.strip().lower())


def emulators_for(platform: str) -> list[str]:
    """Every Archive.org emulator id that maps to one RomM slug.

    The table read backwards, which is what a `--platform` filter needs:
    an operator asking for `genesis` means all three of `genesis`,
    `megadriv` and `megadrij`, and asking for `sms` means `sms`, `smsj`
    and `sms-phaser`. Sorted so the query a search builds is stable and a
    test can assert on it.

    Empty means this source has nothing filed under that platform, which
    a caller must report rather than silently widen into "everything".
    """
    if not isinstance(platform, str):
        return []
    wanted = platform.strip().lower()
    return sorted(e for e, slug in EMULATOR_PLATFORMS.items() if slug == wanted)
