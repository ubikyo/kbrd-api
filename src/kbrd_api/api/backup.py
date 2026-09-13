import os
import shutil
import sqlite3
import tempfile
import time
import zipfile

from flask import Flask, jsonify, request, send_file

from kbrd_api.db import DB

# What every SQLite file starts with, checked on the database read out of
# an archive before anything is opened.
SQLITE_MAGIC = b"SQLite format 3\x00"

# What a backup archive holds: the database under this name, and every
# media file under this folder. Nothing else is written, and nothing else
# is read back — an entry under any other name is ignored on restore,
# which is also what keeps a crafted archive from writing outside the two
# places a restore is allowed to touch.
DATABASE_ENTRY = "database.db"
MEDIA_PREFIX = "media/"

# The tables a KBRD database is expected to carry, each with the names it
# has gone under: a backup old enough to still call them `geometry` and
# `workspace` is one `DB.init_schema` knows how to bring forward, so it is
# a KBRD backup like any other. A valid SQLite file from somewhere else
# would otherwise be restored happily and leave the device with a database
# that has nothing in it the app can read.
REQUIRED_TABLES = (
    ("layout", "geometry"),
    ("layer", "workspace"),
)


class Backup:
    """The whole device, out as one archive and back in as one archive.

    A backup holds the database — every layout, layer, key and plugin
    config — and the media library's own files, which live outside it (see
    `Layer`'s `media_dir`) and are what those plugin configs draw with.
    Restoring puts both back, so a key that was given an image finds that
    image there.
    """

    def __init__(self, db: DB, media_dir: str):
        self.db = db
        self.media_dir = media_dir

    def register(self, app: Flask) -> None:
        @app.get("/api/backup")
        def download_backup():
            path = self._temporary(".zip")
            try:
                self._write_archive(path)
                # Opened, then unlinked straight away: the archive stays
                # readable through this handle for as long as it takes to
                # send, and there is nothing left behind if the transfer
                # is abandoned half way. Flask closes the handle once the
                # response is done with it.
                handle = open(path, "rb")
            except (OSError, sqlite3.Error) as exc:
                self._discard(path)
                app.logger.exception("Unable to build the backup archive")
                return jsonify(error=f"backup failed: {exc}"), 500
            self._discard(path)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            return send_file(
                handle,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"kbrd-backup-{stamp}.zip",
            )

        @app.post("/api/backup/restore")
        def restore_backup():
            uploaded = request.files.get("file")
            if uploaded is None or not uploaded.filename:
                return jsonify(error="missing file"), 400

            archive = self._temporary(".zip")
            # The database and the media folder are both staged beside the
            # ones they are about to become, so each move below stays on
            # one filesystem and is therefore atomic.
            staged_db = f"{self.db.db_path}.restoring"
            staged_media = f"{self.media_dir}.restoring"
            replaced_media = f"{self.media_dir}.replaced"
            staged = False
            try:
                uploaded.save(archive)
                self._stage(archive, staged_db, staged_media)
                staged = True
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            except (OSError, sqlite3.Error) as exc:
                app.logger.exception("Unable to read the uploaded backup")
                return jsonify(error=f"restore failed: {exc}"), 500
            finally:
                self._discard(archive)
                # An archive refused half way through leaves both of these
                # behind — a restore that never happened takes its own
                # staging with it.
                if not staged:
                    self._discard(staged_db)
                    self._discard_tree(staged_media)

            try:
                os.replace(staged_db, self.db.db_path)
                # A journal left by the database being replaced belongs to
                # the file that just went away — kept, SQLite would try to
                # recover the new one with it.
                for suffix in ("-wal", "-shm", "-journal"):
                    self._discard(f"{self.db.db_path}{suffix}")
                # `os.replace` won't drop a folder that has anything in
                # it, so the one on its way out steps aside first and is
                # deleted once the new one is in its place.
                self._discard_tree(replaced_media)
                if os.path.exists(self.media_dir):
                    os.replace(self.media_dir, replaced_media)
                os.replace(staged_media, self.media_dir)
                self._discard_tree(replaced_media)
                # A backup taken by an older KBRD carries an older schema;
                # the same migrations startup runs bring it up to date
                # (see `DB.init_schema`).
                self.db.init_schema()
            except (OSError, sqlite3.Error) as exc:
                app.logger.exception("Unable to restore the uploaded backup")
                return jsonify(error=f"restore failed: {exc}"), 500

            return jsonify(ok=True)

    def _write_archive(self, path: str) -> None:
        snapshot = self._temporary(".db")
        try:
            # Read through SQLite rather than off the disk: a plain file
            # copy taken mid-write would carry a torn page, while this
            # writes out a consistent image of every table.
            with self.db.connect() as source:
                destination = sqlite3.connect(snapshot)
                try:
                    source.backup(destination)
                finally:
                    destination.close()
            with zipfile.ZipFile(path, "w") as archive:
                archive.write(
                    snapshot, DATABASE_ENTRY, compress_type=zipfile.ZIP_DEFLATED
                )
                # Stored rather than deflated: images and videos are
                # compressed already, so all squeezing them again buys is
                # a slower backup on a device that has no CPU to spare.
                for name in self._media_files():
                    archive.write(
                        os.path.join(self.media_dir, name),
                        f"{MEDIA_PREFIX}{name}",
                        compress_type=zipfile.ZIP_STORED,
                    )
        finally:
            self._discard(snapshot)

    def _media_files(self) -> list[str]:
        try:
            entries = sorted(os.listdir(self.media_dir))
        except FileNotFoundError:
            return []
        return [
            name
            for name in entries
            if os.path.isfile(os.path.join(self.media_dir, name))
        ]

    def _stage(self, archive: str, staged_db: str, staged_media: str) -> None:
        """Unpacks the uploaded archive next to what it is replacing and
        opens the database in it, so an archive that isn't a KBRD backup —
        or is one that can't be read — is turned away while the real
        database and media library are still in place."""
        self._discard(staged_db)
        self._discard_tree(staged_media)
        if not zipfile.is_zipfile(archive):
            raise ValueError("not a KBRD backup")

        with zipfile.ZipFile(archive) as opened:
            names = set(opened.namelist())
            if DATABASE_ENTRY not in names:
                raise ValueError("not a KBRD backup")
            self._makedirs(os.path.dirname(staged_db))
            with opened.open(DATABASE_ENTRY) as source:
                with open(staged_db, "wb") as target:
                    shutil.copyfileobj(source, target)

            os.makedirs(staged_media, exist_ok=True)
            for name in names:
                # Only a plain file directly under `media/` — an entry
                # naming a folder, a path of its own, or anywhere outside
                # it is not something a backup ever writes, so it is not
                # something a restore unpacks.
                if not name.startswith(MEDIA_PREFIX):
                    continue
                filename = name[len(MEDIA_PREFIX) :]
                if not filename or filename != os.path.basename(filename):
                    continue
                with opened.open(name) as source:
                    with open(os.path.join(staged_media, filename), "wb") as target:
                        shutil.copyfileobj(source, target)

        self._check(staged_db)

    def _check(self, path: str) -> None:
        with open(path, "rb") as handle:
            if handle.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
                raise ValueError("not a KBRD backup")

        conn = sqlite3.connect(path)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("this backup is damaged")
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"this backup cannot be read: {exc}") from exc
        finally:
            conn.close()

        if any(tables.isdisjoint(names) for names in REQUIRED_TABLES):
            raise ValueError("not a KBRD backup")

    @staticmethod
    def _temporary(suffix: str) -> str:
        handle, path = tempfile.mkstemp(suffix=suffix)
        os.close(handle)
        return path

    @staticmethod
    def _makedirs(directory: str) -> None:
        if directory:
            os.makedirs(directory, exist_ok=True)

    @staticmethod
    def _discard(path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _discard_tree(path: str) -> None:
        shutil.rmtree(path, ignore_errors=True)
