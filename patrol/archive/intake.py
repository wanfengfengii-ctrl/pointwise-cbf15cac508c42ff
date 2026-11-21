from patrol import CapacityError, Conflict, MediaWriteError, MediumUnavailable, StorageError, require_text
from patrol.archive.codec import encode_whole_frame, frame_digest
from patrol.archive.objects import (
    WholeObject,
    discard_uncommitted_object,
    register_whole_object,
    stage_whole_object,
)
from patrol.archive.receipts import ReceiptRegistry, WholeReceipt
from patrol.archive.takeover import (
    TakeoverReceipt,
    TakeoverRegistry,
    plan_takeover,
    stage_takeover_fragments,
    takeover_frame_digest,
    takeover_receipt,
    verify_takeover_recovery,
)
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


def archive_takeover(store: ArchiveStore, order_id: str, frame: SourceFrame) -> TakeoverReceipt:
    """Accept a registered frame for capacity-limited takeover across the three media."""
    order_id = require_text(order_id, "takeover order")
    payload = encode_whole_frame(frame)
    digest = takeover_frame_digest(frame, payload)
    result = evaluate_frame(frame)
    registry = TakeoverRegistry(store)

    # Acceptance fixes the order binding, the frame evidence and the fragment plan.
    with store.write_transaction() as db:
        order = registry.order_by_id(order_id, db=db)
        if order is not None:
            if order.frame_key != frame.frame_key or order.digest != digest:
                raise Conflict("takeover order is already bound to different frame evidence")
            if order.committed:
                return takeover_receipt(order)
        else:
            if registry.order_by_frame(frame.frame_key, db=db) is not None:
                raise Conflict("the frame is already registered under a different takeover order")
            split, plan = plan_takeover(store, db, frame, payload, digest)
            order = registry.record_order(db, order_id, frame, digest, result, split, plan)

    # Fragments are billed only once their shares actually persist on their media.
    stage_takeover_fragments(store, order, payload)
    # The receipt is signed only after persisted shares prove any one medium can be lost.
    verify_takeover_recovery(store, order)

    def before_commit() -> None:
        if store.before_archive_commit is not None:
            store.before_archive_commit()

    with store.write_transaction(before_commit=before_commit) as db:
        registry.mark_committed(db, order.order_id)
    return takeover_receipt(order)
