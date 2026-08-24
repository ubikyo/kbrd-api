import unittest

from kbrd_api.api.geometry_svg import generate_geometry_svg


class GeometrySvgTest(unittest.TestCase):
    def test_renders_regular_and_composite_keys(self):
        svg = generate_geometry_svg([{
            "elements": [[
                {"size": 16, "name": "A&B"},
                {
                    "size": 16,
                    "ref": 'enter"key',
                    "parts": [
                        {"width": 32, "height": 16},
                        {"width": 16, "height": 16},
                    ],
                },
            ]],
        }], "px")

        self.assertEqual(svg.count("<rect "), 1)
        self.assertEqual(svg.count("<path "), 1)
        self.assertIn('data-name="A&amp;B"', svg)
        self.assertIn('data-key="enter&quot;key"', svg)
        self.assertIn('viewBox="0 0 51.000 32.000"', svg)

    def test_converts_millimetres_to_svg_pixels(self):
        svg = generate_geometry_svg(
            [{"elements": [[{"size": 25.4}]]}],
            "mm",
        )
        self.assertIn('width="96.000"', svg)


if __name__ == "__main__":
    unittest.main()
