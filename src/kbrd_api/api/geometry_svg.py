from html import escape

MM_TO_PX = 96 / 25.4

KEY_HEIGHT_MM = 16
GAP_MM = 3

BORDER_RADIUS = 2


def generate_geometry_svg(
    geometry: list,
    unit: str,
) -> str:
    if unit not in ("mm", "px"):
        raise ValueError(
            "unit must be 'mm' or 'px'"
        )

    if not isinstance(geometry, list):
        raise ValueError(
            "geometry must be an array"
        )

    scale = MM_TO_PX if unit == "mm" else 1.0

    gap = GAP_MM * scale
    key_height = KEY_HEIGHT_MM * scale

    #
    # occupation[row] = ensemble des colonnes déjà
    # occupées par un rowspan provenant d'une ligne
    # précédente.
    #
    occupied: dict[int, set[int]] = {}

    keys: list[dict] = []

    max_right = 0.0
    max_bottom = 0.0

    for row_index, row in enumerate(geometry):
        if not isinstance(row, list):
            raise ValueError(
                f"row {row_index} must be an array"
            )

        occupied.setdefault(
            row_index,
            set(),
        )

        column = 0
        x = 0.0

        for item_index, item in enumerate(row):
            if not isinstance(item, dict):
                raise ValueError(
                    f"item {row_index}:{item_index} must be an object"
                )

            size = float(
                item.get("size", 0)
            )

            quantity = int(
                item.get("quantity", 1)
            )

            rowspan = int(
                item.get("rowspan", 0)
            )

            colspan = int(
                item.get("colspan", 0)
            )

            if size <= 0:
                raise ValueError(
                    f"invalid size at {row_index}:{item_index}"
                )

            if quantity < 1:
                raise ValueError(
                    f"invalid quantity at {row_index}:{item_index}"
                )

            #
            # 0 et 1 signifient une seule cellule.
            # À partir de 2 : véritable span HTML.
            #
            row_span = max(1, rowspan)
            col_span = max(1, colspan)

            base_width = size * scale

            for quantity_index in range(quantity):

                #
                # Trouver la première colonne libre.
                #
                while (
                    column
                    in occupied[row_index]
                ):
                    column += 1

                #
                # Calcul de x.
                #
                # On ne peut pas utiliser simplement
                # column * largeur car les touches ont
                # des tailles différentes.
                #
                # x correspond donc à la fin de la
                # dernière touche réellement placée.
                #

                width = (
                    base_width * col_span
                    + gap * (col_span - 1)
                )

                height = (
                    key_height * row_span
                    + gap * (row_span - 1)
                )

                y = row_index * (
                    key_height + gap
                )

                key_ref = (
                    f"r{row_index + 1}"
                    f"-c{column + 1}"
                )

                keys.append(
                    {
                        "x": x,
                        "y": y,
                        "width": width,
                        "height": height,
                        "ref": key_ref,
                        "name": str(
                            item.get(
                                "name",
                                "",
                            )
                        ),
                    }
                )

                #
                # Marquer toutes les cellules couvertes.
                #
                for rr in range(
                    row_index,
                    row_index + row_span,
                ):
                    occupied.setdefault(
                        rr,
                        set(),
                    )

                    for cc in range(
                        column,
                        column + col_span,
                    ):
                        occupied[rr].add(
                            cc
                        )

                right = x + width
                bottom = y + height

                max_right = max(
                    max_right,
                    right,
                )

                max_bottom = max(
                    max_bottom,
                    bottom,
                )

                x = right + gap
                column += col_span

    if not keys:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 1 1"></svg>'
        )

    elements = []

    for key in keys:
        elements.append(
            (
                '<rect '
                'class="kbrd-key" '
                f'data-key="{escape(key["ref"])}" '
                f'data-name="{escape(key["name"])}" '
                f'x="{key["x"]:.3f}" '
                f'y="{key["y"]:.3f}" '
                f'width="{key["width"]:.3f}" '
                f'height="{key["height"]:.3f}" '
                f'rx="{BORDER_RADIUS}" '
                f'ry="{BORDER_RADIUS}" '
                'fill="none" '
                'stroke="rgba(255,255,255,0.5)" '
                'stroke-width="1" '
                '/>'
            )
        )

    return (
        '<svg '
        'xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {max_right:.3f} {max_bottom:.3f}" '
        'preserveAspectRatio="xMidYMid meet">'
        + "".join(elements)
        + "</svg>"
    )