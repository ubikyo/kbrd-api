from html import escape

MM_TO_PX = 96 / 25.4
KEY_HEIGHT_MM = 16
GAP_MM = 3
BORDER_RADIUS = 2


def _parts_path(
    key: dict,
    size: float,
    key_height: float,
    gap: float,
    parts: list,
) -> str:
    cells = set()
    for part in parts:
        for row in range(
            int(part.get("row", 0)),
            int(part.get("row", 0)) + max(1, int(part.get("rowspan", 0))),
        ):
            for column in range(
                int(part.get("column", 0)),
                int(part.get("column", 0)) + max(1, int(part.get("colspan", 0))),
            ):
                cells.add((row, column))

    segments = []
    column_step = size + gap
    row_step = key_height + gap

    for row, column in cells:
        x = key["x"] + column * column_step
        y = key["y"] + row * row_step
        right = x + size
        bottom = y + key_height
        if (row, column + 1) in cells:
            right += gap

        if (
            (row - 1, column) not in cells
            and (row, column - 1) not in cells
        ):
            segments.append(f"M{x:.3f},{y:.3f}h{right - x:.3f}")
        if (row, column + 1) not in cells:
            segments.append(f"M{right:.3f},{y:.3f}v{key_height:.3f}")
        if (
            (row + 1, column) not in cells
            and (row, column - 1) not in cells
        ):
            segments.append(f"M{right:.3f},{bottom:.3f}h-{right - x:.3f}")
        if (row, column - 1) not in cells:
            segments.append(f"M{x:.3f},{bottom:.3f}v-{key_height:.3f}")
    return " ".join(segments)


def _parts_path(key: dict) -> str:
    total_width = key["width"]
    y = key["y"]
    points = []
    rectangles = []

    for part in key["parts"]:
        width = part["width"]
        height = part["height"]
        align = part.get("align", "right")
        x = key["x"]
        if align == "right":
            x += total_width - width
        elif align == "center":
            x += (total_width - width) / 2
        rectangles.append((x, y, width, height))
        y += height

    first_x, first_y, first_width, _ = rectangles[0]
    last_x, _, last_width, last_height = rectangles[-1]
    total_bottom = y
    return (
        f"M{first_x:.3f},{first_y:.3f} "
        f"H{first_x + first_width:.3f} "
        f"V{total_bottom:.3f} "
        f"H{last_x:.3f} "
        f"V{total_bottom - last_height:.3f} "
        f"H{first_x:.3f} Z"
    )


def generate_geometry_svg(geometry: list, unit: str) -> str:
    if unit not in ("mm", "px"):
        raise ValueError("unit must be 'mm' or 'px'")
    if not isinstance(geometry, list):
        raise ValueError("geometry must be an array")

    scale = MM_TO_PX if unit == "mm" else 1.0
    gap = GAP_MM * scale
    key_height = KEY_HEIGHT_MM * scale
    keys = []
    group_x = 0.0
    max_bottom = 0.0

    for group_index, group in enumerate(geometry):
        if not isinstance(group, dict):
            raise ValueError(f"group {group_index} must be an object")
        rows = group.get("elements")
        if not isinstance(rows, list):
            raise ValueError(f"rows missing in group {group_index}")

        occupied = {}
        group_width = 0.0
        group_height = len(rows) * (key_height + gap) - gap

        for row_index, row in enumerate(rows):
            if not isinstance(row, list):
                raise ValueError(f"row {group_index}:{row_index} must be an array")
            occupied.setdefault(row_index, set())
            column = 0
            x = group_x

            for item_index, item in enumerate(row):
                if not isinstance(item, dict):
                    raise ValueError(f"element {group_index}:{row_index}:{item_index} must be an object")
                item_type = item.get("type", "key")
                if item_type not in ("key", "space"):
                    raise ValueError(f"invalid type at {group_index}:{row_index}:{item_index}")

                size = float(item.get("size", 0)) * scale
                quantity = int(item.get("quantity", 1))
                row_span = max(1, int(item.get("rowspan", 0)))
                col_span = max(1, int(item.get("colspan", 0)))
                parts = item.get("parts", [])
                if size <= 0 or quantity < 1 or not isinstance(parts, list):
                    raise ValueError(f"invalid element at {group_index}:{row_index}:{item_index}")

                if parts:
                    if len(parts) != 2:
                        raise ValueError("l shape requires two parts")
                    width = max(float(part["width"]) for part in parts) * scale
                    height = sum(float(part["height"]) for part in parts) * scale
                    item_col_span = 1
                else:
                    part_width = max(
                        [part.get("column", 0) + max(1, part.get("colspan", 0)) for part in parts]
                        or [col_span]
                    )
                    item_col_span = max(col_span, part_width)
                    part_height = max(
                        [part.get("row", 0) + max(1, part.get("rowspan", 0)) for part in parts]
                        or [row_span]
                    )
                    row_span = max(row_span, part_height)
                    width = size * item_col_span + gap * (item_col_span - 1)
                    height = key_height * row_span + gap * (row_span - 1)

                for quantity_index in range(quantity):
                    while column in occupied[row_index]:
                        column += 1
                    x = max(x, group_x)
                    y = row_index * (key_height + gap)
                    actual_ref = str(
                        item.get(
                            "ref",
                            f"g{group_index + 1}-r{row_index + 1}"
                            f"-c{column + 1}",
                        )
                    )
                    if "ref" not in item and quantity > 1:
                        actual_ref += f"-n{quantity_index + 1}"
                    shapes = parts or [{"row": 0, "column": 0, "rowspan": row_span, "colspan": col_span}]

                    if parts and item_type == "key":
                        keys.append({
                            "x": x,
                            "y": y,
                            "width": width,
                            "height": height,
                            "ref": actual_ref,
                            "name": str(item.get("name", "")),
                            "parts": [
                                {
                                    **part,
                                    "width": float(part["width"]) * scale,
                                    "height": float(part["height"]) * scale,
                                }
                                for part in parts
                            ],
                        })

                    for part in ([] if parts and item_type == "key" else shapes):
                        part_row = int(part.get("row", 0))
                        part_column = int(part.get("column", 0))
                        part_rowspan = max(1, int(part.get("rowspan", 0)))
                        part_colspan = max(1, int(part.get("colspan", 0)))
                        part_x = x + part_column * (size + gap)
                        part_y = y + part_row * key_height
                        part_width_px = size * part_colspan + gap * (part_colspan - 1)
                        part_height_px = key_height * part_rowspan + gap * (part_rowspan - 1)
                        if item_type == "key":
                            keys.append({
                                "x": part_x,
                                "y": part_y,
                                "width": part_width_px,
                                "height": part_height_px,
                                "ref": actual_ref,
                                "name": str(item.get("name", "")),
                            })

                    for covered_row in range(row_index, row_index + row_span):
                        occupied.setdefault(covered_row, set()).update(
                            range(column, column + item_col_span)
                        )
                    x += width + gap
                    column += item_col_span
                    group_width = max(group_width, x - group_x - gap)
                    group_height = max(group_height, y + height)

        group_x += group_width
        if group_index < len(geometry) - 1:
            group_x += float(group.get("gap", 0)) * scale
        max_bottom = max(max_bottom, group_height)

    if not keys:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>'

    rectangles = [
        (
            (
                '<path class="kbrd-key" '
                f'data-key="{escape(key["ref"])}" data-name="{escape(key["name"])}" '
                f'd="{_parts_path(key) if "parts" in key else _parts_path(key, key["size"], key["key_height"], gap, key["parts"])}" '
                'fill="rgba(0,0,0,0)" stroke="rgba(255,255,255,0.5)" stroke-width="1" />'
            ) if "parts" in key else (
                '<rect class="kbrd-key" '
                f'data-key="{escape(key["ref"])}" data-name="{escape(key["name"])}" '
                f'x="{key["x"]:.3f}" y="{key["y"]:.3f}" '
                f'width="{key["width"]:.3f}" height="{key["height"]:.3f}" '
                f'rx="{BORDER_RADIUS}" ry="{BORDER_RADIUS}" '
                'fill="rgba(0,0,0,0)" stroke="rgba(255,255,255,0.5)" stroke-width="1" />'
            )
        )
        for key in keys
    ]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {group_x:.3f} {max_bottom:.3f}" preserveAspectRatio="xMidYMid meet">'
        + "".join(rectangles)
        + "</svg>"
    )
