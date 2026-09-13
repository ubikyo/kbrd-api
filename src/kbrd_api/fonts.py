"""What a font file calls itself.

The editors' font picker is two fields — a family and, beside it, the
style — rather than one list of filenames, and a filename can't answer
either of them. `Inter_18pt-Regular.ttf` splits plausibly enough on the
hyphen, but nothing about the string says that its family is displayed
"Inter 18pt" and not "Inter_18pt"; and a face named
`FiraCodeNerdFont.ttf` defeats the split entirely — a camel-case rule
would read it "Fira Code Nerd Font", while the font's own name for
itself is "FiraCode Nerd Font". No amount of guessing gets there,
because the space that isn't in "FiraCode" is knowledge only the file
carries.

So the file is asked. Every TrueType and OpenType face carries a `name`
table of exactly these strings, and an `OS/2` table saying where the
style falls on the weight axis — enough to sort a family's faces the way
a type specimen does (Thin … Black, romans before italics) rather than
alphabetically, which would open Inter on "Black".

Only those two tables are read, through a handful of seeks: the font
directory here holds a colour emoji face of several megabytes, and
listing the fonts must not mean reading it.

Nothing in here guesses. A file whose tables can't be read falls back to
its own stem and *no* style at all — which is the one-entry-per-file list
the picker showed before families existed, not a wrong family name. An
empty style is the picker's cue to say "Regular" as its own word: the
field never stands blank, and nothing is claimed about a face that
didn't say.
"""

import struct
from pathlib import Path
from typing import NamedTuple


class FontName(NamedTuple):
    """A face as it describes itself."""

    family: str
    # Empty when the file names no style — never filled in with a guess.
    # `label` drops it and the editors show "Regular" in its place.
    style: str
    # `usWeightClass`, 100–900 — the axis a family's styles are ordered
    # along, and the reason "SemiBold" sorts after "Medium" here and not
    # before "Thin" as it would alphabetically.
    weight: int
    italic: bool


# The four `name` IDs that matter, in the order they're asked for.
#
# 16/17 ("typographic", the OpenType pair) before 1/2 (the original
# Windows pair), because 1 is only the family for as many faces as the
# pre-OpenType model could hold — four, the RIBBI set — and carries the
# style for the rest. Inter is the plain case: `Inter_18pt-Light.ttf` has
# 1 = "Inter 18pt Light" and 2 = "Regular", which would file it under a
# family of its own; 16/17 say "Inter 18pt" and "Light", which is the
# grouping the picker needs. Fonts with no more than four faces
# (Quicksand's Bold, Teko's) set 16/17 on none of them and are read
# correctly from 1/2.
TYPOGRAPHIC_FAMILY, TYPOGRAPHIC_SUBFAMILY = 16, 17
FAMILY, SUBFAMILY = 1, 2
_WANTED = (TYPOGRAPHIC_FAMILY, TYPOGRAPHIC_SUBFAMILY, FAMILY, SUBFAMILY)

# What an sfnt may say it is. `0x00010000` and `true` are TrueType
# outlines, `OTTO` is CFF — the `.otf` half of ALLOWED_FONT_EXTENSIONS —
# and all three carry the same table directory, which is all that's read
# past this point.
_SFNT_VERSIONS = {b"\x00\x01\x00\x00", b"true", b"OTTO", b"typ1"}

# Windows' en-US, the language a `name` record is picked by below.
_WINDOWS_ENGLISH = 0x0409

# Where a style name falls on the weight axis, for the one job of
# breaking a tie between two faces that claim the *same*
# `usWeightClass`. Inter is why this exists: its Thin and its ExtraLight
# both say 250, and with nothing but the name left to go on the
# alphabet would open the family on ExtraLight.
#
# Only a tie-breaker, never the primary key — a family whose style names
# are its own ("Retina", "Book", "Poster") still sorts by the axis it
# actually declares, and an unknown name simply falls to the end of its
# own weight.
_STYLE_ORDER = {
    "thin": 0,
    "hairline": 0,
    "extralight": 1,
    "ultralight": 1,
    "light": 2,
    "book": 3,
    "regular": 4,
    "normal": 4,
    "": 4,
    "medium": 5,
    "semibold": 6,
    "demibold": 6,
    "bold": 7,
    "extrabold": 8,
    "ultrabold": 8,
    "black": 9,
    "heavy": 9,
    "extrablack": 10,
    "ultrablack": 10,
}
_UNKNOWN_STYLE = 11

# Dropped before a style name is looked up above: the slant is already a
# sort dimension of its own, so "Black Italic" ranks exactly where
# "Black" does.
_SLANTS = ("italic", "oblique")

# The weight assumed of a face whose `OS/2` table is missing or
# unreadable: "Regular" on the axis, so an unsorted face lands mid-family
# rather than at either end.
_DEFAULT_WEIGHT = 400

# Keyed by the file's identity *and* its stamp, so replacing an uploaded
# font under the same name re-reads it. Cleared wholesale rather than
# evicted one by one: the entries are three small fields each, and the
# directory this serves holds dozens of files, not thousands.
_CACHE: dict[tuple[str, int, int], FontName] = {}
_CACHE_LIMIT = 256


def describe(path) -> FontName:
    """The family, style and weight the file at `path` claims.

    Never raises: an unreadable or malformed file comes back named after
    itself.
    """
    path = Path(path)
    try:
        stamp = path.stat()
    except OSError:
        return _from_filename(path)
    key = (str(path), stamp.st_mtime_ns, stamp.st_size)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    name = _read(path)
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = name
    return name


def label(name: FontName) -> str:
    """The one-line name for a picker that has no room for two fields.

    "Regular" is dropped rather than shown: it is what a family is called
    when nothing else is said about it, and "Quicksand Regular" reads as
    a face picked out of a family where "Quicksand" reads as the family
    itself.
    """
    if not name.style or name.style.casefold() == "regular":
        return name.family
    return f"{name.family} {name.style}"


def sort_key(name: FontName):
    """Families alphabetically; inside one, a type specimen's order.

    Romans before italics, and each run light to heavy — so Inter opens
    on Thin and reaches Black, instead of the alphabetical order that
    puts Black first and Thin last.

    Where two faces claim the same weight — which Inter's Thin and
    ExtraLight both do, at 250 — the style name breaks the tie, by what
    it means (`_STYLE_ORDER`) and then by the alphabet, so the pair still
    comes out in the order a specimen prints them.
    """
    return (
        name.family.casefold(),
        name.italic,
        name.weight,
        _style_order(name.style),
        name.style.casefold(),
    )


def _style_order(style: str) -> int:
    words = [
        word
        for word in style.casefold().replace("-", " ").split()
        if word not in _SLANTS
    ]
    return _STYLE_ORDER.get("".join(words), _UNKNOWN_STYLE)


def _from_filename(path: Path) -> FontName:
    return FontName(path.stem, "", _DEFAULT_WEIGHT, False)


def _read(path: Path) -> FontName:
    try:
        with path.open("rb") as handle:
            directory = _table_directory(handle)
            names = (
                _names(handle, *directory[b"name"]) if b"name" in directory else {}
            )
            weight, italic = (
                _metrics(handle, *directory[b"OS/2"])
                if b"OS/2" in directory
                else (_DEFAULT_WEIGHT, False)
            )
    except (OSError, struct.error, ValueError):
        return _from_filename(path)
    family = _pick(names, TYPOGRAPHIC_FAMILY, FAMILY)
    style = _pick(names, TYPOGRAPHIC_SUBFAMILY, SUBFAMILY)
    if not family:
        # A file with outlines but no name for itself — the picker is
        # better off showing the filename than an empty row.
        return _from_filename(path)
    return FontName(family, style, weight, italic)


def _pick(names: dict[int, str], *ids: int) -> str:
    for name_id in ids:
        text = names.get(name_id)
        if text:
            return text
    return ""


def _table_directory(handle) -> dict[bytes, tuple[int, int]]:
    """Every table in the font, as `{tag: (offset, length)}`."""
    head = handle.read(12)
    if len(head) < 12:
        return {}
    if head[:4] == b"ttcf":
        # A collection. `/api/fonts/<filename>` hands the whole file to
        # the renderer, which draws with the first font in it, so that's
        # the one described here.
        handle.seek(12)
        offset = handle.read(4)
        if len(offset) < 4:
            return {}
        handle.seek(struct.unpack(">I", offset)[0])
        head = handle.read(12)
        if len(head) < 12:
            return {}
    if head[:4] not in _SFNT_VERSIONS:
        return {}
    count = struct.unpack_from(">H", head, 4)[0]
    records = handle.read(count * 16)
    directory = {}
    for record in range(0, len(records) - 15, 16):
        tag, _checksum, offset, length = struct.unpack_from(">4sIII", records, record)
        directory[tag] = (offset, length)
    return directory


def _names(handle, offset: int, length: int) -> dict[int, str]:
    """The wanted `name` records, each in its best available encoding."""
    handle.seek(offset)
    table = handle.read(length)
    if len(table) < 6:
        return {}
    count, strings = struct.unpack_from(">2xHH", table)
    # Score against score, so the last record read doesn't simply win:
    # one face can carry the same string four times over, and the one to
    # show is the one most likely to be both English and readable.
    best: dict[int, tuple[int, str]] = {}
    for index in range(count):
        record = 6 + index * 12
        if record + 12 > len(table):
            break
        platform, encoding, language, name_id, size, where = struct.unpack_from(
            ">6H", table, record
        )
        if name_id not in _WANTED:
            continue
        score = _score(platform, language)
        if score <= best.get(name_id, (-1, ""))[0]:
            continue
        raw = table[strings + where : strings + where + size]
        if len(raw) < size:
            continue
        text = _decode(platform, raw)
        if text:
            best[name_id] = (score, text)
    return {name_id: text for name_id, (_score_, text) in best.items()}


def _score(platform: int, language: int) -> int:
    """How much a record is worth showing, highest first."""
    if platform == 3:  # Windows, which every font in practice carries.
        return 4 if language == _WINDOWS_ENGLISH else 3
    if platform == 0:  # Unicode, same UTF-16BE strings.
        return 2
    if platform == 1:  # Macintosh; 0 is its English.
        return 1 if language == 0 else 0
    return 0


def _decode(platform: int, raw: bytes) -> str:
    # Windows and Unicode records are UTF-16BE — including Windows
    # Symbol (3/0), whose *strings* are ordinary text however its cmap
    # is encoded. Everything else is treated as Mac Roman, which is
    # what platform 1 means for the Latin script the names use.
    encoding = "utf-16-be" if platform in (0, 3) else "mac-roman"
    try:
        return raw.decode(encoding).replace("\x00", "").strip()
    except (UnicodeDecodeError, LookupError):
        return ""


def _metrics(handle, offset: int, length: int) -> tuple[int, bool]:
    """`usWeightClass` and the italic bit, both out of `OS/2`."""
    # 78 bytes covers every version of the table through `fsSelection`
    # at 62; the fields past it are metrics nothing here reads.
    handle.seek(offset)
    table = handle.read(min(length, 78))
    weight = _DEFAULT_WEIGHT
    italic = False
    if len(table) >= 6:
        claimed = struct.unpack_from(">H", table, 4)[0]
        # Out-of-range values exist in the wild (0, or a CSS 1–9). Only
        # the ordering depends on this, so an implausible one is better
        # ignored than allowed to file a face at one end of its family.
        if 1 <= claimed <= 1000:
            weight = claimed
    if len(table) >= 64:
        italic = bool(struct.unpack_from(">H", table, 62)[0] & 0x01)
    return weight, italic
