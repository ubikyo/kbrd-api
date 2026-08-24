from html import escape

from .geometry_layout import GeometryLayout, KeyLayout, layout_geometry


MM_TO_PX = 96 / 25.4
BORDER_RADIUS = 2


def _composite_path(key: KeyLayout, scale: float) -> str:
    rectangles = []
    y = key.y * scale
    total_width = key.width * scale

    for part in key.parts:
        width = part.width * scale
        height = part.height * scale
        x = key.x * scale
        if part.align == "right":
            x += total_width - width
        elif part.align == "center":
            x += (total_width - width) / 2
        rectangles.append((x, y, width, height))
        y += height

    top_x, top_y, top_width, _ = rectangles[0]
    bottom_x, _, _, bottom_height = rectangles[-1]
    return (
        f"M{top_x:.3f},{top_y:.3f} "
        f"H{top_x + top_width:.3f} "
        f"V{y:.3f} "
        f"H{bottom_x:.3f} "
        f"V{y - bottom_height:.3f} "
        f"H{top_x:.3f} Z"
    )


def _render_key(key: KeyLayout, scale: float) -> str:
    attributes = (
        f'data-key="{escape(key.ref)}" '
        f'data-name="{escape(key.name)}" '
    )
    style = (
        'fill="rgba(0,0,0,0)" '
        'stroke="rgba(255,255,255,0.5)" stroke-width="1"'
    )

    if key.parts:
        return (
            f'<path class="kbrd-key" {attributes}'
            f'd="{_composite_path(key, scale)}" {style} />'
        )

    return (
        f'<rect class="kbrd-key" {attributes}'
        f'x="{key.x * scale:.3f}" y="{key.y * scale:.3f}" '
        f'width="{key.width * scale:.3f}" height="{key.height * scale:.3f}" '
        f'rx="{BORDER_RADIUS}" ry="{BORDER_RADIUS}" {style} />'
    )


def render_geometry_svg(layout: GeometryLayout, unit: str) -> str:
    if unit not in ("mm", "px"):
        raise ValueError("unit must be 'mm' or 'px'")

    scale = MM_TO_PX if unit == "mm" else 1.0
    width = max(1.0, layout.width * scale)
    height = max(1.0, layout.height * scale)
    elements = "".join(_render_key(key, scale) for key in layout.keys)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width:.3f} {height:.3f}" '
        'preserveAspectRatio="xMidYMid meet">'
        f"{elements}</svg>"
    )


def generate_geometry_svg(geometry: list, unit: str) -> str:
    return render_geometry_svg(layout_geometry(geometry), unit)
