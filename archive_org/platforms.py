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

Emulators deliberately left out because their target is ambiguous
(`ruffle-swf`, `cloudpilot-*`) surface as "needs mapping", which is the
correct answer until someone decides where they should land.
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
    # Amstrad CPC.
    "cpc6128": "acpc",
    # Sinclair. The ZX81 and the Spectrum are distinct slugs in RomM.
    "zx81": "zx81",
    "spectrum": "zxs",
    # Sega.
    "megadriv": "genesis",
    "genesis": "genesis",
    "gamegear": "gamegear",
    "sms": "sms",
    # Nintendo.
    "nes": "nes",
    # Hampa Hug's `pce`, which is a machine emulator rather than a PC
    # Engine emulator -- the variant names the machine.
    "pce-macplus": "mac",
    "pce-atarist-color": "atari-st",
    "pce-atarist": "atari-st",
    # Tandy / Radio Shack.
    "mc10": "trs-80-mc-10",
    "coco2cart": "trs-80-color-computer",
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
