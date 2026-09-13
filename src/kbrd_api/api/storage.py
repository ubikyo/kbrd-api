import os

from flask import Flask, jsonify


class Storage:
    """How full the device's own filesystems are.

    One reading per partition the image lays down (see
    `kbrd-os/board/kbrd/genimage.cfg.in`): the boot partition, the system
    partition the firmware and this service run from, and `/data`, which
    holds the database, the media library and the fonts (see `Config`).

    A mount point that isn't there is left out rather than reported as
    empty, and so is one that turns out to be part of a filesystem already
    listed — on a development machine `/boot` and `/data` are usually not
    filesystems of their own, and answering for `/` three times over would
    only say the same thing three times.
    """

    # In the order the partitions sit on the card, which is the order
    # KBRD-WEB's Storage tab shows them in.
    PARTITIONS = (
        ("Boot", "/boot"),
        ("System", "/"),
        ("Data", "/data"),
    )

    def __init__(self, partitions=None):
        self._partitions = (
            self.PARTITIONS if partitions is None else tuple(partitions)
        )

    def register(self, app: Flask) -> None:
        @app.get("/api/storage")
        def storage():
            return jsonify(partitions=self.partitions())

    def partitions(self) -> list[dict]:
        seen = set()
        found = []
        for name, mount in self._partitions:
            try:
                device = os.stat(mount).st_dev
                stats = os.statvfs(mount)
            except OSError:
                continue
            if device in seen:
                continue
            seen.add(device)
            total = stats.f_blocks * stats.f_frsize
            if total <= 0:
                continue
            # `f_bavail` rather than `f_bfree`: ext4 keeps a reserved
            # slice for root that nothing here can write into, so free is
            # what is actually left to a media upload. Used is measured
            # against the whole filesystem, which is why the two plus that
            # reserve — not each other — add up to the total.
            free = stats.f_bavail * stats.f_frsize
            found.append(
                {
                    "name": name,
                    "mount": mount,
                    "total": total,
                    "used": (stats.f_blocks - stats.f_bfree) * stats.f_frsize,
                    "free": free,
                }
            )
        return found
