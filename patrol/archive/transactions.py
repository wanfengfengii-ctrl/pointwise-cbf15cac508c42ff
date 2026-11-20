from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from patrol import CommitError, InvalidInput, NotFound, StorageError

if TYPE_CHECKING:
    from patrol.archive.volumes import VolumeCatalog, VolumeSpec


class ArchiveStore:
    """Connection factory for a station with separate metadata and data areas."""

    def __init__(
        self,
        root: Path,
        offline: frozenset[str],
        before_media_write: Callable[[str, str], None] | None,
        before_archive_commit: Callable[[], None] | None,
    ) -> None:
        self.root = root
        self.database = root / "station.sqlite3"
        self.offline = offline
        self.before_media_write = before_media_write
        self.before_archive_commit = before_archive_commit
        self.volumes: VolumeCatalog

    @classmethod
    def open(
        cls,
        root: str | Path,
        specs: Iterable[VolumeSpec] | None = None,
        *,
        offline: Iterable[str] = (),
        before_media_write: Callable[[str, str], None] | None = None,
        before_archive_commit: Callable[[], None] | None = None,
    ) -> ArchiveStore:
        from patrol.archive.objects import initialize_objects, prune_uncommitted_objects
        from patrol.archive.receipts import ReceiptRegistry
        from patrol.archive.volumes import VolumeCatalog
        from patrol.handover.index import ShiftIndex

        station = cls(Path(root).resolve(), frozenset(offline), before_media_write, before_archive_commit)
        if specs is None and not station.database.is_file():
            raise NotFound("the station has not been initialized")
        try:
            station.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError("could not create the station metadata area") from exc
        station.volumes = VolumeCatalog(station)
        with station.write_transaction() as db:
            configured = station.volumes.initialize(db, specs)
            unknown = station.offline.difference(spec.name for spec in configured)
            if unknown:
                raise InvalidInput("offline selection contains an unknown medium")
            initialize_objects(db)
            ReceiptRegistry.initialize(db)
            ShiftIndex.initialize(db)
        prune_uncommitted_objects(station)
        return station

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def read_transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(self._connect()) as db, db:
                db.execute("PRAGMA query_only = ON")
                db.execute("BEGIN")
                yield db
        except sqlite3.Error as exc:
            raise StorageError("could not read station metadata") from exc

    @contextmanager
    def write_transaction(
        self, *, before_commit: Callable[[], None] | None = None,
    ) -> Iterator[sqlite3.Connection]:
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                yield db
                if before_commit is not None:
                    try:
                        before_commit()
                    except OSError as exc:
                        raise CommitError("archive commit was declined") from exc
                try:
                    db.commit()
                except sqlite3.Error as exc:
                    raise CommitError("could not commit station metadata") from exc
        except sqlite3.Error as exc:
            raise StorageError("could not write station metadata") from exc
