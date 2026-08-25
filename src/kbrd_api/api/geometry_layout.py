from dataclasses import dataclass


KEY_HEIGHT = 16.0
GAP = 3.0


@dataclass(frozen=True)
class PartLayout:
    width: float
    height: float
    align: str = "right"


@dataclass(frozen=True)
class KeyLayout:
    x: float
    y: float
    width: float
    height: float
    ref: str
    name: str
    parts: tuple[PartLayout, ...] = ()
    type: str = "key"


@dataclass(frozen=True)
class GeometryLayout:
    width: float
    height: float
    keys: tuple[KeyLayout, ...]


def _number(value, error: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(error)
    return float(value)


def _positive_number(value, error: str) -> float:
    number = _number(value, error)
    if number <= 0:
        raise ValueError(error)
    return number


def _span(item: dict, name: str) -> int:
    value = item.get(name, 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid {name}")
    return max(1, value)


def _parts(item: dict) -> tuple[PartLayout, ...]:
    raw_parts = item.get("parts", [])
    if not isinstance(raw_parts, list):
        raise ValueError("parts must be an array")
    if not raw_parts:
        return ()
    if len(raw_parts) != 2:
        raise ValueError("composite key requires two parts")

    parts = []
    for part in raw_parts:
        if not isinstance(part, dict):
            raise ValueError("part must be an object")
        align = part.get("align", "right")
        if align not in ("left", "center", "right"):
            raise ValueError("invalid composite part alignment")
        parts.append(PartLayout(
            width=_positive_number(part.get("width"), "invalid composite part width"),
            height=_positive_number(part.get("height"), "invalid composite part height"),
            align=align,
        ))
    return tuple(parts)


def _next_free_x(
    x: float,
    width: float,
    intervals: list[tuple[float, float]],
) -> float:
    for left, right in sorted(intervals):
        if x + width <= left - GAP:
            break
        if x < right + GAP and x + width > left - GAP:
            x = right + GAP
    return x


def layout_geometry(geometry: list) -> GeometryLayout:
    if not isinstance(geometry, list):
        raise ValueError("geometry must be an array")

    keys = []
    group_x = 0.0
    keyboard_height = 0.0

    for group_index, group in enumerate(geometry):
        if not isinstance(group, dict):
            raise ValueError(f"group {group_index + 1} must be an object")

        rows = group.get("elements")
        if not isinstance(rows, list):
            raise ValueError(f"rows missing in group {group_index + 1}")
        group_gap = _number(group.get("gap", 0), "invalid group gap")
        if group_gap < 0:
            raise ValueError("invalid group gap")

        occupied_intervals: dict[int, list[tuple[float, float]]] = {}
        occupied_columns: dict[int, set[int]] = {}
        group_width = 0.0
        group_height = max(0.0, len(rows) * (KEY_HEIGHT + GAP) - GAP)

        for row_index, row in enumerate(rows):
            if not isinstance(row, list):
                raise ValueError(
                    f"row {group_index + 1}:{row_index + 1} must be an array"
                )

            x = group_x
            y = row_index * (KEY_HEIGHT + GAP)
            column = 0

            for item_index, item in enumerate(row):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"element {group_index + 1}:{row_index + 1}:"
                        f"{item_index + 1} must be an object"
                    )

                item_type = item.get("type", "key")
                if item_type not in ("key", "space"):
                    raise ValueError("invalid element type")

                size = _positive_number(item.get("size"), "invalid size")
                quantity = item.get("quantity", 1)
                if (
                    isinstance(quantity, bool)
                    or not isinstance(quantity, int)
                    or quantity < 1
                ):
                    raise ValueError("invalid quantity")

                rowspan = _span(item, "rowspan")
                colspan = _span(item, "colspan")
                parts = _parts(item)

                if parts:
                    width = max(part.width for part in parts)
                    height = sum(part.height for part in parts)
                    occupied_colspan = 1
                else:
                    width = size * colspan + GAP * (colspan - 1)
                    height = KEY_HEIGHT * rowspan + GAP * (rowspan - 1)
                    occupied_colspan = colspan

                for quantity_index in range(quantity):
                    row_columns = occupied_columns.setdefault(row_index, set())
                    while column in row_columns:
                        column += 1

                    x = _next_free_x(
                        x,
                        width,
                        occupied_intervals.setdefault(row_index, []),
                    )
                    actual_ref = str(item.get(
                        "ref",
                        f"g{group_index + 1}-r{row_index + 1}-c{column + 1}",
                    ))
                    if "ref" not in item and quantity > 1:
                        actual_ref += f"-n{quantity_index + 1}"

                    keys.append(KeyLayout(
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        ref=actual_ref,
                        name=str(item.get("name", "")),
                        parts=parts,
                        type=item_type,
                    ))

                    right = x + width
                    for covered_row in range(row_index, row_index + rowspan):
                        occupied_intervals.setdefault(covered_row, []).append((x, right))
                        occupied_columns.setdefault(covered_row, set()).update(
                            range(column, column + occupied_colspan)
                        )

                    x = right + GAP
                    column += occupied_colspan
                    group_width = max(group_width, right - group_x)
                    group_height = max(group_height, y + height)

        group_x += group_width
        if group_index < len(geometry) - 1:
            group_x += group_gap
        keyboard_height = max(keyboard_height, group_height)

    return GeometryLayout(group_x, keyboard_height, tuple(keys))
