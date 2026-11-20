from patrol import AuditError, InvalidInput, MediumUnavailable, NotFound
from patrol.archive.objects import read_whole_object
from patrol.archive.receipts import ReceiptRegistry
from patrol.archive.transactions import ArchiveStore
from patrol.handover.audit import AuditResult, verify_archived_frame
from patrol.handover.index import ShiftIndex


def lookup_run_receipt(store: ArchiveStore, run_id: str) -> str:
    manifest = ShiftIndex(store).by_run(run_id)
    if manifest is None:
        raise NotFound(f"no shift manifest for run {run_id!r}")
    return manifest.receipt_id


def lookup_receipt(store: ArchiveStore, receipt_id: str) -> AuditResult:
    receipt = ReceiptRegistry(store).get_committed(receipt_id)
    if receipt is None:
        raise NotFound(f"unknown receipt {receipt_id!r}")
    try:
        with store.read_transaction() as db:
            archived = read_whole_object(store, db, receipt.object_id, receipt.origin, receipt.settings)
    except (InvalidInput, MediumUnavailable, NotFound) as exc:
        raise AuditError(f"original object for receipt {receipt_id!r} is unreadable") from exc
    return verify_archived_frame(receipt, archived)
