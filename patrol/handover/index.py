from dataclasses import dataclass
from sqlite3 import Connection

from patrol import CloseoutDenied, require_text
from patrol.archive.transactions import ArchiveStore


@dataclass(frozen=True, slots=True)
class ShiftManifest:
    run_id: str
    receipt_id: str


class ShiftIndex:
    def __init__(self, store: ArchiveStore) -> None:
        self._store = store

    @staticmethod
    def initialize(db: Connection) -> None:
        db.execute("""
            CREATE TABLE IF NOT EXISTS shift_manifests (
                run_id TEXT PRIMARY KEY,
                receipt_id TEXT NOT NULL UNIQUE REFERENCES whole_receipts(receipt_id)
            )
        """)

    def by_run(self, run_id: str, *, db: Connection | None = None) -> ShiftManifest | None:
        require_text(run_id, "run identifier")
        if db is None:
            with self._store.read_transaction() as reader:
                return self.by_run(run_id, db=reader)
        row = db.execute(
            "SELECT run_id, receipt_id FROM shift_manifests WHERE run_id = ?", (run_id,),
        ).fetchone()
        return ShiftManifest(row["run_id"], row["receipt_id"]) if row is not None else None

    def fix(self, run_id: str, receipt_id: str) -> ShiftManifest:
        with self._store.write_transaction() as db:
            existing = self.by_run(run_id, db=db)
            if existing is not None:
                if existing.receipt_id != receipt_id:
                    raise CloseoutDenied("run already has a different fixed shift manifest")
                return existing
            db.execute("INSERT INTO shift_manifests(run_id, receipt_id) VALUES (?, ?)", (run_id, receipt_id))
            return ShiftManifest(run_id, receipt_id)
