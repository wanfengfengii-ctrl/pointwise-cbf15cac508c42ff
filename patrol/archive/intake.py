from patrol import CapacityError, Conflict, MediaWriteError, MediumUnavailable, StorageError
from patrol.archive.codec import encode_whole_frame, frame_digest
from patrol.archive.objects import (
    WholeObject,
    discard_uncommitted_object,
    register_whole_object,
    stage_whole_object,
)
from patrol.archive.receipts import ReceiptRegistry, WholeReceipt
from patrol.archive.transactions import ArchiveStore
from patrol.quality.results import evaluate_frame
from patrol.source.freeze import SourceFrame


def archive_frame(store: ArchiveStore, frame: SourceFrame, volume_id: str) -> WholeReceipt:
    payload = encode_whole_frame(frame)
    digest = frame_digest(frame, payload)
    receipts = ReceiptRegistry(store)
    candidate: WholeObject | None = None

    def before_commit() -> None:
        if candidate is not None and store.before_archive_commit is not None:
            store.before_archive_commit()

    try:
        with store.write_transaction(before_commit=before_commit) as db:
            previous = receipts.by_frame(frame.frame_key, db=db)
            if previous is not None:
                if previous.digest != digest:
                    raise Conflict("frame identity already has different archived evidence")
                return previous

            medium = store.volumes.medium(volume_id, db=db)
            medium.require_online()
            available = store.volumes.available_words(volume_id, db=db)
            if available < len(frame.words):
                raise CapacityError(
                    f"medium {volume_id!r} has {available} free words for a {len(frame.words)}-word frame",
                )
            result = evaluate_frame(frame)
            candidate = WholeObject.for_frame(volume_id, frame, digest)
            stage_whole_object(medium, candidate, payload)
            register_whole_object(db, candidate)
            return receipts.record_first(db, candidate, frame.origin, frame.settings, result)
    except BaseException:
        if candidate is not None:
            try:
                discard_uncommitted_object(store, candidate)
            except (StorageError, MediumUnavailable, MediaWriteError):
                # Leave a candidate for reopen cleanup when metadata or media is unavailable.
                pass
        raise
