from patrol import AuditError, NotFound, ReleaseDenied, StateError
from patrol.archive.codec import frame_digest
from patrol.archive.receipts import ReceiptRegistry
from patrol.archive.transactions import ArchiveStore
from patrol.source.slots import SlotBank


def release_frame(slots: SlotBank, store: ArchiveStore, receipt_id: str) -> None:
    if not isinstance(receipt_id, str) or not receipt_id.startswith("whole-"):
        raise ReleaseDenied("an ordinary whole-frame receipt number is required")
    try:
        receipt = ReceiptRegistry(store).get_committed(receipt_id)
    except AuditError as exc:
        raise ReleaseDenied("the ordinary receipt binding is invalid") from exc
    if receipt is None:
        raise ReleaseDenied("the ordinary receipt is not committed")
    with slots.lock:
        try:
            current = slots.read_current()
        except (NotFound, StateError) as exc:
            raise ReleaseDenied("there is no current frozen frame to release") from exc
        if current.origin != receipt.origin or frame_digest(current) != receipt.digest:
            raise ReleaseDenied("the committed receipt belongs to different frame evidence")
        slots.clear_current(current.frame_key)
