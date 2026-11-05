"""Platform labels used by ROM-set archives and folders."""
import posixpath
import re

MAX_FOLDER_DEPTH = 5
ALIASES = {
    "sony - ps1": "psx", "ps": "psx", "ps1": "psx", "psx": "psx",
    "sony - psp": "psp", "psp": "psp", "ps2": "ps2",
    "arcade - mame 2003 plus": "arcade", "arcade": "arcade", "mame": "arcade",
    "atari - 2600": "atari2600", "atari2600": "atari2600",
    "nec - turbografx cd": "turbografx-cd", "pcecd": "turbografx-cd",
    "nec - turbografx-16": "tg16", "pce": "tg16",
    "nintendo - ds": "nds", "nds": "nds",
    "nintendo - game boy advance": "gba", "gba": "gba",
    "nintendo - game boy color": "gbc", "gbc": "gbc",
    "nintendo - game boy": "gb", "gb": "gb",
    "nintendo - gamecube": "ngc", "ngc": "ngc", "gc": "ngc",
    "nintendo - n64": "n64", "n64": "n64",
    "nintendo - nes": "nes", "nes": "nes", "fc": "nes",
    "nintendo - snes": "snes", "snes": "snes", "sfc": "snes",
    "neogeomvs": "neogeomvs", "neogeoaes": "neogeoaes",
    "sega - dreamcast": "dc", "dc": "dc",
    "sega - game gear": "gamegear", "gg": "gamegear",
    "sega - genesis": "genesis", "genesis": "genesis", "md": "genesis",
    "sega - master system": "sms", "sms": "sms",
    "sega - saturn": "saturn", "saturn": "saturn",
    "sega - sega cd": "segacd", "segacd": "segacd",
}
_SUFFIX = re.compile(r"\s+\((?:[a-z]-[a-z]|update \d+)\)$", re.I)


def within_depth(archive: str | None, name: str) -> bool:
    return name.count("/") + (archive.count("/") if archive else 0) <= MAX_FOLDER_DEPTH


def infer_platform(archive: str | None, name: str) -> str | None:
    labels = name.split("/")[:-1]
    if archive:
        labels += archive.split("/")[:-1]
        labels.append(posixpath.basename(archive).rsplit(".", 1)[0])
    platforms = {ALIASES[label] for part in labels
                 if (label := _SUFFIX.sub("", part.strip().casefold())) in ALIASES}
    return next(iter(platforms)) if len(platforms) == 1 else None
