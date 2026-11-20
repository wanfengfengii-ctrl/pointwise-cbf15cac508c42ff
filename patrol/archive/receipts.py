import re
from dataclasses import dataclass
from sqlite3 import Connection, Row

from patrol import AuditError, InvalidInput, require_text
from patrol.archive.codec import frame_context, load_frame_context
from patrol.archive.objects import WholeObject
from patrol.archive.transactions import ArchiveStore
from patrol.quality.results import PeakResult
from patrol.runs.settings import SamplingSettings
from patrol.source.origin import FrameOrigin

_RECEIPT_ID = re.compile(r"whole-[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class WholeReceipt:
    receipt_id: str
    object_id: str
    volume_id: str
    origin: FrameOrigin
    settings: SamplingSettings
    digest: str
    result: PeakResult

    @property
    def frame_key(self) -> str:
        return self.origin.frame_key


class ReceiptRegistry:
    def __init__(self, store: ArchiveStore) -> None:
        self._store = store

    @staticmethod
    def initialize(db: Connection) -> None:
        db.execute("""
            CREATE TABLE IF NOT EXISTS whole_receipts (
                receipt_id TEXT PRIMARY KEY,
                frame_key TEXT NOT NULL UNIQUE,
                object_id TEXT NOT NULL UNIQUE REFERENCES whole_objects(object_id),
                volume_id TEXT NOT NULL REFERENCES volumes(name),
                context TEXT NOT NULL,
                digest TEXT NOT NULL,
                peak_value TEXT NOT NULL,
                peak_position INTEGER NOT NULL CHECK (peak_position > 0),
                difference TEXT NOT NULL,
                exceeded INTEGER NOT NULL CHECK (exceeded IN (0, 1))
            )
        """)

    def record_first(
        self,
        db: Connection,
        obj: WholeObject,
        origin: FrameOrigin,
        settings: SamplingSettings,
        result: PeakResult,
    ) -> WholeReceipt:
        if result.frame_key != origin.frame_key or result.unit != settings.unit or result.limit != settings.peak_limit:
            raise InvalidInput("peak result does not refer to the source frame's frozen context")
        receipt = WholeReceipt(
            f"whole-{obj.object_id}", obj.object_id, obj.volume_id, origin,
            settings, obj.digest, result,
        )
        db.execute("""
            INSERT INTO whole_receipts(
                receipt_id, frame_key, object_id, volume_id, context, digest,
                peak_value, peak_position, difference, exceeded
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            receipt.receipt_id, receipt.frame_key, receipt.object_id, receipt.volume_id,
            frame_context(origin, settings), receipt.digest, hex(result.value),
            result.position, hex(result.difference), int(result.exceeded),
        ))
        return receipt

    @staticmethod
    def _decode(row: Row | None) -> WholeReceipt | None:
        if row is None:
            return None
        origin, settings = load_frame_context(row["context"])
        if row["frame_key"] != origin.frame_key or row["receipt_id"] != f"whole-{row['object_id']}":
            raise AuditError("ordinary receipt identity differs from its fixed archive binding")
        position = row["peak_position"]
        exceeded = row["exceeded"]
        if type(position) is not int or position < 1 or type(exceeded) is not int or exceeded not in (0, 1):
            raise AuditError("ordinary receipt contains a malformed peak result")
        try:
            result = PeakResult(
                frame_key=origin.frame_key,
                unit=settings.unit,
                value=int(row["peak_value"], 16),
                position=position,
                limit=settings.peak_limit,
                difference=int(row["difference"], 16),
                exceeded=bool(exceeded),
            )
        except (TypeError, ValueError) as exc:
            raise AuditError("ordinary receipt contains malformed integer measurements") from exc
        return WholeReceipt(
            row["receipt_id"], row["object_id"], row["volume_id"], origin,
            settings, row["digest"], result,
        )

    def by_frame(self, frame_key: str, *, db: Connection | None = None) -> WholeReceipt | None:
        require_text(frame_key, "frame key")
        if db is None:
            with self._store.read_transaction() as reader:
                return self.by_frame(frame_key, db=reader)
        row = db.execute("SELECT * FROM whole_receipts WHERE frame_key = ?", (frame_key,)).fetchone()
        return self._decode(row)

    def get_committed(self, receipt_id: str) -> WholeReceipt | None:
        if not isinstance(receipt_id, str) or _RECEIPT_ID.fullmatch(receipt_id) is None:
            return None
        with self._store.read_transaction() as db:
            row = db.execute("SELECT * FROM whole_receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
            return self._decode(row)
