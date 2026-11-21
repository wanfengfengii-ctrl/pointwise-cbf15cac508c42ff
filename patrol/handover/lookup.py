from patrol import AuditError, InvalidInput, MediumUnavailable, NotFound
from patrol.archive.objects import read_whole_object
from patrol.archive.receipts import ReceiptRegistry
from patrol.archive.takeover import TakeoverRegistry, recover_takeover_frame
from patrol.archive.transactions import ArchiveStore
from patrol.handover.audit import AuditResult, verify_archived_frame, verify_takeover_frame
from patrol.handover.index import ShiftIndex


def lookup_run_receipt(store: ArchiveStore, run_id: str) -> str:
    manifest = ShiftIndex(store).by_run(run_id)
    if manifest is None:
        raise NotFound(f"no shift manifest for run {run_id!r}")
    return manifest.receipt_id


def lookup_receipt(store: ArchiveStore, receipt_id: str) -> AuditResult:
    if isinstance(receipt_id, str) and receipt_id.startswith("takeover-"):
        order = TakeoverRegistry(store).get_committed_order(receipt_id)
        if order is None:
            raise NotFound(f"unknown receipt {receipt_id!r}")
        try:
            with store.read_transaction() as db:
                frame = recover_takeover_frame(store, db, order)
        except (InvalidInput, MediumUnavailable, NotFound) as exc:
            raise AuditError(f"original fragments for receipt {receipt_id!r} are unreadable") from exc
        return verify_takeover_frame(order, frame)
    receipt = ReceiptRegistry(store).get_committed(receipt_id)
    if receipt is None:
        raise NotFound(f"unknown receipt {receipt_id!r}")
    try:
        with store.read_transaction() as db:
            archived = read_whole_object(store, db, receipt.object_id, receipt.origin, receipt.settings)
    except (InvalidInput, MediumUnavailable, NotFound) as exc:
        raise AuditError(f"original object for receipt {receipt_id!r} is unreadable") from exc
    return verify_archived_frame(receipt, archived)
