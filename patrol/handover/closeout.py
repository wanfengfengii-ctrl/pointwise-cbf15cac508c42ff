from patrol import AuditError, CloseoutDenied, require_text
from patrol.archive.receipts import ReceiptRegistry
from patrol.archive.transactions import ArchiveStore
from patrol.handover.index import ShiftIndex, ShiftManifest


def close_run(store: ArchiveStore, run_id: str, receipt_id: str) -> ShiftManifest:
    require_text(run_id, "run identifier")
    try:
        receipt = ReceiptRegistry(store).get_committed(receipt_id)
    except AuditError as exc:
        raise CloseoutDenied("receipt has invalid archive evidence") from exc
    if receipt is None:
        raise CloseoutDenied("closeout requires a committed ordinary receipt")
    if receipt.origin.run_id != run_id:
        raise CloseoutDenied("receipt refers to a different inspection run")
    return ShiftIndex(store).fix(run_id, receipt.receipt_id)
