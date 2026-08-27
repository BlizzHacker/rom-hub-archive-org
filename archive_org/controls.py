"""What Archive.org knows about how a game is controlled, and no more.

An Archive.org item that can be emulated in the browser has to tell the
reader which key is which console button, because the reader is holding a
keyboard and the game expects a joypad. That information is real, and it
is worth carrying into the library: a rom imported here plays in RomM's
own EmulatorJS player, which is the same keyboard-shaped problem.

**This module was written after measuring what is there, not before.**
Every count below is from a live census of the Console Living Room
(24,746 items), because the shape of the answer decides what may honestly
be carried.

## The three fields, and what each one actually is

`metadata.controller` -- **structured, and rare.** 405 items, every one
of them Atari 2600, and the whole vocabulary is four words::

    joystick  368     paddle  32     keypad  4     driving  1

This is the only genuinely structured control data in the collection. It
is carried verbatim.

`metadata.emulator_instructions` -- **prose, and trustworthy.** 1,818
items, and across all of them just **eight distinct texts**, one per
machine: Master System (553), Atari 2600 (514), Game Gear (427),
ColecoVision (234), SG-1000 (72), Super A'Can (9), Socrates (8),
Arcadia (1). Every one of the eight is a control description. It is
Archive.org's own field for exactly this, so it is carried whenever it is
present, with no test applied.

That last clause is a finding rather than laziness. Running
`is_control_text` over all eight rejects two of them -- Socrates
(*"included a full keyboard attachment, and so most keys should work"*)
and Arcadia (*"press the '1' key ... Press the arrow keys to move around
the bug"*) -- both of which plainly are control instructions written
without the word "button" or "joystick". A field whose entire population
is control text does not need a test, and applying one there would only
lose nine items.

`metadata.notes` -- **prose, mostly control boilerplate, not always.**
14,317 items. Of 4,000 sampled there were 72 distinct texts, and the ten
largest are per-machine control boilerplate covering ~99% of the
sample -- Mega Drive (3,343), Atari 7800 (218), WonderSwan (114), Atari
5200 (71), Intellivision (70), TurboGrafx-16 (67), Neo Geo Pocket (36).
The tail is not: `notes` is also where an uploader writes *"Unofficial
boxart by me"* or explains that a file is a WASM-4 cartridge. So `notes`
is carried **only when it reads as control text** -- see `is_control_text`
-- and the blob records that it was `notes` it came from, so a reader can
discount it.

## What this deliberately does not do

**It does not parse the prose into a key -> button table.** The
boilerplates look parseable -- *"There are three buttons, A, B and C,
which are CONTROL, ALT/OPTION and SPACE"* -- and a parser for the ten
common ones could be written. It would then be a table this plugin
invented, sitting in a field that says it came from Archive.org, and the
first uploader to reword a sentence would turn it into a wrong table
rather than a missing one. Archive.org publishes sentences; this carries
sentences.

**It does not fill in a machine's mapping from a sibling item.** The
boilerplate really is per-emulator, and copying the Mega Drive text onto
the 175 Mega Drive items that lack it would be defensible right up until
one of them is the item whose controls differ. An item with no control
text yields no control blob, and `MetadataPatch` reads an absent field as
"leave the library alone".

So the honest summary of coverage, on the collection this was measured
against: **16,127 of 24,746 items carry at least one of the three fields**
-- and per machine it swings from ~98% of the Mega Drive to 1 of 219 for
the SNES. A rom that gets no control blob is the normal case for several
platforms, and that is a fact about Archive.org rather than a gap here.
"""

from __future__ import annotations

import html
import re

#: Archive.org's own field for control instructions. Present on 1,818
#: items of the collection and control text on every one of them.
INSTRUCTIONS_FIELD = "emulator_instructions"

#: The free-text field that is *usually* control boilerplate.
NOTES_FIELD = "notes"

#: The one structured field. Four values across 405 items.
CONTROLLER_FIELD = "controller"

#: The vocabulary `controller` was observed to use, in full. Not used to
#: filter -- an unseen value is carried like any other, because a
#: controlled vocabulary this small is exactly the kind that gains a fifth
#: word without telling anybody. Recorded so a reader knows what the
#: field's range looked like when this was written.
CONTROLLER_VALUES = ("joystick", "paddle", "keypad", "driving")

#: Where the blob lands inside `raw_manual_metadata`. Namespaced, because
#: that field is one JSON object shared with whatever else writes to it
#: and an un-namespaced key would be a claim on the whole of it.
BLOB_KEY = "archive_org_controls"

#: Trim before the field's 256 KiB ceiling. The longest observed text is
#: under 1,200 characters; this is room for a much wordier one and a stop
#: well short of a runaway.
MAX_TEXT_CHARS = 8000

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")

#: A text must name something you press **and** something it does. Both
#: halves are required, which is what separates the boilerplate from
#: `"Unofficial boxart by me"` and from the WASM-4 blurb -- neither
#: contains any of these words at all.
_INPUT_WORDS = ("key", "keys", "keypad", "keyboard", "arrow", "press",
                "pressing", "ctrl", "control", "alt", "option", "shift",
                "space", "tab")
_DEVICE_WORDS = ("button", "buttons", "joystick", "joysticks", "controller",
                 "controllers", "paddle", "paddles", "keypad", "d-pad",
                 "gamepad", "trigger", "fire")

_WORD = re.compile(r"[a-z][a-z-]*")


def plain_text(value) -> str:
    """One field's value as readable text.

    Archive.org's control fields are HTML fragments -- `<b>1</b>`,
    `<a href=...>this</a>`, `<p>` -- and a few items give the same field
    as a list of strings rather than one. Tags are dropped rather than
    rendered: what is wanted is the sentence, and a library field is not
    a place to put someone else's markup.

    **Strip, unescape, strip again.** One pass in either order leaves a
    hole. Unescaping first and then stripping loses `a &lt; b`, which is
    prose and not a tag. Stripping first and then unescaping turns
    `&lt;b&gt;` into a `<b>` that was never in the source -- markup
    smuggled past the filter by being written as text. Doing both, in
    that order, keeps the prose and lets nothing through: the second pass
    removes anything the unescape created, and by then there is nothing
    left to unescape into a third.
    """
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value if v is not None)
    if not isinstance(value, str):
        return ""
    text = _TAG.sub(" ", html.unescape(_TAG.sub(" ", value)))
    return _SPACE.sub(" ", text).strip()[:MAX_TEXT_CHARS]


def is_control_text(text: str) -> bool:
    """Does this read as an explanation of how to control the game?

    Deliberately a low bar in one direction and a hard one in the other:
    it must name at least one thing a hand does to a keyboard *and* at
    least one thing on a controller. Every one of the ten boilerplate
    texts passes; the uploader notes that share the field
    (`"Unofficial boxart by me"`, the WASM-4 cartridge blurb) contain no
    word from either list.

    A false positive costs a reader one paragraph of prose filed under
    controls. A false negative costs them a mapping they wanted. Neither
    is silent, because the blob always records which field it came from.
    """
    words = set(_WORD.findall(text.lower()))
    return bool(words & set(_INPUT_WORDS)) and bool(words & set(_DEVICE_WORDS))


def extract(metadata: dict, identifier: str = "") -> dict | None:
    """The control blob for one item, or None when there is nothing real.

    None is the important return value. `MetadataPatch` treats an absent
    field as "leave the library alone", so an item Archive.org says
    nothing about must produce nothing here rather than an empty shell
    that would overwrite whatever a previous enrichment had put there.
    """
    if not isinstance(metadata, dict):
        return None

    blob: dict = {}
    sources: list[str] = []

    controller = metadata.get(CONTROLLER_FIELD)
    if isinstance(controller, str) and controller.strip():
        blob["controller"] = controller.strip()
        sources.append(CONTROLLER_FIELD)
    elif isinstance(controller, list):
        values = [c.strip() for c in controller if isinstance(c, str) and c.strip()]
        if values:
            blob["controller"] = values[0] if len(values) == 1 else values
            sources.append(CONTROLLER_FIELD)

    instructions = plain_text(metadata.get(INSTRUCTIONS_FIELD))
    if instructions:
        blob["instructions"] = instructions
        sources.append(INSTRUCTIONS_FIELD)

    notes = plain_text(metadata.get(NOTES_FIELD))
    if notes and is_control_text(notes):
        # Only when it is not already saying the same thing: a handful of
        # items carry both fields with identical text.
        if notes != instructions:
            blob["notes"] = notes
            sources.append(NOTES_FIELD)

    if not blob:
        return None

    blob["source"] = "archive.org"
    blob["source_fields"] = sources
    emulator = metadata.get("emulator")
    if isinstance(emulator, str) and emulator.strip():
        # The mapping is a property of the machine, so say which machine
        # Archive.org thought it was describing.
        blob["emulator"] = emulator.strip()
    if identifier:
        blob["identifier"] = identifier
    return blob


def patch_field(blob: dict | None) -> dict:
    """The `raw_metadata` mapping for a blob, or `{}` for none.

    `{}` is what `MetadataPatch` wants for "I know nothing": the field
    stays absent and the host leaves the library's own value alone. This
    is why the caller never writes `raw_manual_metadata` unconditionally.

    `raw_manual_metadata` is the field used because it is the only one of
    RPP's eight raw blobs that is not the name of a metadata *provider* --
    the other seven say IGDB, ScreenScraper, LaunchBox, Hasheous,
    Flashpoint, HowLongToBeat and MobyGames, and putting Archive.org's
    text in any of them would be a claim about where it came from that is
    not true. Note that a raw blob is *replaced*, not merged, by whoever
    writes it last; the namespaced `BLOB_KEY` at least makes it obvious
    whose it is.

    **RomM 4.9.2 accepts this field and stores nothing.** Measured against
    a real server, and it is the whole `raw_metadata` channel rather than
    this field: `PUT /api/roms/{id}` declares all eight raw fields in its
    own OpenAPI schema, answers 200 to every one of them, and reads back
    `{}` for every one of them. Pairing a blob with its id does not help
    -- `moby_id=12345` persists and `raw_moby_metadata` beside it does
    not -- and no rom in a 200-rom library has a non-empty raw blob of any
    kind.

    Nothing here works around that. `summary` does persist, and putting a
    keyboard mapping in the field that holds the game's description would
    make the library lie about what a field means, which is worse than
    missing data. This writes the field it should write; a backend that
    stores it will store it.
    """
    if not blob:
        return {}
    return {"raw_manual_metadata": {BLOB_KEY: blob}}
