from dataclasses import dataclass

from patrol import AuditError
from patrol.archive.objects import WholeObjectRead
from patrol.archive.receipts import WholeReceipt
from patrol.archive.takeover import TakeoverOrder
from patrol.quality.results import PeakResult, evaluate_frame
from patrol.source.freeze import SourceFrame


@dataclass(frozen=True, slots=True)
class AuditResult:
    receipt_id: str
    frame: SourceFrame
    result: PeakResult


def verify_archived_frame(receipt: WholeReceipt, archived: WholeObjectRead) -> AuditResult:
    obj, frame = archived.descriptor, archived.frame
    if obj.object_id != receipt.object_id or obj.volume_id != receipt.volume_id or obj.digest != receipt.digest:
        raise AuditError("verified object differs from the ordinary receipt's binding")
    if frame.origin != receipt.origin or frame.settings != receipt.settings:
        raise AuditError("read frame differs from the receipt's frozen context")
    calculated = evaluate_frame(frame)
    if calculated != receipt.result:
        raise AuditError("recalculated peak result differs from the fixed archive result")
    return AuditResult(receipt.receipt_id, frame, calculated)


def verify_takeover_frame(order: TakeoverOrder, frame: SourceFrame) -> AuditResult:
    if frame.origin != order.origin or frame.settings != order.settings:
        raise AuditError("recovered frame differs from the takeover order's frozen context")
    calculated = evaluate_frame(frame)
    if calculated != order.result:
        raise AuditError("recalculated peak result differs from the fixed takeover result")
    return AuditResult(order.receipt_id, frame, calculated)
