import unittest

from kbrd_api.api.geometry_layout import layout_geometry


class GeometryLayoutTest(unittest.TestCase):
    def test_groups_quantities_spaces_and_composite_key(self):
        layout = layout_geometry([
            {
                "gap": 10,
                "elements": [[
                    {"size": 16, "quantity": 2},
                    {"type": "space", "size": 8},
                    {
                        "size": 16,
                        "parts": [
                            {"width": 32, "height": 16},
                            {"width": 16, "height": 16},
                        ],
                    },
                ]],
            },
            {"elements": [[{"size": 16}]]},
        ])

        self.assertEqual((layout.width, layout.height), (107, 32))
        self.assertEqual(
            [(key.x, key.width) for key in layout.keys],
            [(0, 16), (19, 16), (49, 32), (91, 16)],
        )

    def test_rowspan_moves_next_row_to_free_space(self):
        layout = layout_geometry([{
            "elements": [
                [{"size": 16, "rowspan": 2}],
                [{"size": 16}],
            ],
        }])

        self.assertEqual(
            [(key.x, key.y) for key in layout.keys],
            [(0, 0), (19, 19)],
        )
        self.assertEqual((layout.width, layout.height), (35, 35))

    def test_rejects_invalid_composite_key(self):
        with self.assertRaisesRegex(ValueError, "requires two parts"):
            layout_geometry([{
                "elements": [[{
                    "size": 16,
                    "parts": [{"width": 16, "height": 16}],
                }]],
            }])


if __name__ == "__main__":
    unittest.main()
