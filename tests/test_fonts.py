"""`kbrd_api.fonts` against fonts built here, byte by byte.

Real `.ttf` files are megabytes and can't be committed, so each test
assembles the smallest sfnt that carries what it is about: a table
directory, a `name` table of the records under test and an `OS/2` table
of one weight. Nothing else is filled in — no font in these tests has an
outline in it, which is exactly the point: the reader must never need to
get past those two tables.
"""

import struct
import tempfile
import unittest
from pathlib import Path

from kbrd_api import fonts


def name_table(records):
    """A `name` table of `{(platform, language, name_id): text}`."""
    entries = []
    strings = b""
    for (platform, language, name_id), text in records.items():
        raw = text.encode("utf-16-be" if platform in (0, 3) else "mac-roman")
        entries.append(
            struct.pack(
                ">6H",
                platform,
                1 if platform == 3 else 0,
                language,
                name_id,
                len(raw),
                len(strings),
            )
        )
        strings += raw
    header = struct.pack(">HHH", 0, len(entries), 6 + len(entries) * 12)
    return header + b"".join(entries) + strings


def os2_table(weight, italic):
    table = bytearray(78)
    struct.pack_into(">H", table, 4, weight)
    struct.pack_into(">H", table, 62, 1 if italic else 0)
    return bytes(table)


def font(names, weight=400, italic=False, version=b"\x00\x01\x00\x00"):
    """An sfnt carrying just `OS/2` and `name`, in tag order."""
    tables = [(b"OS/2", os2_table(weight, italic)), (b"name", name_table(names))]
    offset = 12 + len(tables) * 16
    directory = b""
    body = b""
    for tag, data in tables:
        directory += struct.pack(">4sIII", tag, 0, offset + len(body), len(data))
        # Tables are four-byte aligned in a real font; padding here keeps
        # the offsets honest even though nothing reads across them.
        body += data + b"\x00" * (-len(data) % 4)
    return version + struct.pack(">HHHH", len(tables), 0, 0, 0) + directory + body


WINDOWS_ENGLISH = (3, 0x0409)


class FontsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, filename, data):
        path = Path(self.dir.name, filename)
        path.write_bytes(data)
        return path

    def test_prefers_the_typographic_family_over_the_windows_one(self):
        # Inter's own bytes: nameID 1 carries the weight, which would
        # file every cut of the family under a family of its own, and
        # nameID 2 says nothing useful at all.
        path = self.write(
            "Inter_18pt-Light.ttf",
            font(
                {
                    (*WINDOWS_ENGLISH, 1): "Inter 18pt Light",
                    (*WINDOWS_ENGLISH, 2): "Regular",
                    (*WINDOWS_ENGLISH, 16): "Inter 18pt",
                    (*WINDOWS_ENGLISH, 17): "Light",
                },
                weight=300,
            ),
        )

        name = fonts.describe(path)

        self.assertEqual(name.family, "Inter 18pt")
        self.assertEqual(name.style, "Light")
        self.assertEqual(name.weight, 300)
        self.assertFalse(name.italic)

    def test_falls_back_to_the_windows_pair(self):
        # A family of no more than four faces sets 16/17 on none of them.
        path = self.write(
            "Quicksand-Bold.ttf",
            font(
                {
                    (*WINDOWS_ENGLISH, 1): "Quicksand",
                    (*WINDOWS_ENGLISH, 2): "Bold",
                },
                weight=700,
            ),
        )

        self.assertEqual(fonts.describe(path), ("Quicksand", "Bold", 700, False))

    def test_reads_a_name_no_filename_could_be_split_into(self):
        # The case the whole module exists for: no rule over
        # "FiraCodeNerdFont" produces the space in "Nerd Font" *and* the
        # one that isn't in "FiraCode".
        path = self.write(
            "FiraCodeNerdFont-Retina.ttf",
            font(
                {
                    (*WINDOWS_ENGLISH, 1): "FiraCode Nerd Font",
                    (*WINDOWS_ENGLISH, 2): "Retina",
                }
            ),
        )

        self.assertEqual(fonts.describe(path).family, "FiraCode Nerd Font")

    def test_prefers_english_windows_records_to_the_rest(self):
        path = self.write(
            "Teko-Regular.ttf",
            font(
                {
                    (1, 0, 1): "Teko (Mac)",
                    (3, 0x040C, 1): "Teko (français)",
                    (*WINDOWS_ENGLISH, 1): "Teko",
                }
            ),
        )

        self.assertEqual(fonts.describe(path).family, "Teko")

    def test_reads_a_macintosh_record_when_it_is_all_there_is(self):
        path = self.write(
            "Ancient.ttf", font({(1, 0, 1): "Ancient", (1, 0, 2): "Bold"})
        )

        self.assertEqual(fonts.describe(path).family, "Ancient")
        self.assertEqual(fonts.describe(path).style, "Bold")

    def test_reads_an_opentype_cff_face(self):
        path = self.write(
            "Something.otf",
            font({(*WINDOWS_ENGLISH, 1): "Something"}, version=b"OTTO"),
        )

        self.assertEqual(fonts.describe(path).family, "Something")

    def test_an_unreadable_file_is_named_after_itself_with_no_style(self):
        # Whatever else happens, the picker gets a row it can show. The
        # empty style is what the editors turn into "Regular" — nothing
        # is claimed about a face that didn't say.
        path = self.write("Emoji.ttf", b"not a font at all")

        self.assertEqual(fonts.describe(path), ("Emoji", "", 400, False))

    def test_a_missing_file_is_named_after_itself(self):
        self.assertEqual(
            fonts.describe(Path(self.dir.name, "Gone.ttf")).family, "Gone"
        )

    def test_an_implausible_weight_is_ignored(self):
        # A CSS weight (1–9) or a zero, both of which exist in the wild.
        path = self.write(
            "Odd.ttf", font({(*WINDOWS_ENGLISH, 1): "Odd"}, weight=0)
        )

        self.assertEqual(fonts.describe(path).weight, 400)

    def test_rereads_a_font_replaced_under_the_same_name(self):
        path = self.write("Face.ttf", font({(*WINDOWS_ENGLISH, 1): "First"}))
        self.assertEqual(fonts.describe(path).family, "First")

        path.write_bytes(font({(*WINDOWS_ENGLISH, 1): "Second Face"}))

        self.assertEqual(fonts.describe(path).family, "Second Face")

    def test_label_drops_a_plain_regular(self):
        self.assertEqual(fonts.label(fonts.FontName("Teko", "", 400, False)), "Teko")
        self.assertEqual(
            fonts.label(fonts.FontName("Teko", "Regular", 400, False)), "Teko"
        )
        self.assertEqual(
            fonts.label(fonts.FontName("Teko", "SemiBold", 600, False)),
            "Teko SemiBold",
        )

    def test_the_style_name_breaks_a_tie_between_equal_weights(self):
        # Inter's own numbers: both faces say 250, so the axis can't
        # separate them and the alphabet would put ExtraLight first.
        family = [
            fonts.FontName("Inter", "ExtraLight", 250, False),
            fonts.FontName("Inter", "Thin", 250, False),
        ]

        self.assertEqual(
            [name.style for name in sorted(family, key=fonts.sort_key)],
            ["Thin", "ExtraLight"],
        )

    def test_a_style_name_of_its_own_sorts_on_the_weight_it_declares(self):
        family = [
            fonts.FontName("Fira", "Retina", 450, False),
            fonts.FontName("Fira", "Regular", 400, False),
            fonts.FontName("Fira", "Medium", 500, False),
        ]

        self.assertEqual(
            [name.style for name in sorted(family, key=fonts.sort_key)],
            ["Regular", "Retina", "Medium"],
        )

    def test_sorts_a_family_the_way_a_specimen_does(self):
        family = [
            fonts.FontName("Inter", "Bold", 700, False),
            fonts.FontName("Inter", "Black Italic", 900, True),
            fonts.FontName("Inter", "Thin", 100, False),
            fonts.FontName("Inter", "Regular", 400, False),
            fonts.FontName("Inter", "Light Italic", 300, True),
        ]

        self.assertEqual(
            [name.style for name in sorted(family, key=fonts.sort_key)],
            ["Thin", "Regular", "Bold", "Light Italic", "Black Italic"],
        )


if __name__ == "__main__":
    unittest.main()
