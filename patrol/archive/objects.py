from dataclasses import dataclass
from sqlite3 import Connection
from uuid import uuid4

from patrol import AuditError, MediaWriteError, MediumUnavailable
from patrol.archive.codec import decode_whole_frame, frame_digest
from patrol.archive.media import LocalMedium
from patrol.archive.transactions import ArchiveStore
from patrol.runs.settings import SamplingSettings
from patrol.source.freeze import SourceFrame
from patrol.source.origin import FrameOrigin


@dataclass(frozen=True, slots=True)
class WholeObject:
    object_id: str
    volume_id: str
    word_count: int
    word_width: int
    digest: str

    @classmethod
    def for_frame(cls, volume_id: str, frame: SourceFrame, digest: str) -> "WholeObject":
        return cls(uuid4().hex, volume_id, len(frame.words), frame.settings.word_width, digest)


@dataclass(frozen=True, slots=True)
class WholeObjectRead:
    descriptor: WholeObject
    frame: SourceFrame


def initialize_objects(db: Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS whole_objects (
            object_id TEXT PRIMARY KEY,
            volume_id TEXT NOT NULL REFERENCES volumes(name),
            word_count INTEGER NOT NULL CHECK (word_count > 0),
            word_width INTEGER NOT NULL CHECK (word_width > 0),
            digest TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS whole_objects_volume ON whole_objects(volume_id)")


def stage_whole_object(medium: LocalMedium, candidate: WholeObject, payload: bytes) -> None:
    if medium.name != candidate.volume_id or len(payload) != candidate.word_count * candidate.word_width:
        raise MediaWriteError("whole-object candidate differs from its media assignment or word count")
    medium.stage_object(candidate.object_id, payload)
    medium.publish_object(candidate.object_id)
    if medium.read_object(candidate.object_id) != payload:
        raise MediaWriteError("published object did not read back as the original frame")


def register_whole_object(db: Connection, candidate: WholeObject) -> None:
    db.execute("""
        INSERT INTO whole_objects(object_id, volume_id, word_count, word_width, digest)
        VALUES (?, ?, ?, ?, ?)
    """, (
        candidate.object_id, candidate.volume_id, candidate.word_count,
        candidate.word_width, candidate.digest,
    ))


def read_whole_object(
    store: ArchiveStore,
    db: Connection,
    object_id: str,
    origin: FrameOrigin,
    settings: SamplingSettings,
) -> WholeObjectRead:
    row = db.execute("""
        SELECT object_id, volume_id, word_count, word_width, digest
        FROM whole_objects WHERE object_id = ?
    """, (object_id,)).fetchone()
    if row is None:
        raise AuditError("receipt references a missing whole object")
    descriptor = WholeObject(**dict(row))
    medium = store.volumes.medium(descriptor.volume_id, db=db)
    payload = medium.read_object(descriptor.object_id)
    frame = decode_whole_frame(origin, settings, descriptor.word_count, descriptor.word_width, payload)
    if frame_digest(frame, payload) != descriptor.digest:
        raise AuditError("archived frame digest differs from the fixed whole object")
    return WholeObjectRead(descriptor, frame)


def discard_uncommitted_object(store: ArchiveStore, candidate: WholeObject) -> None:
    with store.write_transaction() as db:
        referenced = db.execute(
            "SELECT 1 FROM whole_objects WHERE object_id = ?", (candidate.object_id,),
        ).fetchone()
        if referenced is None:
            medium = store.volumes.medium(candidate.volume_id, db=db)
            medium.discard_candidate(candidate.object_id)


def prune_uncommitted_objects(store: ArchiveStore) -> None:
    """Recover leftovers while excluding concurrent whole-object writers."""
    with store.write_transaction() as db:
        for spec in store.volumes.specifications(db=db):
            medium = store.volumes.medium(spec.name, db=db)
            try:
                candidates = medium.candidate_ids()
            except MediumUnavailable:
                continue
            referenced = {
                row[0] for row in db.execute(
                    "SELECT object_id FROM whole_objects WHERE volume_id = ?", (spec.name,),
                )
            }
            referenced.update(
                row[0] for row in db.execute(
                    "SELECT object_id FROM takeover_fragments WHERE volume_id = ?", (spec.name,),
                )
            )
            for object_id in sorted(candidates - referenced):
                try:
                    medium.discard_candidate(object_id)
                except (MediumUnavailable, MediaWriteError):
                    # A later reopen can clean this candidate when the medium is writable.
                    continue
