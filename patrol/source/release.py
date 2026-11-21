from patrol import AuditError, NotFound, ReleaseDenied, StateError, require_text
from patrol.archive.codec import frame_digest
from patrol.archive.receipts import ReceiptRegistry
from patrol.archive.takeover import TakeoverRegistry, takeover_frame_digest
from patrol.archive.transactions import ArchiveStore
from patrol.source.freeze import SourceFrame
from patrol.source.slots import SlotBank


def begin_takeover(slots: SlotBank, frame_key: str, order_id: str) -> SourceFrame:
    """Register a capacity-limited takeover order for the current frozen frame."""
    order_id = require_text(order_id, "takeover order")
    frame_key = require_text(frame_key, "frame key")
    return slots.register_takeover(frame_key, order_id)


def release_frame(slots: SlotBank, store: ArchiveStore, receipt_id: str) -> None:
    if isinstance(receipt_id, str) and receipt_id.startswith("takeover-"):
        _release_takeover_frame(slots, store, receipt_id)
        return
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
        if slots.current_takeover_order() is not None:
            raise ReleaseDenied("the current frame awaits its committed takeover receipt")
        if current.origin != receipt.origin or frame_digest(current) != receipt.digest:
            raise ReleaseDenied("the committed receipt belongs to different frame evidence")
        slots.clear_current(current.frame_key)


def _release_takeover_frame(slots: SlotBank, store: ArchiveStore, receipt_id: str) -> None:
    try:
        order = TakeoverRegistry(store).get_committed_order(receipt_id)
    except AuditError as exc:
        raise ReleaseDenied("the takeover receipt binding is invalid") from exc
    if order is None:
        raise ReleaseDenied("the takeover receipt is not committed")
    with slots.lock:
        try:
            current = slots.read_current()
        except (NotFound, StateError) as exc:
            raise ReleaseDenied("there is no current frozen frame to release") from exc
        if slots.current_takeover_order() != order.order_id:
            raise ReleaseDenied("the committed takeover receipt belongs to a different order")
        if current.origin != order.origin or takeover_frame_digest(current) != order.digest:
            raise ReleaseDenied("the committed takeover receipt belongs to different frame evidence")
        slots.clear_current(current.frame_key)
